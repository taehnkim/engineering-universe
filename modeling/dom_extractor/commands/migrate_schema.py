"""Capture scrape timestamps while preserving all legacy annotation labels."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path

from eng_universe.extraction.contract import load_annotation, save_annotation
from modeling.dom_extractor.manifest import DatasetManifest


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
    }
    for path in sorted((dataset_dir / "annotations").glob("*.json")):
        annotation = load_annotation(path)
        # Keep legacy labels intact when rewriting an annotation.
        save_annotation(path, annotation)
        counts["annotations"] += 1
    return counts


def main() -> None:
    dataset_dir = Path("data/learned_extraction/raw")
    print(json.dumps(migrate(dataset_dir), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
