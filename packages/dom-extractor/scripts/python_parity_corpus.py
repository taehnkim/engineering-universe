"""Generate read-only Python predictions for an npm-port compatibility audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from eng_universe.extraction.contract import load_annotation
from eng_universe.extraction.dom import parse_html
from eng_universe.extraction.inference import DOMExtractor
from modeling.dom_extractor.manifest import DatasetManifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--author-boundary", type=Path)
    args = parser.parse_args()
    model = DOMExtractor(
        args.checkpoint, author_boundary_checkpoint=args.author_boundary
    )
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
        page = parse_html(html, strip_chrome=True)
        prediction = model.predict_page(page)
        content_hashes = {}
        for field, node_id in prediction.items():
            if node_id is None:
                content_hashes[field.value] = {"text": None, "html": None, "normalizedText": None}
            else:
                selected = page.selected_content(node_id)
                content_hashes[field.value] = {
                    key: hashlib.sha256(str(selected[key]).encode("utf-8")).hexdigest()
                    for key in ("text", "html")
                }
                content_hashes[field.value]["normalizedText"] = hashlib.sha256(
                    re.sub(r"\s+", " ", str(selected["text"])).strip().encode("utf-8")
                ).hexdigest()
        rows.append(
            {
                "pageId": record.page_id,
                "website": record.website,
                "htmlPath": record.html_path,
                "expected": {field.value: value for field, value in annotation.labels.items()},
                "python": {field.value: value for field, value in prediction.items()},
                "contentHashes": content_hashes,
            }
        )
    args.output.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(rows)} reviewed pages to {args.output}")


if __name__ == "__main__":
    main()
