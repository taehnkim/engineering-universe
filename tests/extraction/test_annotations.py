import json
from pathlib import Path

from eng_universe.extraction.contract import Annotation, Field, load_annotation
from eng_universe.extraction.dataset import iter_labeled_pages
from eng_universe.extraction.dom import html_sha256
from eng_universe.extraction.manifest import DatasetManifest, PageRecord


def _write_page(dataset_dir: Path, review_status: str) -> None:
    html = "<html><body><article><h1>Title</h1><p>Body</p></article></body></html>"
    (dataset_dir / "html").mkdir(parents=True)
    (dataset_dir / "annotations").mkdir()
    (dataset_dir / "html/page.html").write_text(html)
    DatasetManifest(
        1,
        (
            PageRecord(
                page_id="page",
                source_id="source",
                company="Company",
                website="example.com",
                url="https://example.com/post",
                html_path="html/page.html",
                split="train",
                capture_kind="browser",
                html_hash=html_sha256(html),
            ),
        ),
    ).save(dataset_dir / "manifest.json")
    (dataset_dir / "annotations/page.json").write_text(
        json.dumps(
            {
                "page_id": "page",
                "html_hash": html_sha256(html),
                "review_status": review_status,
                "needs_review": False,
                "labels": {
                    "article": 2,
                    "title": 3,
                    "author": None,
                    "date": None,
                    "summary": None,
                    "relative_date": None,
                },
            }
        )
    )


def test_training_uses_only_human_reviewed_annotations(tmp_path: Path) -> None:
    _write_page(tmp_path, "draft")
    assert list(iter_labeled_pages(tmp_path, "train")) == []
    value = json.loads((tmp_path / "annotations/page.json").read_text())
    value["review_status"] = "reviewed"
    (tmp_path / "annotations/page.json").write_text(json.dumps(value))
    assert len(list(iter_labeled_pages(tmp_path, "train"))) == 1


def test_legacy_annotation_does_not_count_as_reviewed(tmp_path: Path) -> None:
    _write_page(tmp_path, "draft")
    value = json.loads((tmp_path / "annotations/page.json").read_text())
    value.pop("review_status")
    (tmp_path / "annotations/page.json").write_text(json.dumps(value))
    annotation = load_annotation(tmp_path / "annotations/page.json")
    assert annotation.review_status == "legacy"
    assert list(iter_labeled_pages(tmp_path, "train")) == []


def test_annotation_round_trip_includes_review_status(tmp_path: Path) -> None:
    annotation = Annotation(
        "page",
        "hash",
        {field: None for field in Field},
        review_status="reviewed",
    )
    assert annotation.to_dict()["review_status"] == "reviewed"


def test_legacy_author_key_migrates_to_authors() -> None:
    annotation = Annotation.from_dict(
        {
            "page_id": "page",
            "html_hash": "hash",
            "labels": {"author": 7},
        }
    )

    assert annotation.labels[Field.AUTHORS] == 7
    assert "author" not in annotation.to_dict()["labels"]
    assert annotation.to_dict()["labels"]["authors"] == 7
