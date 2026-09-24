"""Audit the experimental Go DOM backend against the checkpoint's Python DOM.

Build the shared library and set ENG_UNIVERSE_GO_DOM_LIBRARY before running.
This command reads annotations and HTML but does not alter either one.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from pathlib import Path

import numpy as np

from eng_universe.extraction.contract import FIELDS, load_annotation
from eng_universe.extraction.dom import annotation_html, parse_html
from eng_universe.extraction.features import featurize_page
from eng_universe.extraction.inference import DOMExtractor
from modeling.dom_extractor.manifest import DatasetManifest

FEATURE_ARRAYS = (
    "node_ids",
    "tag_ids",
    "parent_tag_ids",
    "grandparent_tag_ids",
    "previous_tag_ids",
    "next_tag_ids",
    "attribute_token_ids",
    "text_shape_token_ids",
    "numeric",
)
PREVIEW_ID = re.compile(r'data-eu-node-id="(\d+)"')


def audit(dataset_dir: Path, *, limit: int = 0) -> dict[str, object]:
    model = DOMExtractor()
    manifest = DatasetManifest.load(dataset_dir / "manifest.json")
    counts: Counter[str] = Counter()
    array_differences: Counter[str] = Counter()
    mismatches: list[dict[str, object]] = []
    timings: Counter[str] = Counter()
    for record in manifest.pages:
        annotation_path = dataset_dir / "annotations" / f"{record.page_id}.json"
        if not annotation_path.exists():
            continue
        annotation = load_annotation(annotation_path)
        if annotation.review_status != "reviewed" or annotation.needs_review:
            continue
        html = (dataset_dir / record.html_path).read_text(encoding="utf-8")
        started = time.perf_counter()
        python_page = parse_html(html, strip_chrome=True)
        timings["python_parse_ms"] += (time.perf_counter() - started) * 1_000
        started = time.perf_counter()
        go_page = parse_html(html, strip_chrome=True, backend="go")
        timings["go_prepare_and_reconstruct_ms"] += (
            time.perf_counter() - started
        ) * 1_000
        if annotation.html_hash != python_page.html_hash:
            raise ValueError(f"stale annotation for {record.page_id}")
        if record.html_hash != python_page.html_hash:
            raise ValueError(f"stale manifest for {record.page_id}")
        counts["pages"] += 1
        python_ids = [candidate.node_id for candidate in python_page.candidates]
        go_ids = [candidate.node_id for candidate in go_page.candidates]
        differing: list[str] = []
        if python_ids != go_ids:
            counts["candidate_id_mismatch"] += 1
            differing.append("candidate_ids")
        python_features = featurize_page(
            python_page, model.vocabulary, model.semantic_vocabulary, model.normalizer
        )
        go_features = featurize_page(
            go_page, model.vocabulary, model.semantic_vocabulary, model.normalizer
        )
        for name in FEATURE_ARRAYS:
            if not np.array_equal(
                getattr(python_features, name), getattr(go_features, name)
            ):
                array_differences[name] += 1
                differing.append(name)
        if any(name in differing for name in FEATURE_ARRAYS):
            counts["feature_mismatch_pages"] += 1
        python_prediction = model.predict_page(python_page)
        go_prediction = model.predict_page(go_page)
        changed_fields = [
            field.value
            for field in FIELDS
            if python_prediction[field] != go_prediction[field]
        ]
        if changed_fields:
            counts["prediction_mismatch_pages"] += 1
            differing.append("predictions")
        changed_content_fields = [
            field.value
            for field in FIELDS
            if python_prediction[field] is not None
            and go_prediction[field] is not None
            and python_page.selected_content(python_prediction[field])["text"]
            != go_page.selected_content(go_prediction[field])["text"]
        ]
        if changed_content_fields:
            counts["selected_text_mismatch_pages"] += 1
            differing.append("selected_text")
        for field in FIELDS:
            counts[f"python_correct_{field.value}"] += int(
                python_prediction[field] == annotation.labels[field]
            )
            counts[f"go_correct_{field.value}"] += int(
                go_prediction[field] == annotation.labels[field]
            )
            if python_prediction[field] != go_prediction[field]:
                counts[f"prediction_mismatch_{field.value}"] += 1
        python_preview = annotation_html(python_page)
        go_preview = annotation_html(go_page)
        if PREVIEW_ID.findall(python_preview) != PREVIEW_ID.findall(go_preview):
            counts["preview_id_mismatch_pages"] += 1
            differing.append("preview_ids")
        if differing:
            mismatches.append(
                {
                    "page_id": record.page_id,
                    "website": record.website,
                    "differences": differing,
                    "changed_prediction_fields": changed_fields,
                    "changed_content_fields": changed_content_fields,
                }
            )
        if limit and counts["pages"] >= limit:
            break
    return {
        "reviewed_pages": counts["pages"],
        "candidate_id_mismatch_pages": counts["candidate_id_mismatch"],
        "feature_mismatch_pages": counts["feature_mismatch_pages"],
        "prediction_mismatch_pages": counts["prediction_mismatch_pages"],
        "selected_text_mismatch_pages": counts["selected_text_mismatch_pages"],
        "preview_id_mismatch_pages": counts["preview_id_mismatch_pages"],
        "feature_array_mismatch_pages": dict(array_differences),
        "field_accuracy": {
            field.value: {
                "python": counts[f"python_correct_{field.value}"] / counts["pages"],
                "go": counts[f"go_correct_{field.value}"] / counts["pages"],
                "changed_predictions": counts[f"prediction_mismatch_{field.value}"],
            }
            for field in FIELDS
        }
        if counts["pages"]
        else {},
        "parse_timing_ms": dict(timings),
        "mismatches": mismatches,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-dir", type=Path, default=Path("data/learned_extraction/raw")
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="0 audits all reviewed pages"
    )
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    args = parser.parse_args()
    result = audit(args.dataset_dir, limit=args.limit)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    summary = {key: value for key, value in result.items() if key != "mismatches"}
    print(json.dumps(summary, indent=2))
    print(f"Mismatched pages: {len(result['mismatches'])}")


if __name__ == "__main__":
    main()
