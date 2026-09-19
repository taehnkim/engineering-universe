"""Local, script-disabled annotation UI for DOM selection labels."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from eng_universe.extraction.contract import FIELDS, Annotation, load_annotation, save_annotation
from eng_universe.extraction.dom import annotation_html, parse_html
from eng_universe.extraction.manifest import DatasetManifest


class AnnotationRequest(BaseModel):
    html_hash: str
    labels: dict[str, int | None]
    needs_review: bool = False


SHELL = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>DOM extraction labeler</title>
<style>
body{margin:0;font:14px system-ui;background:#111827;color:#e5e7eb}header{display:flex;gap:8px;padding:10px;background:#1f2937;align-items:center;position:sticky;top:0;z-index:3}button,select{padding:7px;border-radius:5px;border:1px solid #4b5563;background:#111827;color:#e5e7eb}.active{background:#2563eb}main{display:grid;grid-template-columns:1fr 330px;height:calc(100vh - 55px)}iframe{width:100%;height:100%;border:0;background:white}aside{padding:12px;overflow:auto}.field{margin:10px 0;padding:9px;border:1px solid #374151;border-radius:6px}.value{font-family:ui-monospace;word-break:break-all}.preview{white-space:pre-wrap;max-height:160px;overflow:auto;background:#030712;padding:8px}.review{color:#fbbf24}
</style></head><body>
<header><select id="pages"></select><button id="parent">Select parent</button><button id="save">Save</button><span id="status"></span></header>
<main><iframe id="page" sandbox="allow-same-origin"></iframe><aside><h2>Selections</h2><p>Choose a field, then click the tightest correct wrapper. Hover highlights candidates.</p><div id="fields"></div><label><input id="review" type="checkbox"> Page needs review</label><h3>Preview</h3><div id="preview" class="preview"></div></aside></main>
<script>
const names=['article','title','authors','date','summary','relative_date']; let active='article', current=null, payload=null, labels={};
const $=id=>document.getElementById(id); const esc=s=>(s??'').toString().replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function draw(){ $('fields').innerHTML=names.map(n=>`<div class="field"><button data-field="${n}" class="${active===n?'active':''}">${n}</button> <button data-missing="${n}">missing</button><div class="value">${labels[n]===null?'null':labels[n]??'unlabeled'}</div></div>`).join(''); document.querySelectorAll('[data-field]').forEach(b=>b.onclick=()=>{active=b.dataset.field;draw()}); document.querySelectorAll('[data-missing]').forEach(b=>b.onclick=()=>{labels[b.dataset.missing]=null;draw();preview()})}
function preview(){const d=$('page').contentDocument, id=labels[active], el=id==null?null:d.querySelector(`[data-eu-node-id="${id}"]`); $('preview').textContent=el?el.innerText.slice(0,4000):(id===null?'Missing':'No selection')}
function wire(){const d=$('page').contentDocument; d.querySelectorAll('[data-eu-node-id]').forEach(el=>{el.addEventListener('mouseenter',e=>{e.stopPropagation();el.style.outline='3px solid #f59e0b'});el.addEventListener('mouseleave',e=>{e.stopPropagation();el.style.outline=''});el.addEventListener('click',e=>{e.preventDefault();e.stopPropagation();current=el;labels[active]=Number(el.dataset.euNodeId);draw();preview()})})}
async function load(id){payload=await fetch(`/api/pages/${id}`).then(r=>r.json());labels={...payload.labels};$('review').checked=payload.needs_review;$('page').srcdoc=payload.document_html;$('page').onload=wire;draw();preview();$('status').textContent=`${payload.review_status} · ${payload.url}`}
async function start(){const pages=await fetch('/api/pages').then(r=>r.json());const mark={reviewed:'✓ ',draft:'◐ ',unlabeled:'○ '};$('pages').innerHTML=pages.map(p=>`<option value="${esc(p.page_id)}">${mark[p.review_status]}${esc(p.page_id)} [${p.split}]</option>`).join('');$('pages').onchange=()=>load($('pages').value);if(pages.length)load(pages[0].page_id)}
$('parent').onclick=()=>{if(!current)return;const p=current.parentElement?.closest('[data-eu-node-id]');if(p){current=p;labels[active]=Number(p.dataset.euNodeId);draw();preview()}};
$('save').onclick=async()=>{const body={html_hash:payload.html_hash,labels,needs_review:$('review').checked};const r=await fetch(`/api/pages/${payload.page_id}`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});$('status').textContent=r.ok?'Saved':await r.text()};start();
</script></body></html>"""


def create_app(dataset_dir: Path) -> FastAPI:
    app = FastAPI(title="DOM extraction labeler")
    manifest = DatasetManifest.load(dataset_dir / "manifest.json")
    records = {record.page_id: record for record in manifest.pages}
    annotations_dir = dataset_dir / "annotations"

    @app.get("/", response_class=HTMLResponse)
    async def shell() -> str:
        return SHELL

    @app.get("/api/pages")
    async def list_pages() -> list[dict[str, object]]:
        pages: list[dict[str, object]] = []
        for record in manifest.pages:
            annotation_path = annotations_dir / f"{record.page_id}.json"
            annotation = load_annotation(annotation_path) if annotation_path.exists() else None
            pages.append({
                "page_id": record.page_id,
                "split": record.split,
                "website": record.website,
                "review_status": annotation.review_status if annotation else "unlabeled",
            })
        return pages

    @app.get("/api/pages/{page_id}")
    async def get_page(page_id: str) -> dict[str, object]:
        record = records.get(page_id)
        if record is None:
            raise HTTPException(404, "unknown page")
        page = parse_html((dataset_dir / record.html_path).read_text(encoding="utf-8"))
        annotation_path = annotations_dir / f"{page_id}.json"
        annotation = load_annotation(annotation_path) if annotation_path.exists() else None
        return {
            "page_id": page_id,
            "url": record.url,
            "html_hash": page.html_hash,
            "document_html": annotation_html(page),
            "labels": {
                field.value: annotation.labels[field] if annotation else None
                for field in FIELDS
            },
            "needs_review": annotation.needs_review if annotation else False,
            "review_status": annotation.review_status if annotation else "unlabeled",
        }

    @app.post("/api/pages/{page_id}")
    async def put_page(page_id: str, request: AnnotationRequest) -> dict[str, bool]:
        record = records.get(page_id)
        if record is None:
            raise HTTPException(404, "unknown page")
        page = parse_html((dataset_dir / record.html_path).read_text(encoding="utf-8"))
        if request.html_hash != page.html_hash:
            raise HTTPException(409, "HTML changed; reload before saving")
        if set(request.labels) != {field.value for field in FIELDS}:
            raise HTTPException(422, "all extraction fields are required")
        annotation = Annotation.from_dict(
            {
                "page_id": page_id,
                "html_hash": page.html_hash,
                "labels": request.labels,
                "needs_review": request.needs_review,
                "review_status": "reviewed",
            }
        )
        for field, node_id in annotation.labels.items():
            if node_id is not None and node_id not in page.node_by_id:
                raise HTTPException(422, f"{field.value}: unknown node ID {node_id}")
        save_annotation(annotations_dir / f"{page_id}.json", annotation)
        return {"saved": True}

    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=Path("data/learned_extraction/raw"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    import uvicorn

    args = build_parser().parse_args(argv)
    uvicorn.run(create_app(args.dataset_dir), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
