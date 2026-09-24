import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from modeling.dom_extractor.apps.labeler import SHELL, create_app
from eng_universe.extraction.dom import html_sha256
from modeling.dom_extractor.manifest import DatasetManifest, PageRecord


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
    assert listing[0]["needs_review"] is True
    assert detail["jev"]["model"] == "jev-1.13.0"
    assert detail["jev"]["metadata"]["title"]["confidence"] == 0.9

    edited = dict(labels, title=2)
    response = client.post(
        "/api/pages/page",
        json={"html_hash": html_hash, "labels": edited, "needs_review": False},
    )
    assert response.status_code == 200
    assert client.get("/api/pages/page").json()["labels"]["title"] == 2
    stored = json.loads((tmp_path / "annotations/page.json").read_text())
    assert stored["labels"]["summary"] is None
    assert stored["labels"]["relative_date"] is None
    assert "const names=['article','title','authors','date'];" in SHELL

    class FakeBot:
        def prepare(self, value: str) -> SimpleNamespace:
            assert value == html
            return SimpleNamespace(page=SimpleNamespace(html_hash=html_hash))

        async def label_field_prepared_async(
            self,
            prepared: object,
            record: PageRecord,
            field: object,
        ) -> dict[str, object]:
            assert record.page_id == "page"
            assert str(field) == "Field.ARTICLE"
            return {
                "field": "article",
                "node_id": 3,
                "metadata": {
                    "choice": "node_3",
                    "confidence": 0.77,
                    "selected_probability": 0.77,
                    "probabilities": {"node_3": 0.77},
                    "latency_ms": 42.5,
                },
                "model": "jev-1.13.0",
                "latency_ms": 42.5,
                "usage": {"input_tokens": 10, "output_tokens": 2},
            }

    live_client = TestClient(create_app(tmp_path, jev_bot=FakeBot()))
    live_response = live_client.post("/api/pages/page/jev/article")

    assert live_response.status_code == 200
    assert live_response.json()["metadata"]["confidence"] == 0.77
    audit = json.loads((tmp_path / "jev_annotations/page.json").read_text())
    assert audit["labels"]["article"] == 3
    assert "summary" in audit["labels"]
    assert audit["metadata"]["article"]["latency_ms"] == 42.5
    assert audit["field_reruns"]["article"]["latency_ms"] == 42.5
    assert live_client.get("/api/pages/page").json()["labels"]["article"] == 2


def test_field_tabs_focus_and_highlight_the_selected_node() -> None:
    assert "function focusSelection(scroll=false)" in SHELL
    assert "el.scrollIntoView({behavior:'instant',block:'center'" in SHELL
    assert 'data-labeler-selected="true"' in SHELL
    assert 'data-field-card="${n}"' in SHELL
    assert "card.onclick=()=>activateField(card.dataset.fieldCard)" in SHELL


def test_keyboard_cycles_through_save_review_and_fields() -> None:
    assert "const keyboardOrder=['save','review',...names]" in SHELL
    assert 'data-keyboard-target="save"' in SHELL
    assert 'data-keyboard-target="review"' in SHELL
    assert 'data-keyboard-target="${n}"' in SHELL
    assert "if(e.key==='Tab')" in SHELL
    assert "keyboardTarget.click()" in SHELL


def test_remove_page_archives_its_data_and_removes_it_from_manifest(
    tmp_path: Path,
) -> None:
    html = "<html><body><article><h1>Title</h1></article></body></html>"
    html_hash = html_sha256(html)
    (tmp_path / "html").mkdir()
    (tmp_path / "annotations").mkdir()
    (tmp_path / "jev_annotations").mkdir()
    (tmp_path / "html/page.html").write_text(html, encoding="utf-8")
    labels = {
        "article": 2,
        "title": 3,
        "authors": None,
        "date": None,
        "summary": None,
        "relative_date": None,
    }
    annotation = {
        "page_id": "page",
        "html_hash": html_hash,
        "review_status": "draft",
        "needs_review": True,
        "labels": labels,
    }
    (tmp_path / "annotations/page.json").write_text(
        json.dumps(annotation), encoding="utf-8"
    )
    (tmp_path / "jev_annotations/page.json").write_text(
        json.dumps({**annotation, "model": "jev-1.13.0", "latency_ms": 10}),
        encoding="utf-8",
    )
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
                split="train",
                capture_kind="browser",
                html_hash=html_hash,
            ),
        ),
    ).save(tmp_path / "manifest.json")
    client = TestClient(create_app(tmp_path))

    response = client.delete("/api/pages/page")

    assert response.status_code == 200
    assert client.get("/api/pages").json() == []
    assert client.get("/api/pages/page").status_code == 404
    assert DatasetManifest.load(tmp_path / "manifest.json").pages == ()
    assert not (tmp_path / "html/page.html").exists()
    archive = tmp_path / "removed/page"
    assert (archive / "page.html").read_text(encoding="utf-8") == html
    assert json.loads((archive / "annotation.json").read_text())["labels"] == labels
    assert (archive / "jev_annotation.json").exists()
    assert json.loads((archive / "record.json").read_text())["page"]["page_id"] == "page"


def test_remove_button_confirms_before_deleting_current_page() -> None:
    assert '<button id="remove" class="danger">Remove page</button>' in SHELL
    assert "confirm(`Move ${id} out of the dataset" in SHELL
    assert "method:'DELETE'" in SHELL


def test_header_puts_the_url_on_an_ellipsized_second_row() -> None:
    assert '<div id="url" class="url-row"></div>' in SHELL
    assert ".url-row{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" in SHELL
    assert "$('url').textContent=payload.url" in SHELL
    assert "$('url').title=payload.url" in SHELL


def test_compact_field_cards_and_review_card_have_requested_controls() -> None:
    save_button = '<button id="save" class="save-review"'
    review_card = '<label id="review-card" class="field review-card"'
    assert save_button in SHELL
    assert review_card in SHELL
    assert SHELL.index(save_button) < SHELL.index(review_card)
    assert SHELL.index(review_card) < SHELL.index('<div id="fields"></div>')
    assert "Selections</h2>" not in SHELL
    assert "Choose a field, then click" not in SHELL
    assert "Make a new Jev API request for this field" in SHELL
    assert '<span class="meta-chip">current: ${nodeLabel(labels[n])}</span>' in SHELL
    assert '<span class="meta-chip jev">Jev: ${nodeLabel(suggested)}' in SHELL
    assert '<strong>Cached Jev first pass</strong> · ${esc(payload.jev.model)}' in SHELL


def test_field_actions_have_hover_and_click_feedback() -> None:
    assert "[data-missing]:hover{background:#7f1d1d" in SHELL
    assert "[data-rerun-jev]:hover{background:#4c1d95" in SHELL
    assert "b.textContent='Running Jev…'" in SHELL
    assert "button.textContent='Jev updated ✓'" in SHELL
    assert "Marked ${active} missing (not saved)" in SHELL


def test_rerun_jev_action_shows_new_confidence_and_field_latency() -> None:
    assert ">rerun Jev</button>" in SHELL
    assert "Jev confidence: ${confidenceLabel}" in SHELL
    assert "latency.toFixed(1)" in SHELL
    assert "page request" in SHELL
    assert "/jev/${encodeURIComponent(field)}`" in SHELL
    assert "result.metadata.confidence*100" in SHELL


def test_review_filter_can_show_only_pages_needing_human_review() -> None:
    assert '<select id="review-filter">' in SHELL
    assert '<option value="needs">Needs human review</option>' in SHELL
    assert '<option value="verified">Human verified</option>' in SHELL
    assert "function needsHumanReview(p){return p.review_status!=='reviewed'||p.needs_review}" in SHELL
    assert "$('review-filter').onchange=filterPages" in SHELL
    assert "page.needs_review=body.needs_review" in SHELL
