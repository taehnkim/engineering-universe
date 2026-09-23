"""Local, script-disabled annotation UI for DOM selection labels."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from eng_universe.extraction.contract import (
    FIELDS,
    Annotation,
    Field,
    load_annotation,
    save_annotation,
)
from eng_universe.extraction.dom import annotation_html, parse_html
from modeling.dom_extractor.labeler_bot import LabelerBot, save_json
from modeling.dom_extractor.manifest import DatasetManifest


class AnnotationRequest(BaseModel):
    html_hash: str
    labels: dict[str, int | None]
    needs_review: bool = False


SHELL = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>DOM extraction labeler</title>
<style>
body{margin:0;height:100vh;overflow:hidden;display:flex;flex-direction:column;font:14px system-ui;background:#111827;color:#e5e7eb}header{display:flex;flex-direction:column;gap:7px;padding:10px;background:#1f2937;position:sticky;top:0;z-index:3}.toolbar{display:flex;gap:8px;align-items:center;min-width:0}.toolbar #pages{flex:1;min-width:180px}button,select{padding:7px;border-radius:5px;border:1px solid #4b5563;background:#111827;color:#e5e7eb}.active{background:#2563eb}.danger{border-color:#991b1b;color:#fecaca;margin-left:auto;white-space:nowrap}.danger:hover{background:#7f1d1d}.url-row{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#aebbd1;font-size:12px}main{display:grid;grid-template-columns:1fr 360px;flex:1;min-height:0}iframe{width:100%;height:100%;border:0;background:white}aside{padding:8px 10px;overflow:auto}.save-review{width:100%;margin:0 0 6px;background:#166534;border-color:#22c55e;font-weight:750;letter-spacing:.04em}.save-review:hover{background:#15803d}.field{margin:6px 0;padding:7px;border:1px solid #374151;border-radius:6px;cursor:pointer}.field:hover{border-color:#64748b}.field:focus-visible,.save-review:focus-visible{outline:3px solid #fbbf24;outline-offset:2px}.field.active{border-color:#3b82f6;background:#172554}.field.changed{border-color:#f59e0b}.field-header{display:flex;align-items:center;justify-content:space-between;gap:6px}.field-name{font-weight:650}.field-row{display:flex;align-items:center;gap:5px;margin-top:5px;min-width:0;flex-wrap:wrap}.field-row button{padding:3px 6px;font-size:12px;transition:background .12s,border-color .12s,color .12s}.field-row [data-missing]:hover{background:#7f1d1d;border-color:#ef4444;color:#fee2e2}.field-row [data-rerun-jev]:hover{background:#4c1d95;border-color:#8b5cf6;color:#ede9fe}.field-row [data-rerun-jev].action-confirmed{background:#14532d;border-color:#22c55e;color:#dcfce7}.meta-chip{padding:3px 6px;border-radius:5px;background:#030712;color:#cbd5e1;font:11px ui-monospace;white-space:nowrap}.confidence{color:#a7f3d0;font-size:12px;white-space:nowrap}.jev{color:#aebbd1}.jev-summary{padding:7px;margin-bottom:6px;border:1px solid #4c1d95;background:#1e1b4b;border-radius:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.review-card{display:flex;align-items:center;gap:7px;color:#fbbf24}.review-card:has(input:checked){border-color:#f59e0b;background:#451a03}.review-card input{margin:0}.preview{white-space:pre-wrap;max-height:160px;overflow:auto;background:#030712;padding:8px}
</style></head><body>
<header><div class="toolbar"><select id="split"><option value="all">All splits</option><option value="train">Train</option><option value="validation">Validation</option><option value="test">Test</option></select><select id="source"><option value="all">All labels</option><option value="jev">Jev first pass</option><option value="human">No Jev pass</option></select><select id="review-filter"><option value="all">All review states</option><option value="needs">Needs human review</option><option value="verified">Human verified</option></select><select id="pages"></select><button id="parent">Select parent</button><span id="status"></span><button id="remove" class="danger">Remove page</button></div><div id="url" class="url-row"></div></header>
<main><iframe id="page" sandbox="allow-same-origin"></iframe><aside><div id="jev"></div><button id="save" class="save-review" data-keyboard-target="save">SAVE</button><label id="review-card" class="field review-card" data-keyboard-target="review" tabindex="0"><input id="review" type="checkbox" tabindex="-1"><span>Page needs review</span></label><div id="fields"></div><h3>Preview</h3><div id="preview" class="preview"></div></aside></main>
<script>
const names=['article','title','authors','date']; let active='article', current=null, payload=null, labels={},allPages=[];
const $=id=>document.getElementById(id); const esc=s=>(s??'').toString().replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function selectedElement(){const d=$('page').contentDocument,id=labels[active];return !d||id==null?null:d.querySelector(`[data-eu-node-id="${id}"]`)}
function focusSelection(scroll=false){const d=$('page').contentDocument;if(!d)return;d.querySelectorAll('[data-labeler-selected]').forEach(el=>delete el.dataset.labelerSelected);const el=selectedElement();current=el;if(!el)return;el.dataset.labelerSelected='true';if(scroll)el.scrollIntoView({behavior:'instant',block:'center',inline:'nearest'})}
function activateField(name,scroll=true){active=name;draw();focusSelection(scroll);preview()}
function nodeLabel(value){return value===null?'missing':value===undefined?'unlabeled':'node '+value}
function draw(){const bot=payload?.jev;$('fields').innerHTML=names.map(n=>{const suggested=bot?.labels?.[n],metadata=bot?.metadata?.[n],confidence=metadata?.confidence,latency=metadata?.latency_ms,changed=bot&&labels[n]!==suggested,confidenceLabel=confidence==null?'':(confidence*100).toFixed(1)+'%',jevStats=confidence==null?'':`Jev confidence: ${confidenceLabel}${latency==null?'':` · ${latency.toFixed(1)} ms`}`;return `<div class="field ${active===n?'active ':''}${changed?'changed':''}" data-field-card="${n}" data-keyboard-target="${n}" role="button" tabindex="0" aria-pressed="${active===n}"><div class="field-header"><span class="field-name">${n}</span>${jevStats?`<span class="confidence">${jevStats}</span>`:''}</div><div class="field-row"><button data-missing="${n}">missing</button>${bot?`<button data-rerun-jev="${n}" title="Make a new Jev API request for this field; click Save to persist the result">rerun Jev</button>`:''}<span class="meta-chip">current: ${nodeLabel(labels[n])}</span>${bot?`<span class="meta-chip jev">Jev: ${nodeLabel(suggested)}${changed?' · edited':''}</span>`:''}</div></div>`}).join('');document.querySelectorAll('[data-field-card]').forEach(card=>{card.onclick=()=>activateField(card.dataset.fieldCard)});document.querySelectorAll('[data-missing]').forEach(b=>b.onclick=e=>{e.stopPropagation();active=b.dataset.missing;labels[active]=null;draw();focusSelection();preview();$('status').textContent=`Marked ${active} missing (not saved)`});document.querySelectorAll('[data-rerun-jev]').forEach(b=>b.onclick=async e=>{e.stopPropagation();active=b.dataset.rerunJev;const field=active,pageId=payload.page_id;b.disabled=true;b.textContent='Running Jev…';$('status').textContent=`Running Jev for ${field}…`;try{const r=await fetch(`/api/pages/${encodeURIComponent(pageId)}/jev/${encodeURIComponent(field)}`,{method:'POST'});if(!r.ok)throw new Error(await r.text());const result=await r.json();if(!payload||payload.page_id!==pageId)return;payload.jev.labels[field]=result.node_id;payload.jev.metadata[field]=result.metadata;payload.jev.model=result.model;labels[field]=result.node_id;draw();focusSelection(true);preview();const button=document.querySelector(`[data-rerun-jev="${field}"]`);if(button){button.textContent='Jev updated ✓';button.classList.add('action-confirmed')}$('status').textContent=`${field}: ${nodeLabel(result.node_id)} · ${(result.metadata.confidence*100).toFixed(1)}% · ${result.latency_ms.toFixed(1)} ms (not saved)`}catch(error){b.disabled=false;b.textContent='rerun Jev';$('status').textContent=`Jev error: ${error.message}`}})}
function preview(){const id=labels[active],el=selectedElement();$('preview').textContent=el?el.innerText.slice(0,4000):(id===null?'Missing':'No selection')}
function wire(){const d=$('page').contentDocument,style=d.createElement('style');style.textContent='[data-labeler-selected="true"]{outline:4px solid #2563eb!important;outline-offset:3px!important;background-color:rgba(37,99,235,.08)!important}';d.head.appendChild(style);d.querySelectorAll('[data-eu-node-id]').forEach(el=>{el.addEventListener('mouseenter',e=>{e.stopPropagation();if(!el.dataset.labelerSelected)el.style.outline='3px solid #f59e0b'});el.addEventListener('mouseleave',e=>{e.stopPropagation();if(!el.dataset.labelerSelected)el.style.outline=''});el.addEventListener('click',e=>{e.preventDefault();e.stopPropagation();current=el;labels[active]=Number(el.dataset.euNodeId);draw();focusSelection();preview()})});focusSelection(true);preview()}
async function load(id){payload=await fetch(`/api/pages/${id}`).then(r=>r.json());labels={...payload.labels};current=null;$('review').checked=payload.needs_review;const frame=$('page');frame.onload=wire;frame.srcdoc=payload.document_html;$('jev').innerHTML=payload.jev?`<div class="jev-summary"><strong>Cached Jev first pass</strong> · ${esc(payload.jev.model)} · page request ${payload.jev.latency_ms.toFixed(1)} ms</div>`:'';draw();$('preview').textContent='Loading selection…';$('status').textContent=payload.review_status;$('url').textContent=payload.url;$('url').title=payload.url}
function pageLabel(p){const mark={reviewed:'✓ ',draft:'◐ ',unlabeled:'○ '};return `${p.jev_labeled?'◆ ':''}${mark[p.review_status]}${p.page_id} [${p.split}]`}
function needsHumanReview(p){return p.review_status!=='reviewed'||p.needs_review}
function filterPages(){const split=$('split').value,source=$('source').value,review=$('review-filter').value,pages=allPages.filter(p=>(split==='all'||p.split===split)&&(source==='all'||(source==='jev')===p.jev_labeled)&&(review==='all'||(review==='needs')===needsHumanReview(p)));const previous=$('pages').value;$('pages').innerHTML=pages.map(p=>`<option value="${esc(p.page_id)}">${esc(pageLabel(p))}</option>`).join('');if(pages.length){$('pages').value=pages.some(p=>p.page_id===previous)?previous:pages[0].page_id;load($('pages').value)}else{payload=null;$('page').srcdoc='';$('fields').innerHTML='';$('preview').textContent='';$('status').textContent='No matching pages.';$('url').textContent='';$('url').title=''}}
async function start(){allPages=await fetch('/api/pages').then(r=>r.json());$('pages').onchange=()=>load($('pages').value);$('split').onchange=filterPages;$('source').onchange=filterPages;$('review-filter').onchange=filterPages;filterPages()}
$('parent').onclick=()=>{if(!current)return;const p=current.parentElement?.closest('[data-eu-node-id]');if(p){current=p;labels[active]=Number(p.dataset.euNodeId);draw();focusSelection(true);preview()}};
$('save').onclick=async()=>{if(!payload)return;const body={html_hash:payload.html_hash,labels,needs_review:$('review').checked};const r=await fetch(`/api/pages/${payload.page_id}`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});if(!r.ok){$('status').textContent=await r.text();return}const page=allPages.find(p=>p.page_id===payload.page_id);if(page){page.review_status='reviewed';page.needs_review=body.needs_review}payload.review_status='reviewed';payload.needs_review=body.needs_review;if($('review-filter').value!=='all'){filterPages();return}const option=[...$('pages').options].find(o=>o.value===payload.page_id);if(option&&page)option.textContent=pageLabel(page);$('status').textContent='Saved'};
$('remove').onclick=async()=>{if(!payload)return;const id=payload.page_id;if(!confirm(`Move ${id} out of the dataset and into removed/?`))return;const r=await fetch(`/api/pages/${encodeURIComponent(id)}`,{method:'DELETE'});if(!r.ok){$('status').textContent=await r.text();return}allPages=allPages.filter(p=>p.page_id!==id);payload=null;filterPages()};start();
const keyboardOrder=['save','review',...names];document.addEventListener('keydown',e=>{const focused=document.activeElement,keyboardTarget=focused?.matches?.('[data-keyboard-target]')?focused:null;if(e.key==='Tab'){e.preventDefault();const currentIndex=keyboardTarget?keyboardOrder.indexOf(keyboardTarget.dataset.keyboardTarget):-1;const nextIndex=e.shiftKey?(currentIndex<=0?keyboardOrder.length-1:currentIndex-1):(currentIndex+1)%keyboardOrder.length;document.querySelector(`[data-keyboard-target="${keyboardOrder[nextIndex]}"]`)?.focus();return}if((e.key===' '||e.code==='Space')&&keyboardTarget){e.preventDefault();const targetName=keyboardTarget.dataset.keyboardTarget;keyboardTarget.click();requestAnimationFrame(()=>document.querySelector(`[data-keyboard-target="${targetName}"]`)?.focus())}});
</script></body></html>"""


def create_app(
    dataset_dir: Path,
    jev_dir: Path | None = None,
    *,
    jev_bot: LabelerBot | Any | None = None,
) -> FastAPI:
    app = FastAPI(title="DOM extraction labeler")
    manifest_path = dataset_dir / "manifest.json"
    manifest = DatasetManifest.load(manifest_path)
    records = {record.page_id: record for record in manifest.pages}
    annotations_dir = dataset_dir / "annotations"
    jev_dir = jev_dir or dataset_dir / "jev_annotations"
    bot = jev_bot or LabelerBot()

    def jev_result(page_id: str, html_hash: str) -> dict[str, Any] | None:
        path = jev_dir / f"{page_id}.json"
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("page_id") != page_id or value.get("html_hash") != html_hash:
            return None
        if not {field.value for field in FIELDS}.issubset(value.get("labels", {})):
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
                "needs_review": annotation.needs_review if annotation else True,
                "jev_labeled": jev_result(record.page_id, record.html_hash) is not None,
            })
        return pages

    @app.get("/api/pages/{page_id}")
    async def get_page(page_id: str) -> dict[str, object]:
        record = records.get(page_id)
        if record is None:
            raise HTTPException(404, "unknown page")
        page = parse_html(
            (dataset_dir / record.html_path).read_text(encoding="utf-8"),
            strip_chrome=True,
        )
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
        page = parse_html(
            (dataset_dir / record.html_path).read_text(encoding="utf-8"),
            strip_chrome=True,
        )
        if request.html_hash != page.html_hash:
            raise HTTPException(409, "HTML changed; reload before saving")
        if not {field.value for field in FIELDS}.issubset(request.labels):
            raise HTTPException(422, "all extraction fields are required")
        existing_path = annotations_dir / f"{page_id}.json"
        existing = load_annotation(existing_path) if existing_path.exists() else None
        annotation = Annotation.from_dict(
            {
                "page_id": page_id,
                "html_hash": page.html_hash,
                "labels": request.labels,
                "needs_review": request.needs_review,
                "review_status": "reviewed",
            }
        )
        if existing is not None:
            annotation = Annotation(
                page_id=annotation.page_id,
                html_hash=annotation.html_hash,
                labels=annotation.labels,
                needs_review=annotation.needs_review,
                review_status=annotation.review_status,
                legacy_labels=existing.legacy_labels,
            )
        for field, node_id in annotation.labels.items():
            if node_id is not None and node_id not in page.node_by_id:
                raise HTTPException(422, f"{field.value}: unknown node ID {node_id}")
        save_annotation(annotations_dir / f"{page_id}.json", annotation)
        return {"saved": True}

    @app.post("/api/pages/{page_id}/jev/{field_name}")
    async def rerun_jev_field(page_id: str, field_name: str) -> dict[str, object]:
        record = records.get(page_id)
        if record is None:
            raise HTTPException(404, "unknown page")
        try:
            field = Field(field_name)
        except ValueError as exc:
            raise HTTPException(404, "unknown extraction field") from exc
        if jev_bot is None:
            load_dotenv(Path(__file__).resolve().parents[1] / ".env")
            if not os.environ.get("TYPESAFE_API_KEY"):
                raise HTTPException(503, "TYPESAFE_API_KEY is not configured")

        html = (dataset_dir / record.html_path).read_text(encoding="utf-8")
        prepared = await asyncio.to_thread(bot.prepare, html)
        try:
            result = await bot.label_field_prepared_async(prepared, record, field)
        except Exception as exc:
            raise HTTPException(502, f"Jev request failed: {exc}") from exc

        audit = jev_result(page_id, prepared.page.html_hash)
        if audit is None:
            raise HTTPException(409, "page has no current Jev audit to update")
        audit["labels"][field.value] = result["node_id"]
        audit["metadata"][field.value] = result["metadata"]
        audit["model"] = result["model"]
        audit["labeled_at"] = datetime.now(UTC).isoformat()
        reruns = audit.setdefault("field_reruns", {})
        reruns[field.value] = {
            "latency_ms": result["latency_ms"],
            "usage": result["usage"],
            "labeled_at": audit["labeled_at"],
        }
        save_json(jev_dir / f"{page_id}.json", audit)
        return result

    @app.delete("/api/pages/{page_id}")
    async def remove_page(page_id: str) -> dict[str, object]:
        nonlocal manifest
        record = records.get(page_id)
        if record is None:
            raise HTTPException(404, "unknown page")

        archive_dir = dataset_dir / "removed" / page_id
        if archive_dir.exists():
            raise HTTPException(409, f"archive already exists for {page_id}")

        archive_dir.mkdir(parents=True)
        moved: list[tuple[Path, Path]] = []
        sources = (
            (dataset_dir / record.html_path, archive_dir / "page.html"),
            (annotations_dir / f"{page_id}.json", archive_dir / "annotation.json"),
            (jev_dir / f"{page_id}.json", archive_dir / "jev_annotation.json"),
        )
        try:
            for source, destination in sources:
                if source.exists():
                    shutil.move(str(source), destination)
                    moved.append((destination, source))
            (archive_dir / "record.json").write_text(
                json.dumps(
                    {
                        "manifest_version": manifest.version,
                        "removed_at": datetime.now(UTC).isoformat(),
                        "page": asdict(record),
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            updated = DatasetManifest(
                manifest.version,
                tuple(page for page in manifest.pages if page.page_id != page_id),
            )
            updated.save(manifest_path)
        except Exception:
            for destination, source in reversed(moved):
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), source)
            shutil.rmtree(archive_dir, ignore_errors=True)
            raise

        manifest = updated
        records.pop(page_id)
        return {"removed": True, "page_id": page_id, "archive": str(archive_dir)}

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
