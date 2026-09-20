"""Local, script-disabled annotation UI for DOM selection labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

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
body{margin:0;font:14px system-ui;background:#111827;color:#e5e7eb}header{display:flex;gap:8px;padding:10px;background:#1f2937;align-items:center;position:sticky;top:0;z-index:3}button,select{padding:7px;border-radius:5px;border:1px solid #4b5563;background:#111827;color:#e5e7eb}.active{background:#2563eb}main{display:grid;grid-template-columns:1fr 360px;height:calc(100vh - 55px)}iframe{width:100%;height:100%;border:0;background:white}aside{padding:12px;overflow:auto}.field{margin:10px 0;padding:9px;border:1px solid #374151;border-radius:6px}.field.changed{border-color:#f59e0b}.value{font-family:ui-monospace;word-break:break-all}.confidence{float:right;color:#a7f3d0}.jev{color:#aebbd1;font-size:12px;margin-top:6px}.jev-summary{padding:8px;border:1px solid #4c1d95;background:#1e1b4b;border-radius:6px;line-height:1.5}.preview{white-space:pre-wrap;max-height:160px;overflow:auto;background:#030712;padding:8px}.review{color:#fbbf24}
</style></head><body>
<header><select id="split"><option value="all">All splits</option><option value="train">Train</option><option value="validation">Validation</option><option value="test">Test</option></select><select id="source"><option value="all">All labels</option><option value="jev">Jev first pass</option><option value="human">No Jev pass</option></select><select id="pages"></select><button id="parent">Select parent</button><button id="save">Save</button><span id="status"></span></header>
<main><iframe id="page" sandbox="allow-same-origin"></iframe><aside><h2>Selections</h2><div id="jev"></div><p>Choose a field, then click the tightest correct wrapper. Hover highlights candidates.</p><div id="fields"></div><label><input id="review" type="checkbox"> Page needs review</label><h3>Preview</h3><div id="preview" class="preview"></div></aside></main>
<script>
const names=['article','title','authors','date','summary','relative_date']; let active='article', current=null, payload=null, labels={},allPages=[];
const $=id=>document.getElementById(id); const esc=s=>(s??'').toString().replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function selectedElement(){const d=$('page').contentDocument,id=labels[active];return !d||id==null?null:d.querySelector(`[data-eu-node-id="${id}"]`)}
function focusSelection(scroll=false){const d=$('page').contentDocument;if(!d)return;d.querySelectorAll('[data-labeler-selected]').forEach(el=>delete el.dataset.labelerSelected);const el=selectedElement();current=el;if(!el)return;el.dataset.labelerSelected='true';if(scroll)el.scrollIntoView({behavior:'smooth',block:'center',inline:'nearest'})}
function activateField(name,scroll=true){active=name;draw();focusSelection(scroll);preview()}
function draw(){const bot=payload?.jev;$('fields').innerHTML=names.map(n=>{const suggested=bot?.labels?.[n],confidence=bot?.metadata?.[n]?.confidence,changed=bot&&labels[n]!==suggested;return `<div class="field ${changed?'changed':''}"><button data-field="${n}" class="${active===n?'active':''}" aria-pressed="${active===n}">${n}</button> <button data-missing="${n}">missing</button>${bot?`<button data-use-jev="${n}">use Jev</button><span class="confidence">${(confidence*100).toFixed(1)}%</span>`:''}<div class="value">current: ${labels[n]===null?'null':labels[n]??'unlabeled'}</div>${bot?`<div class="jev">Jev: ${suggested===null?'missing':'node '+suggested}${changed?' · edited':''}</div>`:''}</div>`}).join('');document.querySelectorAll('[data-field]').forEach(b=>b.onclick=()=>activateField(b.dataset.field));document.querySelectorAll('[data-missing]').forEach(b=>b.onclick=()=>{active=b.dataset.missing;labels[active]=null;draw();focusSelection();preview()});document.querySelectorAll('[data-use-jev]').forEach(b=>b.onclick=()=>{active=b.dataset.useJev;labels[active]=payload.jev.labels[active];draw();focusSelection(true);preview()})}
function preview(){const id=labels[active],el=selectedElement();$('preview').textContent=el?el.innerText.slice(0,4000):(id===null?'Missing':'No selection')}
function wire(){const d=$('page').contentDocument,style=d.createElement('style');style.textContent='[data-labeler-selected="true"]{outline:4px solid #2563eb!important;outline-offset:3px!important;background-color:rgba(37,99,235,.08)!important}';d.head.appendChild(style);d.querySelectorAll('[data-eu-node-id]').forEach(el=>{el.addEventListener('mouseenter',e=>{e.stopPropagation();if(!el.dataset.labelerSelected)el.style.outline='3px solid #f59e0b'});el.addEventListener('mouseleave',e=>{e.stopPropagation();if(!el.dataset.labelerSelected)el.style.outline=''});el.addEventListener('click',e=>{e.preventDefault();e.stopPropagation();current=el;labels[active]=Number(el.dataset.euNodeId);draw();focusSelection();preview()})});focusSelection(true);preview()}
async function load(id){payload=await fetch(`/api/pages/${id}`).then(r=>r.json());labels={...payload.labels};current=null;$('review').checked=payload.needs_review;const frame=$('page');frame.onload=wire;frame.srcdoc=payload.document_html;$('jev').innerHTML=payload.jev?`<div class="jev-summary"><strong>Jev first pass</strong><br>${esc(payload.jev.model)} · ${payload.jev.latency_ms.toFixed(1)} ms</div>`:'';draw();$('preview').textContent='Loading selection…';$('status').textContent=`${payload.review_status} · ${payload.url}`}
function filterPages(){const split=$('split').value,source=$('source').value,pages=allPages.filter(p=>(split==='all'||p.split===split)&&(source==='all'||(source==='jev')===p.jev_labeled));const previous=$('pages').value,mark={reviewed:'✓ ',draft:'◐ ',unlabeled:'○ '};$('pages').innerHTML=pages.map(p=>`<option value="${esc(p.page_id)}">${p.jev_labeled?'◆ ':''}${mark[p.review_status]}${esc(p.page_id)} [${p.split}]</option>`).join('');if(pages.length){$('pages').value=pages.some(p=>p.page_id===previous)?previous:pages[0].page_id;load($('pages').value)}else{$('status').textContent='No matching pages.'}}
async function start(){allPages=await fetch('/api/pages').then(r=>r.json());$('pages').onchange=()=>load($('pages').value);$('split').onchange=filterPages;$('source').onchange=filterPages;filterPages()}
$('parent').onclick=()=>{if(!current)return;const p=current.parentElement?.closest('[data-eu-node-id]');if(p){current=p;labels[active]=Number(p.dataset.euNodeId);draw();focusSelection(true);preview()}};
$('save').onclick=async()=>{const body={html_hash:payload.html_hash,labels,needs_review:$('review').checked};const r=await fetch(`/api/pages/${payload.page_id}`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});$('status').textContent=r.ok?'Saved':await r.text()};start();
</script></body></html>"""


def create_app(dataset_dir: Path, jev_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="DOM extraction labeler")
    manifest = DatasetManifest.load(dataset_dir / "manifest.json")
    records = {record.page_id: record for record in manifest.pages}
    annotations_dir = dataset_dir / "annotations"
    jev_dir = jev_dir or dataset_dir / "jev_annotations"

    def jev_result(page_id: str, html_hash: str) -> dict[str, Any] | None:
        path = jev_dir / f"{page_id}.json"
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("page_id") != page_id or value.get("html_hash") != html_hash:
            return None
        if set(value.get("labels", {})) != {field.value for field in FIELDS}:
            return None
        return value

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
                "jev_labeled": jev_result(record.page_id, record.html_hash) is not None,
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
        jev = jev_result(page_id, page.html_hash)
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
            "jev": jev,
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
    parser.add_argument(
        "--jev-dir",
        type=Path,
        help="Jev audit directory. Default: <dataset-dir>/jev_annotations.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    import uvicorn

    args = build_parser().parse_args(argv)
    uvicorn.run(
        create_app(args.dataset_dir, args.jev_dir), host=args.host, port=args.port
    )


if __name__ == "__main__":
    main()
