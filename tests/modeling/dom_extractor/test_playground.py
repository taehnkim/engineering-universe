from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from eng_universe.extraction.contract import FIELDS, Field
from eng_universe.extraction.dom import html_sha256, parse_html
from modeling.dom_extractor.apps.playground import SHELL, create_app
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
    assert "el.scrollIntoView({behavior:'smooth',block:'center'" in SHELL
    assert 'data-playground-prediction="true"' in SHELL
