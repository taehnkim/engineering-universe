"""Local playground for visually inspecting checkpoint DOM predictions."""

from __future__ import annotations

import argparse
from pathlib import Path
import time
from typing import Any, Sequence

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from eng_universe.extraction.contract import FIELDS, load_annotation
from eng_universe.extraction.dom import annotation_html, parse_html
from eng_universe.extraction.inference import DOMExtractor
from eng_universe.extraction.manifest import DatasetManifest, PageRecord


SHELL = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>DOM inference playground</title>
<style>
body{margin:0;font:14px system-ui;background:#111827;color:#e5e7eb}header{display:flex;gap:8px;padding:10px;background:#1f2937;align-items:center;position:sticky;top:0;z-index:3}button,select{padding:7px;border-radius:5px;border:1px solid #4b5563;background:#111827;color:#e5e7eb}button:disabled{opacity:.55}.run{background:#16a34a;border-color:#22c55e;font-weight:700}.active{background:#2563eb}main{display:grid;grid-template-columns:1fr 390px;height:calc(100vh - 55px)}iframe{width:100%;height:100%;border:0;background:white}aside{padding:12px;overflow:auto}.model{padding:9px;border:1px solid #166534;background:#052e16;border-radius:6px;line-height:1.5}.field{margin:10px 0;padding:9px;border:1px solid #374151;border-radius:6px}.field.exact{border-color:#166534}.field.different{border-color:#b45309}.value{font-family:ui-monospace;word-break:break-all}.reference{color:#aebbd1;font-size:12px;margin-top:6px}.badge{float:right;border-radius:999px;padding:2px 7px;font-size:11px}.exact .badge{background:#14532d;color:#bbf7d0}.different .badge{background:#78350f;color:#fde68a}.preview{white-space:pre-wrap;max-height:300px;overflow:auto;background:#030712;padding:8px}.muted{color:#94a3b8}.error{color:#fca5a5}
</style></head><body>
<header><select id="split"><option value="all">All splits</option><option value="train">Train</option><option value="validation">Validation</option><option value="test">Test</option></select><select id="pages"></select><button id="run" class="run">RUN</button><a id="source" target="_blank" rel="noopener">Open source</a><span id="status"></span></header>
<main><iframe id="page" sandbox="allow-same-origin"></iframe><aside><h2>Inference</h2><div id="model" class="model"></div><p class="muted">Run the checkpoint, then choose a field to scroll to its predicted DOM node. Blue is the model prediction.</p><div id="fields"></div><h3>Predicted content</h3><div id="preview" class="preview">Choose a page and click RUN.</div></aside></main>
<script>
const names=['article','title','authors','date','summary','relative_date'];let active='article',payload=null,result=null,allPages=[];
const $=id=>document.getElementById(id);const esc=s=>(s??'').toString().replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function selectedElement(){const d=$('page').contentDocument,id=result?.predictions?.[active];return !d||id==null?null:d.querySelector(`[data-eu-node-id="${id}"]`)}
function focusPrediction(scroll=false){const d=$('page').contentDocument;if(!d)return;d.querySelectorAll('[data-playground-prediction]').forEach(el=>delete el.dataset.playgroundPrediction);const el=selectedElement();if(!el)return;el.dataset.playgroundPrediction='true';if(scroll)el.scrollIntoView({behavior:'smooth',block:'center',inline:'nearest'})}
function preview(){const item=result?.results?.[active];$('preview').textContent=!result?'Click RUN to generate predictions.':item?item.text.slice(0,5000):'Model predicted missing.'}
function activateField(name){active=name;draw();focusPrediction(true);preview()}
function draw(){if(!result){$('fields').innerHTML='<p class="muted">No inference result yet.</p>';return}const reference=result.reference_labels,hasReference=result.reference_source!==null;$('fields').innerHTML=names.map(n=>{const predicted=result.predictions[n],expected=reference?.[n],exact=hasReference&&predicted===expected,status=hasReference?(exact?'exact':'different'):'';return `<div class="field ${status}"><button data-field="${n}" class="${active===n?'active':''}" aria-pressed="${active===n}">${n}</button>${hasReference?`<span class="badge">${exact?'exact':'different'}</span>`:''}<div class="value">predicted: ${predicted===null?'missing':'node '+predicted}</div>${hasReference?`<div class="reference">${esc(result.reference_source)}: ${expected===null?'missing':'node '+expected}</div>`:''}</div>`}).join('');document.querySelectorAll('[data-field]').forEach(button=>button.onclick=()=>activateField(button.dataset.field))}
function wire(){const d=$('page').contentDocument,style=d.createElement('style');style.textContent='[data-playground-prediction="true"]{outline:4px solid #2563eb!important;outline-offset:3px!important;background-color:rgba(37,99,235,.08)!important}';d.head.appendChild(style);focusPrediction(true);preview()}
async function load(id){$('run').disabled=true;result=null;active='article';payload=await fetch(`/api/pages/${id}`).then(r=>r.json());$('source').href=payload.url;$('status').textContent=`${payload.split} · ${payload.website}`;const frame=$('page');frame.onload=wire;frame.srcdoc=payload.document_html;$('model').innerHTML=`<strong>${esc(payload.checkpoint)}</strong><br><span class="muted">${esc(payload.reference_source||'No reference label')}</span>`;draw();preview();$('run').disabled=false}
async function run(){const button=$('run');button.disabled=true;button.textContent='RUNNING…';$('status').textContent='Running checkpoint…';try{const response=await fetch(`/api/pages/${payload.page_id}/run`,{method:'POST'});if(!response.ok)throw new Error(await response.text());result=await response.json();active='article';draw();focusPrediction(true);preview();$('status').textContent=`${result.latency_ms.toFixed(1)} ms${result.reference_source?` · ${result.matches}/${result.reference_count} exact`:''}`}catch(error){$('status').innerHTML=`<span class="error">${esc(error.message)}</span>`}finally{button.disabled=false;button.textContent='RUN'}}
function filterPages(){const split=$('split').value,pages=allPages.filter(page=>split==='all'||page.split===split),previous=$('pages').value;$('pages').innerHTML=pages.map(page=>`<option value="${esc(page.page_id)}">${esc(page.page_id)} [${page.split}]</option>`).join('');if(pages.length){$('pages').value=pages.some(page=>page.page_id===previous)?previous:pages[0].page_id;load($('pages').value)}}
async function start(){allPages=await fetch('/api/pages').then(r=>r.json());$('pages').onchange=()=>load($('pages').value);$('split').onchange=filterPages;$('run').onclick=run;filterPages()}start();
</script></body></html>"""


def _reference_source(record: PageRecord, dataset_dir: Path) -> str | None:
    path = dataset_dir / "annotations" / f"{record.page_id}.json"
    if not path.exists():
        return None
    annotation = load_annotation(path)
    if annotation.review_status == "reviewed" and not annotation.needs_review:
        return "human reference"
    jev_path = dataset_dir / "jev_annotations" / f"{record.page_id}.json"
    return "Jev draft" if annotation.review_status == "draft" and jev_path.exists() else None


def create_app(
    dataset_dir: Path,
    checkpoint: Path,
    *,
    extractor: DOMExtractor | Any | None = None,
) -> FastAPI:
    app = FastAPI(title="DOM inference playground")
    manifest = DatasetManifest.load(dataset_dir / "manifest.json")
    records = {record.page_id: record for record in manifest.pages}
    model = extractor or DOMExtractor(checkpoint)

    @app.get("/", response_class=HTMLResponse)
    async def shell() -> str:
        return SHELL

    @app.get("/api/pages")
    async def list_pages() -> list[dict[str, object]]:
        return [
            {
                "page_id": record.page_id,
                "split": record.split,
                "website": record.website,
                "is_article": record.is_article,
            }
            for record in manifest.pages
        ]

    @app.get("/api/pages/{page_id}")
    async def get_page(page_id: str) -> dict[str, object]:
        record = records.get(page_id)
        if record is None:
            raise HTTPException(404, "unknown page")
        html = (dataset_dir / record.html_path).read_text(encoding="utf-8")
        source = _reference_source(record, dataset_dir)
        return {
            "page_id": record.page_id,
            "url": record.url,
            "split": record.split,
            "website": record.website,
            "document_html": annotation_html(parse_html(html)),
            "checkpoint": str(checkpoint),
            "reference_source": source,
        }

    @app.post("/api/pages/{page_id}/run")
    async def run_inference(page_id: str) -> dict[str, object]:
        record = records.get(page_id)
        if record is None:
            raise HTTPException(404, "unknown page")
        html = (dataset_dir / record.html_path).read_text(encoding="utf-8")
        page = parse_html(html)
        started = time.perf_counter()
        predicted = model.predict_ids(html)
        latency_ms = (time.perf_counter() - started) * 1_000
        predictions = {field.value: predicted[field] for field in FIELDS}
        results = {
            field.value: (
                None
                if predicted[field] is None
                else {
                    **page.selected_content(int(predicted[field])),
                    "tag": page.candidate(int(predicted[field])).name,
                }
            )
            for field in FIELDS
        }
        annotation_path = dataset_dir / "annotations" / f"{record.page_id}.json"
        source = _reference_source(record, dataset_dir)
        reference_labels = None
        if source is not None and annotation_path.exists():
            annotation = load_annotation(annotation_path)
            reference_labels = {
                field.value: annotation.labels[field] for field in FIELDS
            }
        matches = (
            sum(
                predictions[field.value] == reference_labels[field.value]
                for field in FIELDS
            )
            if reference_labels is not None
            else None
        )
        return {
            "page_id": record.page_id,
            "latency_ms": latency_ms,
            "predictions": predictions,
            "results": results,
            "reference_source": source,
            "reference_labels": reference_labels,
            "matches": matches,
            "reference_count": len(FIELDS) if reference_labels is not None else None,
        }

    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=Path("data/learned_extraction/raw"),
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=Path("data/learned_extraction/model/best.pt"),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    import uvicorn

    args = build_parser().parse_args(argv)
    uvicorn.run(
        create_app(args.dataset_dir, args.checkpoint),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
