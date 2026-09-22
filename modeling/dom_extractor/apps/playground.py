"""Local playground for visually inspecting checkpoint DOM predictions."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from eng_universe.extraction.contract import FIELDS, Field, load_annotation
from eng_universe.extraction.dom import annotation_html, parse_html
from eng_universe.extraction.inference import DOMExtractor
from modeling.dom_extractor.manifest import DatasetManifest, PageRecord

DEFAULT_EVALUATION_WORKERS = 10

SHELL = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>DOM inference playground</title>
<style>
body{margin:0;font:14px system-ui;background:#111827;color:#e5e7eb}header{display:flex;gap:8px;padding:10px;background:#1f2937;align-items:center;position:sticky;top:0;z-index:3}button,select{padding:7px;border-radius:5px;border:1px solid #4b5563;background:#111827;color:#e5e7eb}button:disabled{opacity:.55}.run{background:#16a34a;border-color:#22c55e;font-weight:700}.source-link{color:#f8fafc;font-weight:650;text-decoration:underline;text-underline-offset:2px}main{display:grid;grid-template-columns:1fr 390px;height:calc(100vh - 55px)}iframe{width:100%;height:100%;border:0;background:white}aside{padding:12px;overflow:auto}.inference-heading{display:flex;align-items:baseline;justify-content:space-between;gap:10px}.inference-heading h2{margin:8px 0}.latency{color:#a7f3d0;font:12px ui-monospace}.model{padding:9px;border:1px solid #166534;background:#052e16;border-radius:6px;line-height:1.5}.field{margin:8px 0;padding:9px;border:1px solid #374151;border-radius:6px;cursor:pointer}.field:hover{border-color:#64748b}.field:focus-visible{outline:3px solid #fbbf24;outline-offset:2px}.field.active{border-color:#3b82f6;background:#172554}.field.exact{border-color:#166534}.field.different{border-color:#b45309}.field.active.exact{border-color:#3b82f6}.field.active.different{border-color:#3b82f6}.field-header{display:flex;align-items:center;justify-content:space-between;gap:8px}.field-name{font-weight:700}.value{font-family:ui-monospace;word-break:break-all;margin-top:6px}.reference{color:#aebbd1;font-size:12px;margin-top:4px}.badge{border-radius:999px;padding:2px 7px;font-size:11px}.exact .badge{background:#14532d;color:#bbf7d0}.different .badge{background:#78350f;color:#fde68a}.preview{white-space:pre-wrap;max-height:300px;overflow:auto;background:#030712;padding:8px}.muted{color:#94a3b8}.error{color:#fca5a5}
</style></head><body>
<header><a class="source-link" href="/evals">← Evals</a><select id="split"><option value="all">All splits</option><option value="train">Train</option><option value="validation">Validation</option><option value="test">Test</option></select><select id="pages"></select><button id="run" class="run">RUN</button><a id="source" class="source-link" target="_blank" rel="noopener">Open source</a><span id="status"></span></header>
<main><iframe id="page" sandbox="allow-same-origin"></iframe><aside><div class="inference-heading"><h2>Inference</h2><span id="latency" class="latency"></span></div><div id="model" class="model"></div><div id="fields"></div><h3>Predicted content</h3><div id="preview" class="preview">Choose a page and click RUN.</div></aside></main>
<script>
const names=['article','title','authors','date','summary','relative_date'];const requestedPage=new URLSearchParams(location.search).get('page');let active='article',payload=null,result=null,allPages=[];
const $=id=>document.getElementById(id);const esc=s=>(s??'').toString().replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function selectedElement(){const d=$('page').contentDocument,id=result?.predictions?.[active];return !d||id==null?null:d.querySelector(`[data-eu-node-id="${id}"]`)}
function focusPrediction(scroll=false){const d=$('page').contentDocument;if(!d)return;d.querySelectorAll('[data-playground-prediction]').forEach(el=>delete el.dataset.playgroundPrediction);const el=selectedElement();if(!el)return;el.dataset.playgroundPrediction='true';if(scroll)el.scrollIntoView({behavior:'instant',block:'center',inline:'nearest'})}
function preview(){const item=result?.results?.[active];$('preview').textContent=!result?'Click RUN to generate predictions.':item?item.text.slice(0,5000):'Model predicted missing.'}
function activateField(name){active=name;draw();focusPrediction(true);preview()}
function draw(){if(!result){$('fields').innerHTML='<p class="muted">No inference result yet.</p>';return}const reference=result.reference_labels,hasReference=result.reference_source!==null;$('fields').innerHTML=names.map(n=>{const predicted=result.predictions[n],expected=reference?.[n],exact=hasReference&&predicted===expected,status=hasReference?(exact?'exact':'different'):'';return `<div class="field ${status} ${active===n?'active':''}" data-field="${n}" role="button" tabindex="0" aria-pressed="${active===n}"><div class="field-header"><span class="field-name">${n}</span>${hasReference?`<span class="badge">${exact?'exact':'different'}</span>`:''}</div><div class="value">predicted: ${predicted===null?'missing':'node '+predicted}</div>${hasReference?`<div class="reference">${esc(result.reference_source)}: ${expected===null?'missing':'node '+expected}</div>`:''}</div>`}).join('');document.querySelectorAll('[data-field]').forEach(card=>{card.onclick=()=>activateField(card.dataset.field);card.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();activateField(card.dataset.field)}}})}
function wire(){const d=$('page').contentDocument,style=d.createElement('style');style.textContent='[data-playground-prediction="true"]{outline:4px solid #2563eb!important;outline-offset:3px!important;background-color:rgba(37,99,235,.08)!important}';d.head.appendChild(style);focusPrediction(true);preview()}
async function load(id){$('run').disabled=true;result=null;active='article';$('latency').textContent='';payload=await fetch(`/api/pages/${id}`).then(r=>r.json());$('source').href=payload.url;$('status').textContent=`${payload.split} · ${payload.website}`;const frame=$('page');frame.onload=wire;frame.srcdoc=payload.document_html;$('model').innerHTML=`<strong>${esc(payload.checkpoint)}</strong><br><span class="muted">${esc(payload.reference_source||'No reference label')}</span>`;draw();preview();$('run').disabled=false}
async function run(){const button=$('run');button.disabled=true;button.textContent='RUNNING…';$('latency').textContent='Running…';$('status').textContent='Running checkpoint…';try{const response=await fetch(`/api/pages/${payload.page_id}/run`,{method:'POST'});if(!response.ok)throw new Error(await response.text());result=await response.json();active='article';draw();focusPrediction(true);preview();$('latency').textContent=`${result.latency_ms.toFixed(1)} ms`;$('status').textContent=result.reference_source?`${result.matches}/${result.reference_count} exact`:''}catch(error){$('latency').textContent='';$('status').innerHTML=`<span class="error">${esc(error.message)}</span>`}finally{button.disabled=false;button.textContent='RUN'}}
function filterPages(){const split=$('split').value,pages=allPages.filter(page=>split==='all'||page.split===split),previous=$('pages').value||requestedPage;$('pages').innerHTML=pages.map(page=>`<option value="${esc(page.page_id)}">${esc(page.page_id)} [${page.split}]</option>`).join('');if(pages.length){$('pages').value=pages.some(page=>page.page_id===previous)?previous:pages[0].page_id;load($('pages').value)}}
async function start(){allPages=await fetch('/api/pages').then(r=>r.json());$('pages').onchange=()=>load($('pages').value);$('split').onchange=filterPages;$('run').onclick=run;filterPages()}start();
</script></body></html>"""


EVALS_SHELL = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>DOM extractor evaluations</title>
<style>
*{box-sizing:border-box}body{margin:0;font:14px system-ui;background:#0f172a;color:#e5e7eb}header{position:sticky;top:0;z-index:2;padding:16px 20px;background:#1e293b;border-bottom:1px solid #334155}h1{margin:0 0 5px;font-size:22px}.subtitle{color:#94a3b8}.controls{display:flex;align-items:center;gap:8px;margin-top:12px;flex-wrap:wrap}button,select{padding:8px 10px;border-radius:6px;border:1px solid #475569;background:#111827;color:#e5e7eb}button{cursor:pointer;font-weight:650}button:hover{border-color:#94a3b8}button:disabled{opacity:.55;cursor:wait}.run-all{background:#166534;border-color:#22c55e}.status{color:#a7f3d0;margin-left:4px}.progress{display:none;align-items:center;gap:10px;margin-top:11px}.progress.visible{display:flex}.progress-track{width:min(520px,70vw);height:10px;background:#0f172a;border:1px solid #475569;border-radius:999px;overflow:hidden}.progress-bar{width:0;height:100%;background:#22c55e;transition:width .15s linear}.progress-text{color:#cbd5e1;font-variant-numeric:tabular-nums;white-space:nowrap}main{padding:18px 20px 40px;max-width:1500px;margin:auto}.notice{padding:10px 12px;border:1px solid #854d0e;background:#422006;color:#fde68a;border-radius:7px;margin-bottom:14px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}.card{padding:13px;border:1px solid #334155;border-radius:8px}.card h3{margin:0 0 9px;font-size:19px}.card.kpi-green,.metric-cell.kpi-green{background:#052e16;border-color:#16a34a}.card.kpi-orange,.metric-cell.kpi-orange{background:#713f12;border-color:#fbbf24}.card.kpi-red,.metric-cell.kpi-red{background:#450a0a;border-color:#dc2626}.accuracy{font-size:27px;font-weight:750}section{margin-top:22px}table{width:100%;border-collapse:collapse;background:#111827;border:1px solid #334155}th,td{text-align:left;padding:8px 9px;border-bottom:1px solid #263244}th{position:sticky;top:145px;background:#1e293b;color:#cbd5e1;font-size:12px}.sort-button{display:flex;align-items:center;gap:5px;width:100%;padding:0;border:0;background:none;border-radius:0;color:inherit;font:inherit;text-align:inherit}.sort-button:hover{color:#fff}.number .sort-button{justify-content:flex-end}.sort-indicator{width:12px;color:#94a3b8}tbody tr:hover{background:#172554}.site-row{cursor:pointer;font-weight:650}.site-row:focus-visible{outline:2px solid #fbbf24;outline-offset:-2px}.disclosure{display:inline-block;width:18px;color:#93c5fd}.site-page{background:#0b1220;color:#cbd5e1}.site-page:hover{background:#111d35}.site-page-title{position:relative;padding-left:35px;max-width:680px}.site-page-title::before{content:'↳';position:absolute;margin-left:-21px;color:#64748b}.eval-title{font-weight:650}.result-mark{font-size:16px;font-weight:800}.result-mark.pass{color:#4ade80}.result-mark.fail{color:#f87171}.number{text-align:right;font-variant-numeric:tabular-nums}.links{white-space:nowrap}a{color:#93c5fd}.title{max-width:620px}.muted{color:#94a3b8}.empty{padding:30px;text-align:center;color:#94a3b8}
</style></head><body>
<header><h1>DOM extractor evaluations</h1><div class="subtitle">Checkpoint: <span id="checkpoint"></span> · <span id="workers"></span> parallel workers</div><div class="controls"><button id="run-all" class="run-all">RUN ALL</button><select id="site"></select><button id="run-site">RUN SITE</button><span id="status" class="status"></span></div><div id="progress" class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0"><div class="progress-track"><div id="progress-bar" class="progress-bar"></div></div><span id="progress-text" class="progress-text"></span></div></header>
<main><div class="notice">Whole-corpus accuracy includes train and validation pages. Use it to inspect fit and labeling consistency; use the test split for unbiased generalization accuracy.</div><div id="cards" class="cards"></div><section><h2>Accuracy by site</h2><div id="sites" class="empty">Waiting for evaluation…</div></section><section><h2>Pages</h2><div id="pages" class="empty">Waiting for evaluation…</div></section></main>
<script>
const names=['article','title','authors','date','summary','relative_date'];let evalOptions=null,currentResult=null,siteSort={key:'website',direction:'asc'};const expandedSites=new Set();const $=id=>document.getElementById(id);const esc=s=>(s??'').toString().replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const pct=n=>n==null?'—':(n*100).toFixed(1)+'%';
const scoreClass=value=>value>=.8?'green':value>=.5?'orange':'red';const resultMark=exact=>`<span class="result-mark ${exact?'pass':'fail'}" aria-label="${exact?'correct':'incorrect'}" title="${exact?'correct':'incorrect'}">${exact?'✓':'×'}</span>`;
function sortHeader(label,key,numeric=false){const active=siteSort.key===key,direction=active?siteSort.direction:'none',indicator=active?(direction==='asc'?'↑':'↓'):'↕';return `<th class="${numeric?'number':''}" aria-sort="${direction==='asc'?'ascending':direction==='desc'?'descending':'none'}"><button class="sort-button" data-sort="${key}" type="button">${label}<span class="sort-indicator" aria-hidden="true">${indicator}</span></button></th>`}
function renderSites(){const result=currentResult;if(!result)return;const valueFor=(site,key)=>key==='website'?site.website:key==='pages'?site.pages:site.fields[key].accuracy,sortedSites=[...result.sites].sort((left,right)=>{const a=valueFor(left,siteSort.key),b=valueFor(right,siteSort.key),order=typeof a==='string'?a.localeCompare(b):a-b;return siteSort.direction==='asc'?order:-order}),fieldHeaders=names.map(name=>sortHeader(name,name,true)).join(''),pageRows=page=>`<tr class="site-page"><td class="site-page-title" colspan="2"><a href="${esc(page.url)}" target="_blank" rel="noopener">[url]</a> <a class="eval-title" href="/playground?page=${encodeURIComponent(page.page_id)}" target="_blank" rel="noopener">${esc(page.title)}</a></td>${names.map(name=>`<td class="number metric-cell kpi-${scoreClass(page.field_matches[name]?1:0)}">${resultMark(page.field_matches[name])}</td>`).join('')}</tr>`;$('sites').className='';$('sites').innerHTML=`<table><thead><tr>${sortHeader('Site / page','website')}${sortHeader('Pages','pages',true)}${fieldHeaders}</tr></thead><tbody>${sortedSites.map(site=>{const open=expandedSites.has(site.website),children=open?result.pages.filter(page=>page.website===site.website).map(pageRows).join(''):'';return `<tr class="site-row" data-site="${esc(site.website)}" role="button" tabindex="0" aria-expanded="${open}"><td><span class="disclosure" aria-hidden="true">${open?'▾':'▸'}</span>${esc(site.website)}</td><td class="number">${site.pages}</td>${names.map(name=>{const accuracy=site.fields[name].accuracy;return `<td class="number metric-cell kpi-${scoreClass(accuracy)}">${pct(accuracy)}</td>`}).join('')}</tr>${children}`}).join('')}</tbody></table>`;document.querySelectorAll('[data-sort]').forEach(button=>button.onclick=()=>{const key=button.dataset.sort;siteSort=siteSort.key===key?{key,direction:siteSort.direction==='asc'?'desc':'asc'}:{key,direction:key==='website'?'asc':'desc'};renderSites()});document.querySelectorAll('.site-row').forEach(row=>{const toggle=()=>{expandedSites.has(row.dataset.site)?expandedSites.delete(row.dataset.site):expandedSites.add(row.dataset.site);renderSites()};row.onclick=toggle;row.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();toggle()}}})}
function draw(result){currentResult=result;$('checkpoint').textContent=result.checkpoint;$('cards').innerHTML=names.map(name=>{const item=result.fields[name];return `<article class="card kpi-${scoreClass(item.accuracy)}"><h3>${name}</h3><div class="accuracy">${pct(item.accuracy)}</div><div class="muted">${item.correct}/${item.examples} exact</div></article>`}).join('');renderSites();$('pages').className='';$('pages').innerHTML=`<table><thead><tr><th>Title</th><th>Site</th><th>Split</th><th class="number">Exact</th><th>Links</th></tr></thead><tbody>${result.pages.map(page=>`<tr><td class="title">${esc(page.title)}</td><td>${esc(page.website)}</td><td>${esc(page.split)}</td><td class="number">${page.matches}/${page.field_count}</td><td class="links"><a href="${esc(page.url)}" target="_blank" rel="noopener">[url]</a> <a href="/playground?page=${encodeURIComponent(page.page_id)}">[eval]</a></td></tr>`).join('')}</tbody></table>`}
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function showProgress(completed,total,state){const root=$('progress'),safeTotal=Math.max(total,1),percent=Math.min(100,completed/safeTotal*100);root.classList.add('visible');root.setAttribute('aria-valuemax',total);root.setAttribute('aria-valuenow',completed);$('progress-bar').style.width=`${percent}%`;$('progress-text').textContent=state==='queued'?`Queued · 0/${total} pages classified`:`${completed}/${total} pages classified`}
function cacheKey(website){return `dom-extractor-evals:v1:${evalOptions.cache_token}:${website||'all'}`}
function saveCached(website,result){try{localStorage.setItem(cacheKey(website),JSON.stringify(result))}catch(error){console.warn('Could not cache evaluation result',error)}}
function restoreCached(website=null){try{const value=localStorage.getItem(cacheKey(website));if(!value)return false;const result=JSON.parse(value);draw(result);showProgress(result.page_count,result.page_count,'complete');const when=new Date(result.evaluated_at).toLocaleString();$('status').textContent=`Cached ${result.page_count} pages · ${when}`;return true}catch(error){console.warn('Could not restore evaluation result',error);return false}}
async function run(website=null){const buttons=[$('run-all'),$('run-site')];buttons.forEach(button=>button.disabled=true);$('status').textContent=website?`Running ${website}…`:'Running entire corpus…';showProgress(0,website?Number($('site').selectedOptions[0]?.dataset.pages||0):evalOptions.page_count,'queued');try{const query=website?`?website=${encodeURIComponent(website)}`:'';const started=await fetch('/api/evals/jobs'+query,{method:'POST'});if(!started.ok)throw new Error(await started.text());const created=await started.json();let job=created;while(job.status==='queued'||job.status==='running'){showProgress(job.completed,job.total,job.status);await wait(250);const response=await fetch(`/api/evals/jobs/${job.job_id}`);if(!response.ok)throw new Error(await response.text());job=await response.json()}showProgress(job.completed,job.total,job.status);if(job.status==='failed')throw new Error(job.error||'Evaluation failed');draw(job.result);saveCached(website,job.result);$('status').textContent=`${job.result.page_count} pages · ${job.result.latency_ms.toFixed(1)} ms`}catch(error){$('status').textContent=`Error: ${error.message}`}finally{buttons.forEach(button=>button.disabled=false)}}
async function start(){evalOptions=await fetch('/api/evals/options').then(response=>response.json());$('checkpoint').textContent=evalOptions.checkpoint;$('workers').textContent=evalOptions.evaluation_workers;$('site').innerHTML=evalOptions.sites.map(site=>`<option value="${esc(site.website)}" data-pages="${site.pages}">${esc(site.website)} (${site.pages})</option>`).join('');$('run-all').onclick=()=>run();$('run-site').onclick=()=>run($('site').value);if(!restoreCached())run()}start();
</script></body></html>"""


def _reference_source(record: PageRecord, dataset_dir: Path) -> str | None:
    path = dataset_dir / "annotations" / f"{record.page_id}.json"
    if not path.exists():
        return None
    annotation = load_annotation(path)
    if annotation.review_status == "reviewed" and not annotation.needs_review:
        return "human reference"
    jev_path = dataset_dir / "jev_annotations" / f"{record.page_id}.json"
    return (
        "Jev draft"
        if annotation.review_status == "draft" and jev_path.exists()
        else None
    )


def _reviewed_records(
    records: Sequence[PageRecord], dataset_dir: Path
) -> list[PageRecord]:
    reviewed: list[PageRecord] = []
    for record in records:
        path = dataset_dir / "annotations" / f"{record.page_id}.json"
        if not path.exists():
            continue
        annotation = load_annotation(path)
        if annotation.review_status == "reviewed" and not annotation.needs_review:
            reviewed.append(record)
    return reviewed


def _evaluation_cache_token(
    records: Sequence[PageRecord],
    dataset_dir: Path,
    checkpoint: Path,
) -> str:
    checkpoint_stat = checkpoint.stat() if checkpoint.exists() else None
    annotation_mtimes = [
        (dataset_dir / "annotations" / f"{record.page_id}.json").stat().st_mtime_ns
        for record in records
    ]
    identity = ":".join(
        (
            "eval-cache-v2",
            str(checkpoint.resolve()),
            str(checkpoint_stat.st_size if checkpoint_stat else 0),
            str(checkpoint_stat.st_mtime_ns if checkpoint_stat else 0),
            str(len(records)),
            str(max(annotation_mtimes, default=0)),
        )
    )
    return hashlib.sha256(identity.encode()).hexdigest()[:20]


def _metric(counts: dict[str, int]) -> dict[str, int | float | None]:
    examples = counts["examples"]
    return {
        **counts,
        "accuracy": counts["correct"] / examples if examples else None,
    }


ProgressCallback = Callable[[int, int, str], None]
_PROCESS_MODEL: DOMExtractor | None = None


def _initialize_evaluation_process(checkpoint: Path) -> None:
    global _PROCESS_MODEL

    import torch

    torch.set_num_threads(1)
    _PROCESS_MODEL = DOMExtractor(checkpoint)


def _evaluate_record_in_process(
    record: PageRecord,
    dataset_dir: Path,
) -> dict[str, object]:
    if _PROCESS_MODEL is None:
        raise RuntimeError("evaluation worker model is not initialized")
    return _evaluate_record(record, dataset_dir, _PROCESS_MODEL)


def _evaluate_record(
    record: PageRecord,
    dataset_dir: Path,
    model: DOMExtractor | Any,
) -> dict[str, object]:
    html = (dataset_dir / record.html_path).read_text(encoding="utf-8")
    page = parse_html(html, strip_chrome=True)
    annotation = load_annotation(dataset_dir / "annotations" / f"{record.page_id}.json")
    inference_started = time.perf_counter()
    predict_page = getattr(model, "predict_page", None)
    predicted = (
        predict_page(page) if callable(predict_page) else model.predict_ids(html)
    )
    inference_ms = (time.perf_counter() - inference_started) * 1_000
    matches = 0
    fields: dict[str, dict[str, int | bool | None]] = {}
    for field in FIELDS:
        expected = annotation.labels[field]
        actual = predicted[field]
        exact = actual == expected
        matches += int(exact)
        fields[field.value] = {
            "expected": expected,
            "predicted": actual,
            "exact": exact,
        }
    title_id = annotation.labels[Field.TITLE]
    title = (
        page.candidate(title_id).get_text(" ", strip=True)
        if title_id is not None
        else record.page_id
    )
    return {
        "page_id": record.page_id,
        "title": title[:300] or record.page_id,
        "url": record.url,
        "website": record.website,
        "split": record.split,
        "matches": matches,
        "field_count": len(FIELDS),
        "latency_ms": inference_ms,
        "fields": fields,
    }


def _evaluate_records(
    records: Sequence[PageRecord],
    dataset_dir: Path,
    checkpoint: Path,
    model: DOMExtractor | Any,
    *,
    max_workers: int = DEFAULT_EVALUATION_WORKERS,
    progress: ProgressCallback | None = None,
    use_processes: bool = False,
) -> dict[str, object]:
    started = time.perf_counter()
    field_counts = {field: {"correct": 0, "examples": 0} for field in FIELDS}
    site_pages: dict[str, int] = defaultdict(int)
    site_field_counts: dict[str, dict[Field, dict[str, int]]] = defaultdict(
        lambda: {field: {"correct": 0, "examples": 0} for field in FIELDS}
    )
    indexed_rows: list[tuple[int, dict[str, object]]] = []
    total = len(records)
    if total:
        worker_count = min(max_workers, total)
        executor_type = ProcessPoolExecutor if use_processes else ThreadPoolExecutor
        executor_kwargs = (
            {"initializer": _initialize_evaluation_process, "initargs": (checkpoint,)}
            if use_processes
            else {}
        )
        with executor_type(max_workers=worker_count, **executor_kwargs) as executor:
            pending = {
                (
                    executor.submit(_evaluate_record_in_process, record, dataset_dir)
                    if use_processes
                    else executor.submit(_evaluate_record, record, dataset_dir, model)
                ): (index, record)
                for index, record in enumerate(records)
            }
            for completed, future in enumerate(as_completed(pending), start=1):
                index, record = pending[future]
                indexed_rows.append((index, future.result()))
                if progress is not None:
                    progress(completed, total, record.page_id)
    page_rows = [row for _, row in sorted(indexed_rows)]
    for row in page_rows:
        fields = row["fields"]
        website = str(row["website"])
        site_pages[website] += 1
        for field in FIELDS:
            field_result = fields[field.value]
            exact = int(field_result["exact"])
            field_counts[field]["examples"] += 1
            field_counts[field]["correct"] += exact
            site_field_counts[website][field]["examples"] += 1
            site_field_counts[website][field]["correct"] += exact
    sites = [
        {
            "website": website,
            "pages": site_pages[website],
            "fields": {field.value: _metric(counts[field]) for field in FIELDS},
        }
        for website, counts in sorted(site_field_counts.items())
    ]
    return {
        "checkpoint": str(checkpoint),
        "scope": "all human-reviewed pages across train, validation, and test",
        "evaluated_at": datetime.now(UTC).isoformat(),
        "page_count": len(page_rows),
        "latency_ms": (time.perf_counter() - started) * 1_000,
        "fields": {field.value: _metric(field_counts[field]) for field in FIELDS},
        "sites": sites,
        "pages": [
            {
                **{key: value for key, value in row.items() if key != "fields"},
                "field_matches": {
                    field.value: bool(row["fields"][field.value]["exact"])
                    for field in FIELDS
                },
            }
            for row in page_rows
        ],
    }


def create_app(
    dataset_dir: Path,
    checkpoint: Path,
    *,
    extractor: DOMExtractor | Any | None = None,
    evaluation_workers: int = DEFAULT_EVALUATION_WORKERS,
) -> FastAPI:
    if evaluation_workers < 1:
        raise ValueError("evaluation_workers must be at least 1")
    app = FastAPI(title="DOM inference playground")
    manifest = DatasetManifest.load(dataset_dir / "manifest.json")
    records = {record.page_id: record for record in manifest.pages}
    model = extractor or DOMExtractor(checkpoint)
    evaluation_lock = asyncio.Lock()
    evaluation_jobs: dict[str, dict[str, object]] = {}
    evaluation_jobs_lock = threading.Lock()
    evaluation_tasks: set[asyncio.Task[None]] = set()

    def reviewed_for(website: str | None) -> list[PageRecord]:
        reviewed = _reviewed_records(manifest.pages, dataset_dir)
        if website is not None:
            reviewed = [record for record in reviewed if record.website == website]
            if not reviewed:
                raise HTTPException(404, "unknown site or no human-reviewed pages")
        return reviewed

    def update_job(job_id: str, **changes: object) -> None:
        with evaluation_jobs_lock:
            evaluation_jobs[job_id].update(changes)

    async def execute_evaluation_job(
        job_id: str,
        reviewed: Sequence[PageRecord],
    ) -> None:
        try:
            async with evaluation_lock:
                started = time.perf_counter()
                update_job(job_id, status="running", started_at=time.time())

                def report_progress(completed: int, total: int, page_id: str) -> None:
                    update_job(
                        job_id,
                        completed=completed,
                        total=total,
                        current_page=page_id,
                        elapsed_ms=(time.perf_counter() - started) * 1_000,
                    )

                result = await asyncio.to_thread(
                    _evaluate_records,
                    reviewed,
                    dataset_dir,
                    checkpoint,
                    model,
                    max_workers=evaluation_workers,
                    progress=report_progress,
                    use_processes=isinstance(model, DOMExtractor),
                )
                update_job(
                    job_id,
                    status="complete",
                    completed=len(reviewed),
                    elapsed_ms=result["latency_ms"],
                    result=result,
                )
        except Exception as error:  # noqa: BLE001 - report background failures
            update_job(job_id, status="failed", error=str(error))

    @app.get("/", response_class=HTMLResponse)
    async def shell() -> str:
        return SHELL

    @app.get("/playground", response_class=HTMLResponse)
    async def playground_shell() -> str:
        return SHELL

    @app.get("/evals", response_class=HTMLResponse)
    async def evals_shell() -> str:
        return EVALS_SHELL

    @app.get("/api/evals/options")
    async def evaluation_options() -> dict[str, object]:
        reviewed = _reviewed_records(manifest.pages, dataset_dir)
        site_pages: dict[str, int] = defaultdict(int)
        for record in reviewed:
            site_pages[record.website] += 1
        return {
            "checkpoint": str(checkpoint),
            "page_count": len(reviewed),
            "evaluation_workers": evaluation_workers,
            "cache_token": _evaluation_cache_token(reviewed, dataset_dir, checkpoint),
            "sites": [
                {"website": website, "pages": pages}
                for website, pages in sorted(site_pages.items())
            ],
        }

    @app.post("/api/evals/run")
    async def run_evaluation(website: str | None = None) -> dict[str, object]:
        reviewed = reviewed_for(website)
        async with evaluation_lock:
            return await asyncio.to_thread(
                _evaluate_records,
                reviewed,
                dataset_dir,
                checkpoint,
                model,
                max_workers=evaluation_workers,
                use_processes=isinstance(model, DOMExtractor),
            )

    @app.post("/api/evals/jobs", status_code=202)
    async def start_evaluation_job(website: str | None = None) -> dict[str, object]:
        reviewed = reviewed_for(website)
        job_id = uuid4().hex
        job: dict[str, object] = {
            "job_id": job_id,
            "status": "queued",
            "completed": 0,
            "total": len(reviewed),
            "current_page": None,
            "elapsed_ms": 0.0,
            "result": None,
            "error": None,
        }
        with evaluation_jobs_lock:
            evaluation_jobs[job_id] = job
        task = asyncio.create_task(execute_evaluation_job(job_id, reviewed))
        evaluation_tasks.add(task)
        task.add_done_callback(evaluation_tasks.discard)
        return dict(job)

    @app.get("/api/evals/jobs/{job_id}")
    async def get_evaluation_job(job_id: str) -> dict[str, object]:
        with evaluation_jobs_lock:
            job = evaluation_jobs.get(job_id)
            if job is None:
                raise HTTPException(404, "unknown evaluation job")
            return dict(job)

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
        page = parse_html(html, strip_chrome=True)
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
    parser.add_argument(
        "--eval-workers",
        type=int,
        default=DEFAULT_EVALUATION_WORKERS,
        help=(
            "Parallel workers for whole-corpus evaluation. "
            f"Default: {DEFAULT_EVALUATION_WORKERS}."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    import uvicorn

    args = build_parser().parse_args(argv)
    uvicorn.run(
        create_app(
            args.dataset_dir,
            args.checkpoint,
            evaluation_workers=args.eval_workers,
        ),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
