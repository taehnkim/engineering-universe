"""Local playground for visually inspecting checkpoint DOM predictions."""

from __future__ import annotations

import argparse
import asyncio
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from eng_universe.extraction.contract import FIELDS, Field, load_annotation
from eng_universe.extraction.dom import annotation_html, parse_html
from eng_universe.extraction.inference import DOMExtractor
from modeling.dom_extractor.evaluation import heuristic_select
from modeling.dom_extractor.manifest import DatasetManifest, PageRecord

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
*{box-sizing:border-box}body{margin:0;font:14px system-ui;background:#0f172a;color:#e5e7eb}header{position:sticky;top:0;z-index:2;padding:16px 20px;background:#1e293b;border-bottom:1px solid #334155}h1{margin:0 0 5px;font-size:22px}.subtitle{color:#94a3b8}.controls{display:flex;align-items:center;gap:8px;margin-top:12px;flex-wrap:wrap}button,select{padding:8px 10px;border-radius:6px;border:1px solid #475569;background:#111827;color:#e5e7eb}button{cursor:pointer;font-weight:650}button:hover{border-color:#94a3b8}button:disabled{opacity:.55;cursor:wait}.run-all{background:#166534;border-color:#22c55e}.status{color:#a7f3d0;margin-left:4px}.progress{display:none;align-items:center;gap:10px;margin-top:11px}.progress.visible{display:flex}.progress-track{width:min(520px,70vw);height:10px;background:#0f172a;border:1px solid #475569;border-radius:999px;overflow:hidden}.progress-bar{width:0;height:100%;background:#22c55e;transition:width .15s linear}.progress-text{color:#cbd5e1;font-variant-numeric:tabular-nums;white-space:nowrap}main{padding:18px 20px 40px;max-width:1500px;margin:auto}.notice{padding:10px 12px;border:1px solid #854d0e;background:#422006;color:#fde68a;border-radius:7px;margin-bottom:14px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}.card{padding:12px;border:1px solid #334155;background:#1e293b;border-radius:8px}.card h3{margin:0 0 8px;font-size:14px}.accuracy{font-size:25px;font-weight:750}.baseline{margin-top:5px;color:#94a3b8}.delta.positive{color:#86efac}.delta.negative{color:#fca5a5}section{margin-top:22px}table{width:100%;border-collapse:collapse;background:#111827;border:1px solid #334155}th,td{text-align:left;padding:8px 9px;border-bottom:1px solid #263244}th{position:sticky;top:145px;background:#1e293b;color:#cbd5e1;font-size:12px}tbody tr:hover{background:#172554}.number{text-align:right;font-variant-numeric:tabular-nums}.links{white-space:nowrap}a{color:#93c5fd}.title{max-width:620px}.muted{color:#94a3b8}.empty{padding:30px;text-align:center;color:#94a3b8}
</style></head><body>
<header><h1>DOM extractor evaluations</h1><div class="subtitle">Checkpoint: <span id="checkpoint"></span> · <span id="workers"></span> parallel workers</div><div class="controls"><button id="run-all" class="run-all">RUN ALL</button><select id="site"></select><button id="run-site">RUN SITE</button><span id="status" class="status"></span></div><div id="progress" class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0"><div class="progress-track"><div id="progress-bar" class="progress-bar"></div></div><span id="progress-text" class="progress-text"></span></div></header>
<main><div class="notice">Whole-corpus accuracy includes train and validation pages. Use it to inspect fit and labeling consistency; use the test split for unbiased generalization accuracy.</div><div id="cards" class="cards"></div><section><h2>Accuracy by site</h2><div id="sites" class="empty">Waiting for evaluation…</div></section><section><h2>Pages</h2><div id="pages" class="empty">Waiting for evaluation…</div></section></main>
<script>
const names=['article','title','authors','date','summary','relative_date'];const $=id=>document.getElementById(id);const esc=s=>(s??'').toString().replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const pct=n=>n==null?'—':(n*100).toFixed(1)+'%';
function delta(model,baseline){const value=model-baseline,style=value>=0?'positive':'negative';return `<span class="delta ${style}">${value>=0?'+':''}${(value*100).toFixed(1)} pp</span>`}
function draw(result){$('checkpoint').textContent=result.checkpoint;$('cards').innerHTML=names.map(name=>{const item=result.fields[name];return `<article class="card"><h3>${name}</h3><div class="accuracy">${pct(item.accuracy)}</div><div class="baseline">baseline ${pct(item.baseline_accuracy)} · ${delta(item.accuracy,item.baseline_accuracy)}</div><div class="muted">${item.correct}/${item.examples} exact</div></article>`}).join('');$('sites').className='';$('sites').innerHTML=`<table><thead><tr><th>Site</th><th class="number">Pages</th><th class="number">Model</th><th class="number">Baseline</th><th class="number">Delta</th></tr></thead><tbody>${result.sites.map(site=>`<tr><td>${esc(site.website)}</td><td class="number">${site.pages}</td><td class="number">${pct(site.accuracy)}</td><td class="number">${pct(site.baseline_accuracy)}</td><td class="number">${delta(site.accuracy,site.baseline_accuracy)}</td></tr>`).join('')}</tbody></table>`;$('pages').className='';$('pages').innerHTML=`<table><thead><tr><th>Title</th><th>Site</th><th>Split</th><th class="number">Model</th><th class="number">Baseline</th><th>Links</th></tr></thead><tbody>${result.pages.map(page=>`<tr><td class="title">${esc(page.title)}</td><td>${esc(page.website)}</td><td>${esc(page.split)}</td><td class="number">${page.matches}/${page.field_count}</td><td class="number">${page.baseline_matches}/${page.field_count}</td><td class="links"><a href="${esc(page.url)}" target="_blank" rel="noopener">[url]</a> <a href="/playground?page=${encodeURIComponent(page.page_id)}">[eval]</a></td></tr>`).join('')}</tbody></table>`}
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function showProgress(completed,total,state){const root=$('progress'),safeTotal=Math.max(total,1),percent=Math.min(100,completed/safeTotal*100);root.classList.add('visible');root.setAttribute('aria-valuemax',total);root.setAttribute('aria-valuenow',completed);$('progress-bar').style.width=`${percent}%`;$('progress-text').textContent=state==='queued'?`Queued · 0/${total} pages classified`:`${completed}/${total} pages classified`}
async function run(website=null){const buttons=[$('run-all'),$('run-site')];buttons.forEach(button=>button.disabled=true);$('status').textContent=website?`Running ${website}…`:'Running entire corpus…';showProgress(0,website?Number($('site').selectedOptions[0]?.dataset.pages||0):window.corpusPages,'queued');try{const query=website?`?website=${encodeURIComponent(website)}`:'';const started=await fetch('/api/evals/jobs'+query,{method:'POST'});if(!started.ok)throw new Error(await started.text());const created=await started.json();let job=created;while(job.status==='queued'||job.status==='running'){showProgress(job.completed,job.total,job.status);await wait(250);const response=await fetch(`/api/evals/jobs/${job.job_id}`);if(!response.ok)throw new Error(await response.text());job=await response.json()}showProgress(job.completed,job.total,job.status);if(job.status==='failed')throw new Error(job.error||'Evaluation failed');draw(job.result);$('status').textContent=`${job.result.page_count} pages · ${job.result.latency_ms.toFixed(1)} ms`}catch(error){$('status').textContent=`Error: ${error.message}`}finally{buttons.forEach(button=>button.disabled=false)}}
async function start(){const options=await fetch('/api/evals/options').then(response=>response.json());window.corpusPages=options.page_count;$('checkpoint').textContent=options.checkpoint;$('workers').textContent=options.evaluation_workers;$('site').innerHTML=options.sites.map(site=>`<option value="${esc(site.website)}" data-pages="${site.pages}">${esc(site.website)} (${site.pages})</option>`).join('');$('run-all').onclick=()=>run();$('run-site').onclick=()=>run($('site').value);run()}start();
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


def _metric(counts: dict[str, int]) -> dict[str, int | float | None]:
    examples = counts["examples"]
    return {
        **counts,
        "accuracy": counts["correct"] / examples if examples else None,
        "baseline_accuracy": (
            counts["baseline_correct"] / examples if examples else None
        ),
    }


ProgressCallback = Callable[[int, int, str], None]


def _evaluate_record(
    record: PageRecord,
    dataset_dir: Path,
    model: DOMExtractor | Any,
) -> dict[str, object]:
    html = (dataset_dir / record.html_path).read_text(encoding="utf-8")
    page = parse_html(html, strip_chrome=True)
    annotation = load_annotation(
        dataset_dir / "annotations" / f"{record.page_id}.json"
    )
    inference_started = time.perf_counter()
    predicted = model.predict_ids(html)
    inference_ms = (time.perf_counter() - inference_started) * 1_000
    matches = 0
    baseline_matches = 0
    fields: dict[str, dict[str, int | bool | None]] = {}
    for field in FIELDS:
        expected = annotation.labels[field]
        actual = predicted[field]
        baseline = heuristic_select(page, field)
        exact = actual == expected
        baseline_exact = baseline == expected
        matches += int(exact)
        baseline_matches += int(baseline_exact)
        fields[field.value] = {
            "expected": expected,
            "predicted": actual,
            "baseline": baseline,
            "exact": exact,
            "baseline_exact": baseline_exact,
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
        "baseline_matches": baseline_matches,
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
    max_workers: int = 4,
    progress: ProgressCallback | None = None,
) -> dict[str, object]:
    started = time.perf_counter()
    field_counts = {
        field: {"correct": 0, "baseline_correct": 0, "examples": 0}
        for field in FIELDS
    }
    site_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "pages": 0,
            "correct": 0,
            "baseline_correct": 0,
            "examples": 0,
        }
    )
    indexed_rows: list[tuple[int, dict[str, object]]] = []
    total = len(records)
    if total:
        worker_count = min(max_workers, total)
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            pending = {
                executor.submit(_evaluate_record, record, dataset_dir, model): (
                    index,
                    record,
                )
                for index, record in enumerate(records)
            }
            for completed, future in enumerate(as_completed(pending), start=1):
                index, record = pending[future]
                indexed_rows.append((index, future.result()))
                if progress is not None:
                    progress(completed, total, record.page_id)
    page_rows = [row for _, row in sorted(indexed_rows)]
    for row in page_rows:
        matches = int(row["matches"])
        baseline_matches = int(row["baseline_matches"])
        fields = row["fields"]
        for field in FIELDS:
            field_result = fields[field.value]
            field_counts[field]["examples"] += 1
            field_counts[field]["correct"] += int(field_result["exact"])
            field_counts[field]["baseline_correct"] += int(
                field_result["baseline_exact"]
            )
        site = site_counts[str(row["website"])]
        site["pages"] += 1
        site["correct"] += matches
        site["baseline_correct"] += baseline_matches
        site["examples"] += len(FIELDS)
    sites = [
        {
            "website": website,
            **_metric(counts),
        }
        for website, counts in sorted(site_counts.items())
    ]
    return {
        "checkpoint": str(checkpoint),
        "scope": "all human-reviewed pages across train, validation, and test",
        "evaluated_at": datetime.now(UTC).isoformat(),
        "page_count": len(page_rows),
        "latency_ms": (time.perf_counter() - started) * 1_000,
        "fields": {
            field.value: _metric(field_counts[field]) for field in FIELDS
        },
        "sites": sites,
        "pages": page_rows,
    }


def create_app(
    dataset_dir: Path,
    checkpoint: Path,
    *,
    extractor: DOMExtractor | Any | None = None,
    evaluation_workers: int = 4,
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
        default=4,
        help="Parallel workers for whole-corpus evaluation. Default: 4.",
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
