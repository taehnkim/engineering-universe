import json
from pathlib import Path

from fastapi.testclient import TestClient

from eng_universe.extraction.annotation_app import SHELL, create_app
from eng_universe.extraction.dom import html_sha256
from eng_universe.extraction.manifest import DatasetManifest, PageRecord


def test_core_labeler_exposes_jev_confidence_and_keeps_labels_editable(
    tmp_path: Path,
) -> None:
    html = "<html><body><article><h1>Title</h1><p>Body</p></article></body></html>"
    html_hash = html_sha256(html)
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
                split="validation",
                capture_kind="browser",
                html_hash=html_hash,
            ),
        ),
    ).save(tmp_path / "manifest.json")
    labels = {
        "article": 2,
        "title": 3,
        "authors": None,
        "date": None,
        "summary": None,
        "relative_date": None,
    }
    (tmp_path / "annotations/page.json").write_text(
        json.dumps(
            {
                "page_id": "page",
                "html_hash": html_hash,
                "review_status": "draft",
                "needs_review": True,
                "labels": labels,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "jev_annotations/page.json").write_text(
        json.dumps(
            {
                "page_id": "page",
                "html_hash": html_hash,
                "model": "jev-1.13.0",
                "latency_ms": 123.4,
                "labels": labels,
                "metadata": {
                    field: {"confidence": 0.9} for field in labels
                },
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app(tmp_path))

    listing = client.get("/api/pages").json()
    detail = client.get("/api/pages/page").json()

    assert listing[0]["split"] == "validation"
    assert listing[0]["jev_labeled"] is True
    assert detail["jev"]["model"] == "jev-1.13.0"
    assert detail["jev"]["metadata"]["title"]["confidence"] == 0.9

    edited = dict(labels, title=2)
    response = client.post(
        "/api/pages/page",
        json={"html_hash": html_hash, "labels": edited, "needs_review": False},
    )
    assert response.status_code == 200
    assert client.get("/api/pages/page").json()["labels"]["title"] == 2


def test_field_tabs_focus_and_highlight_the_selected_node() -> None:
    assert "function focusSelection(scroll=false)" in SHELL
    assert "el.scrollIntoView({behavior:'smooth',block:'center'" in SHELL
    assert 'data-labeler-selected="true"' in SHELL
    assert "b.onclick=()=>activateField(b.dataset.field)" in SHELL
