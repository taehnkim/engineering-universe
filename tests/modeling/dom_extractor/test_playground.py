from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from eng_universe.extraction.contract import FIELDS, Field
from eng_universe.extraction.dom import html_sha256, parse_html
from modeling.dom_extractor.apps.playground import EVALS_SHELL, SHELL, create_app
from modeling.dom_extractor.manifest import DatasetManifest, PageRecord


class FakeExtractor:
    def __init__(self, article_id: int, title_id: int) -> None:
        self.article_id = article_id
        self.title_id = title_id

    def predict_ids(self, html: str) -> dict[Field, int | None]:
        return {
            field: (
                self.article_id
                if field == Field.ARTICLE
                else self.title_id
                if field == Field.TITLE
                else None
            )
            for field in FIELDS
        }


def test_playground_runs_inference_and_returns_visualizable_nodes(
    tmp_path: Path,
) -> None:
    html = "<html><body><article><h1>Title</h1><p>Body</p></article></body></html>"
    page = parse_html(html)
    article_id = next(
        candidate.node_id
        for candidate in page.candidates
        if candidate.element.name == "article"
    )
    title_id = next(
        candidate.node_id
        for candidate in page.candidates
        if candidate.element.name == "h1"
    )
    (tmp_path / "html").mkdir()
    (tmp_path / "annotations").mkdir()
    (tmp_path / "jev_annotations").mkdir()
    (tmp_path / "html/page.html").write_text(html, encoding="utf-8")
    DatasetManifest(
        1,
        (
            PageRecord(
                page_id="page",
                source_id="source",
                company="Company",
                website="example.com",
                url="https://example.com/page",
                html_path="html/page.html",
                split="test",
                capture_kind="browser",
                html_hash=html_sha256(html),
            ),
        ),
    ).save(tmp_path / "manifest.json")
    labels = {
        "article": article_id,
        "title": title_id,
        "authors": None,
        "date": None,
        "summary": None,
        "relative_date": None,
    }
    (tmp_path / "annotations/page.json").write_text(
        json.dumps(
            {
                "page_id": "page",
                "html_hash": html_sha256(html),
                "review_status": "draft",
                "needs_review": True,
                "labels": labels,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "jev_annotations/page.json").write_text("{}", encoding="utf-8")
    client = TestClient(
        create_app(
            tmp_path,
            Path("checkpoint.pt"),
            extractor=FakeExtractor(article_id, title_id),
        )
    )

    detail = client.get("/api/pages/page").json()
    result = client.post("/api/pages/page/run").json()

    assert detail["reference_source"] == "Jev draft"
    assert f'data-eu-node-id="{title_id}"' in detail["document_html"]
    assert result["predictions"]["article"] == article_id
    assert result["results"]["title"]["text"] == "Title"
    assert result["matches"] == len(FIELDS)


def test_playground_ui_has_run_and_prediction_focus_controls() -> None:
    assert 'id="run" class="run">RUN</button>' in SHELL
    assert "function focusPrediction(scroll=false)" in SHELL
    assert "el.scrollIntoView({behavior:'instant',block:'center'" in SHELL
    assert 'data-playground-prediction="true"' in SHELL
    assert '<span id="latency" class="latency"></span>' in SHELL
    assert "$('latency').textContent=`${result.latency_ms.toFixed(1)} ms`" in SHELL
    assert 'class="source-link"' in SHELL
    assert "Run the checkpoint, then choose a field" not in SHELL
    assert 'data-field="${n}" role="button" tabindex="0"' in SHELL
    assert '<span class="field-name">${n}</span>' in SHELL
    assert "card.onclick=()=>activateField(card.dataset.field)" in SHELL
    assert 'href="/evals">← Evals</a>' in SHELL
    assert "new URLSearchParams(location.search).get('page')" in SHELL


def test_whole_corpus_evaluation_dashboard_uses_human_reviews(
    tmp_path: Path,
) -> None:
    html = "<html><body><article><h1>Test title</h1><p>Body</p></article></body></html>"
    page = parse_html(html)
    article_id = next(
        candidate.node_id
        for candidate in page.candidates
        if candidate.element.name == "article"
    )
    title_id = next(
        candidate.node_id
        for candidate in page.candidates
        if candidate.element.name == "h1"
    )
    (tmp_path / "html").mkdir()
    (tmp_path / "annotations").mkdir()
    (tmp_path / "html/page.html").write_text(html, encoding="utf-8")
    DatasetManifest(
        1,
        (
            PageRecord(
                page_id="page",
                source_id="source",
                company="Company",
                website="engineering.example.com",
                url="https://engineering.example.com/page",
                html_path="html/page.html",
                split="train",
                capture_kind="browser",
                html_hash=html_sha256(html),
            ),
        ),
    ).save(tmp_path / "manifest.json")
    (tmp_path / "annotations/page.json").write_text(
        json.dumps(
            {
                "page_id": "page",
                "html_hash": html_sha256(html),
                "review_status": "reviewed",
                "needs_review": False,
                "labels": {
                    "article": article_id,
                    "title": title_id,
                    "authors": None,
                    "date": None,
                    "summary": None,
                    "relative_date": None,
                },
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(
        create_app(
            tmp_path,
            Path("best.pt"),
            extractor=FakeExtractor(article_id, title_id),
        )
    )

    options = client.get("/api/evals/options").json()
    result = client.post("/api/evals/run").json()
    site_result = client.post("/api/evals/run?website=engineering.example.com").json()

    assert client.get("/evals").status_code == 200
    assert client.get("/playground?page=page").status_code == 200
    assert options["page_count"] == 1
    assert options["evaluation_workers"] == 4
    assert options["cache_token"]
    assert options["sites"] == [{"website": "engineering.example.com", "pages": 1}]
    assert result["page_count"] == 1
    assert result["pages"][0]["title"] == "Test title"
    assert result["sites"][0]["accuracy"] == 1.0
    assert all(field["accuracy"] == 1.0 for field in result["fields"].values())
    assert site_result["page_count"] == 1
    assert client.post("/api/evals/run?website=unknown.example.com").status_code == 404

    with TestClient(
        create_app(
            tmp_path,
            Path("best.pt"),
            extractor=FakeExtractor(article_id, title_id),
        )
    ) as job_client:
        job = job_client.post("/api/evals/jobs").json()
        assert job["total"] == 1
        for _ in range(100):
            job = job_client.get(f"/api/evals/jobs/{job['job_id']}").json()
            if job["status"] not in {"queued", "running"}:
                break
            time.sleep(0.01)
        assert job["status"] == "complete"
        assert job["completed"] == 1
        assert job["result"]["page_count"] == 1
        assert "fields" not in job["result"]["pages"][0]


def test_evaluation_dashboard_has_requested_controls_and_links() -> None:
    assert 'id="run-all" class="run-all">RUN ALL</button>' in EVALS_SHELL
    assert 'id="run-site">RUN SITE</button>' in EVALS_SHELL
    assert "baseline_accuracy" in EVALS_SHELL
    assert "Accuracy by site" in EVALS_SHELL
    assert 'href="${esc(page.url)}"' in EVALS_SHELL
    assert 'href="/playground?page=${encodeURIComponent(page.page_id)}"' in EVALS_SHELL
    assert 'role="progressbar"' in EVALS_SHELL
    assert "pages classified" in EVALS_SHELL
    assert "fetch('/api/evals/jobs'" in EVALS_SHELL
    assert "localStorage.setItem(cacheKey(website)" in EVALS_SHELL
    assert "if(!restoreCached())run()" in EVALS_SHELL
    assert "run()}start()" in EVALS_SHELL
