"""Migrate extraction annotations and capture timestamps to schema version 2."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path

from eng_universe.extraction.contract import Annotation, Field, load_annotation, save_annotation
from eng_universe.extraction.dom import parse_html
from modeling.dom_extractor.manifest import DatasetManifest
from modeling.dom_extractor.commands.bootstrap_annotations import draft_annotation


def _file_timestamp(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def migrate(dataset_dir: Path) -> dict[str, int]:
    manifest_path = dataset_dir / "manifest.json"
    manifest = DatasetManifest.load(manifest_path)
    records = tuple(
        replace(
            record,
            scraped_at=record.scraped_at
            or _file_timestamp(dataset_dir / record.html_path),
        )
        for record in manifest.pages
    )
    DatasetManifest(max(2, manifest.version), records).save(manifest_path)

    counts = {
        "manifest_pages": len(records),
        "annotations": 0,
        "draft_summaries": 0,
        "draft_relative_dates": 0,
    }
    by_id = {record.page_id: record for record in records}
    for path in sorted((dataset_dir / "annotations").glob("*.json")):
        annotation = load_annotation(path)
        record = by_id[annotation.page_id]
        labels = dict(annotation.labels)
        if annotation.review_status == "draft":
            page = parse_html((dataset_dir / record.html_path).read_text(encoding="utf-8"))
            generated = draft_annotation(record.page_id, page, record.is_article)
            labels[Field.SUMMARY] = generated.labels[Field.SUMMARY]
            labels[Field.RELATIVE_DATE] = generated.labels[Field.RELATIVE_DATE]
        counts["draft_summaries"] += int(labels[Field.SUMMARY] is not None)
        counts["draft_relative_dates"] += int(labels[Field.RELATIVE_DATE] is not None)
        save_annotation(
            path,
            Annotation(
                page_id=annotation.page_id,
                html_hash=annotation.html_hash,
                labels=labels,
                needs_review=annotation.needs_review,
                review_status=annotation.review_status,
            ),
        )
        counts["annotations"] += 1
    return counts


def main() -> None:
    dataset_dir = Path("data/learned_extraction/raw")
    print(json.dumps(migrate(dataset_dir), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
