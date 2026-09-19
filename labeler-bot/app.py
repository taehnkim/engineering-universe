#!/usr/bin/env python3
"""Serve a read-only UI for reviewing Jev bot labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response

from eng_universe.extraction.contract import FIELDS
from eng_universe.extraction.dom import annotation_html, parse_html
from eng_universe.extraction.manifest import DatasetManifest


SHELL = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Jev label review</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#11182b;--line:#26334d;--muted:#91a0ba;--accent:#7c9cff}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#eef2ff;font:14px system-ui}header{height:58px;display:flex;gap:10px;align-items:center;padding:10px 14px;border-bottom:1px solid var(--line);background:#0e1528}select,button{background:#17213a;color:#eef2ff;border:1px solid #354362;border-radius:7px;padding:8px 10px}header a{color:#9eb4ff;margin-left:auto}main{display:grid;grid-template-columns:minmax(0,1fr) 370px;height:calc(100vh - 58px)}iframe{width:100%;height:100%;border:0;background:white}aside{overflow:auto;padding:14px;background:var(--panel);border-left:1px solid var(--line)}.meta{color:var(--muted);line-height:1.55}.pill{padding:3px 7px;border:1px solid var(--line);border-radius:999px}.field{width:100%;text-align:left;margin:9px 0;padding:11px;background:#151f36;border:1px solid var(--line);border-radius:9px;color:inherit;cursor:pointer}.field.active{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}.field strong{font-size:15px}.confidence{float:right;color:#a7f3d0}.node{display:block;color:#aebbd1;font:12px ui-monospace;margin-top:5px}.preview{white-space:pre-wrap;background:#080d19;border:1px solid var(--line);padding:10px;border-radius:8px;max-height:230px;overflow:auto;line-height:1.45}.error{color:#fca5a5}details{margin-top:14px}code{color:#c4b5fd}
</style></head><body>
<header><select id="pages"></select><span id="split" class="pill"></span><span id="status"></span><a id="source" target="_blank">source URL ↗</a></header>
<main><iframe id="page" sandbox="allow-same-origin"></iframe><aside><h2>Jev labels</h2><div id="meta" class="meta"></div><div id="fields"></div><h3>Selected content</h3><div id="preview" class="preview"></div><details><summary>Prepared input sent to Jev</summary><p><a id="prepared" target="_blank">open prepared HTML</a></p></details></aside></main>
<script>
const names=['article','title','author','date'];let payload=null,active='article';const $=id=>document.getElementById(id);const esc=s=>(s??'').toString().replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function confidence(name){return payload.metadata?.[name]?.confidence}
function draw(){const labels=payload.labels||{};$('fields').innerHTML=names.map(name=>{const value=labels[name],conf=confidence(name),has=payload.metadata?.[name]!=null,label=has?(value==null?'missing':'node '+value):'not run';return `<button class="field ${active===name?'active':''}" data-field="${name}"><strong>${name}</strong><span class="confidence">${conf==null?'—':(conf*100).toFixed(1)+'%'}</span><span class="node">${label}</span></button>`}).join('');document.querySelectorAll('[data-field]').forEach(button=>button.onclick=()=>{active=button.dataset.field;draw();highlight()})}
function wire(){const doc=$('page').contentDocument;doc.querySelectorAll('[data-eu-node-id]').forEach(el=>{el.addEventListener('mouseenter',()=>el.style.outline='2px dashed #f59e0b');el.addEventListener('mouseleave',()=>{if(el.dataset.active!=='true')el.style.outline=''})});highlight()}
function highlight(){const doc=$('page').contentDocument;if(!doc)return;doc.querySelectorAll('[data-active]').forEach(el=>{delete el.dataset.active;el.style.outline=''});const id=payload.labels?.[active],has=payload.metadata?.[active]!=null,el=id==null?null:doc.querySelector(`[data-eu-node-id="${id}"]`);if(el){el.dataset.active='true';el.style.outline='4px solid #7c3aed';el.scrollIntoView({block:'center'});$('preview').textContent=el.innerText.slice(0,5000)}else{$('preview').textContent=has&&id===null?'Jev marked this field as missing.':'No bot label is available.'}}
async function load(id){payload=await fetch(`/api/pages/${id}`).then(r=>r.json());active='article';$('split').textContent=payload.split;$('status').textContent=payload.status;$('source').href=payload.url;$('prepared').href=`/api/pages/${id}/prepared`;$('meta').innerHTML=`<div><code>${esc(payload.page_id)}</code></div><div>${esc(payload.website)} · ${esc(payload.model||'not run')}</div><div>${payload.preparation?payload.preparation.candidate_count+' candidates · '+payload.preparation.prepared_char_count.toLocaleString()+' prepared characters':'No Jev result yet'}</div>${payload.error?`<p class="error">${esc(payload.error)}</p>`:''}`;$('page').srcdoc=payload.document_html;$('page').onload=wire;draw()}
async function start(){const pages=await fetch('/api/pages').then(r=>r.json());$('pages').innerHTML=pages.map(p=>`<option value="${esc(p.page_id)}">${esc(p.page_id)} · ${esc(p.status)}</option>`).join('');$('pages').onchange=()=>load($('pages').value);if(pages.length)load(pages[0].page_id);else $('status').textContent='Run labeler-bot/run.py first.'}start();
</script></body></html>"""


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def create_app(dataset_dir: Path, output_dir: Path) -> FastAPI:
    app = FastAPI(title="Jev label review")
    manifest = DatasetManifest.load(dataset_dir / "manifest.json")
    records = {record.page_id: record for record in manifest.pages}

    def run_data() -> dict[str, Any]:
        path = output_dir / "run.json"
        return _read_json(path) if path.exists() else {"model": None, "pages": []}

    @app.get("/", response_class=HTMLResponse)
    async def shell() -> str:
        return SHELL

    @app.get("/api/pages")
    async def list_pages() -> list[dict[str, Any]]:
        return run_data()["pages"]

    @app.get("/api/pages/{page_id}")
    async def get_page(page_id: str) -> dict[str, Any]:
        run = run_data()
        entry = next((item for item in run["pages"] if item["page_id"] == page_id), None)
        record = records.get(page_id)
        if entry is None or record is None:
            raise HTTPException(404, "unknown page")
        html = (dataset_dir / record.html_path).read_text(encoding="utf-8")
        annotation_path = output_dir / "annotations" / f"{page_id}.json"
        annotation = _read_json(annotation_path) if annotation_path.exists() else {}
        return {
            **entry,
            "model": annotation.get("model", run.get("model")),
            "document_html": annotation_html(parse_html(html)),
            "labels": annotation.get(
                "labels", {field.value: None for field in FIELDS}
            ),
            "metadata": annotation.get("metadata", {}),
            "preparation": annotation.get("preparation")
            or {
                "candidate_count": entry.get("candidate_count"),
                "prepared_char_count": entry.get("prepared_char_count"),
            },
        }

    @app.get("/api/pages/{page_id}/prepared")
    async def prepared(page_id: str) -> Response:
        if page_id not in records:
            raise HTTPException(404, "unknown page")
        path = output_dir / "prepared" / f"{page_id}.html"
        if not path.exists():
            raise HTTPException(404, "prepared input does not exist")
        return Response(path.read_text(encoding="utf-8"), media_type="text/plain")

    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=ROOT / "data/learned_extraction/raw",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "data",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    import uvicorn

    args = build_parser().parse_args(argv)
    uvicorn.run(create_app(args.dataset_dir, args.output_dir), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
