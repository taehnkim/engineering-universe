"""Generate read-only Python predictions for an npm-port compatibility audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from eng_universe.extraction.contract import load_annotation
from eng_universe.extraction.inference import DOMExtractor
from modeling.dom_extractor.manifest import DatasetManifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    model = DOMExtractor()
    manifest = DatasetManifest.load(args.dataset_dir / "manifest.json")
    rows = []
    for record in manifest.pages:
        annotation_path = args.dataset_dir / "annotations" / f"{record.page_id}.json"
        if not annotation_path.exists():
            continue
        annotation = load_annotation(annotation_path)
        if annotation.review_status != "reviewed" or annotation.needs_review:
            continue
        html = (args.dataset_dir / record.html_path).read_text(encoding="utf-8")
        prediction = model.predict_ids(html)
        rows.append(
            {
                "pageId": record.page_id,
                "website": record.website,
                "htmlPath": record.html_path,
                "expected": {field.value: value for field, value in annotation.labels.items()},
                "python": {field.value: value for field, value in prediction.items()},
            }
        )
    args.output.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(rows)} reviewed pages to {args.output}")


if __name__ == "__main__":
    main()
