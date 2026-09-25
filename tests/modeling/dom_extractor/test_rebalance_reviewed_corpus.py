"""The training split can change only after review and by whole website."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eng_universe.extraction.dom import html_sha256
from modeling.dom_extractor.commands.rebalance_reviewed_corpus import apply_plan
from modeling.dom_extractor.manifest import DatasetManifest, PageRecord


def _record(page_id: str, website: str, split: str, source_id: str) -> PageRecord:
    return PageRecord(
        page_id=page_id,
        source_id=source_id,
        company=website,
        website=website,
        url=f"https://{website}/{page_id}",
        html_path=f"html/{page_id}.html",
        split=split,
        capture_kind="http",
        html_hash=html_sha256(page_id),
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    dataset = tmp_path / "raw"
    dataset.mkdir()
    (dataset / "annotations").mkdir()
    records = (
        _record("old-train", "old.example.com", "train", "old"),
        _record("old-test", "holdout.example.com", "test", "old"),
        _record("new-train", "train.example.com", "test", "test-new"),
        _record("new-validation", "validation.example.com", "test", "test-new"),
    )
    DatasetManifest(1, records).save(dataset / "manifest.json")
    for record in records[2:]:
        (dataset / "annotations" / f"{record.page_id}.json").write_text(
            json.dumps(
                {
                    "page_id": record.page_id,
                    "html_hash": record.html_hash,
                    "review_status": "reviewed",
                    "needs_review": False,
                    "labels": {},
                }
            ),
            encoding="utf-8",
        )
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "version": 1,
                "sourceIdPrefix": "test-",
                "expectedFinalPages": {"train": 2, "validation": 1, "test": 1},
                "sites": {
                    "train.example.com": {"pages": 1, "split": "train"},
                    "validation.example.com": {"pages": 1, "split": "validation"},
                },
            }
        ),
        encoding="utf-8",
    )
    return dataset, plan


def test_rebalance_is_dry_run_by_default_and_backs_up_on_apply(tmp_path: Path) -> None:
    dataset, plan = _fixture(tmp_path)
    original = (dataset / "manifest.json").read_bytes()
    backup_dir = tmp_path / "backups"

    preview = apply_plan(dataset, plan, backup_dir, apply=False)
    assert preview["split_pages"] == {"train": 2, "validation": 1, "test": 1}
    assert preview["moved_pages"] == 2
    assert not preview["applied"]
    assert (dataset / "manifest.json").read_bytes() == original

    applied = apply_plan(dataset, plan, backup_dir, apply=True)
    assert applied["applied"]
    assert Path(applied["manifest_backup"]).read_bytes() == original
    new_manifest = DatasetManifest.load(dataset / "manifest.json")
    assert {record.page_id: record.split for record in new_manifest.pages} == {
        "old-train": "train",
        "old-test": "test",
        "new-train": "train",
        "new-validation": "validation",
    }
    assert not apply_plan(dataset, plan, backup_dir, apply=True)["applied"]


def test_rebalance_rejects_unreviewed_new_page(tmp_path: Path) -> None:
    dataset, plan = _fixture(tmp_path)
    annotation_path = dataset / "annotations/new-train.json"
    annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
    annotation["needs_review"] = True
    annotation_path.write_text(json.dumps(annotation), encoding="utf-8")
    with pytest.raises(ValueError, match="not a current human review"):
        apply_plan(dataset, plan, tmp_path / "backups", apply=True)
    assert not (tmp_path / "backups").exists()
