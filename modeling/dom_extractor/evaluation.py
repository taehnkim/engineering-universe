"""Held-out website evaluation and failure inspection."""

from __future__ import annotations

import argparse
import json
import re
import resource
import statistics
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from html import escape
from pathlib import Path

import bs4
import numpy as np
import torch

from eng_universe.extraction.contract import FIELDS, Field
from eng_universe.extraction.dom import ParsedPage
from eng_universe.extraction.inference import DOMExtractor
from modeling.dom_extractor.dataset import iter_labeled_pages

WORD_RE = re.compile(r"\w+", re.UNICODE)


def _word_error(
    page: ParsedPage, expected: int | None, predicted: int | None
) -> tuple[int, int]:
    expected_words = Counter(
        WORD_RE.findall(page.candidate(expected).get_text(" ", strip=True).lower())
        if expected is not None
        else []
    )
    predicted_words = Counter(
        WORD_RE.findall(page.candidate(predicted).get_text(" ", strip=True).lower())
        if predicted is not None
        else []
    )
    overlap = expected_words & predicted_words
    return sum((expected_words - overlap).values()), sum(
        (predicted_words - overlap).values()
    )


def _empty_counts() -> dict[str, float]:
    return {
        "correct": 0,
        "count": 0,
        "missing_true_positive": 0,
        "missing_predicted": 0,
        "missing_expected": 0,
        "missing_words": 0,
        "unwanted_words": 0,
    }


def _finish(values: dict[str, float]) -> dict[str, float | None]:
    if values["count"] == 0:
        return {
            "exact_selected_node_accuracy": None,
            "missing_precision": None,
            "missing_recall": None,
            "missing_desired_words": 0,
            "included_unwanted_words": 0,
            "examples": 0,
        }
    return {
        "exact_selected_node_accuracy": values["correct"] / max(1, values["count"]),
        "missing_precision": values["missing_true_positive"]
        / max(1, values["missing_predicted"]),
        "missing_recall": values["missing_true_positive"]
        / max(1, values["missing_expected"]),
        "missing_desired_words": values["missing_words"],
        "included_unwanted_words": values["unwanted_words"],
        "examples": values["count"],
    }


def _tree_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def evaluate(
    dataset_dir: Path,
    checkpoint: Path,
    output_dir: Path,
    *,
    include_jev_drafts: bool = False,
) -> dict[str, object]:
    extractor = DOMExtractor(checkpoint)
    counts = {field: _empty_counts() for field in FIELDS}
    website_counts: dict[str, dict[Field, dict[str, float]]] = defaultdict(
        lambda: {field: _empty_counts() for field in FIELDS}
    )
    failures: list[str] = []
    latencies: list[float] = []
    pages = list(
        iter_labeled_pages(
            dataset_dir,
            "test",
            include_jev_drafts=include_jev_drafts,
        )
    )
    for item in pages:
        started = time.perf_counter()
        predicted = extractor.predict_ids(item.page.original_html)
        latencies.append((time.perf_counter() - started) * 1000)
        for field in FIELDS:
            expected_id = item.annotation.labels[field]
            predicted_id = predicted[field]
            for bucket, actual in (
                (counts[field], predicted_id),
                (website_counts[item.record.website][field], predicted_id),
            ):
                bucket["count"] += 1
                bucket["correct"] += float(actual == expected_id)
                bucket["missing_expected"] += float(expected_id is None)
                bucket["missing_predicted"] += float(actual is None)
                bucket["missing_true_positive"] += float(
                    actual is None and expected_id is None
                )
                missing, unwanted = _word_error(item.page, expected_id, actual)
                bucket["missing_words"] += missing
                bucket["unwanted_words"] += unwanted
            if predicted_id != expected_id:
                expected_text = (
                    "MISSING"
                    if expected_id is None
                    else item.page.candidate(expected_id).get_text(" ", strip=True)[
                        :1000
                    ]
                )
                predicted_text = (
                    "MISSING"
                    if predicted_id is None
                    else item.page.candidate(predicted_id).get_text(" ", strip=True)[
                        :1000
                    ]
                )
                failures.append(
                    f"<section><h2>{escape(item.record.page_id)} — {field.value}</h2>"
                    f"<p><a href='{escape(item.record.url)}'>{escape(item.record.url)}</a></p>"
                    f"<h3>Expected node {expected_id}</h3><pre>{escape(expected_text)}</pre>"
                    f"<h3>Predicted node {predicted_id}</h3><pre>{escape(predicted_text)}</pre></section>"
                )
    source_size = sum(
        path.stat().st_size for path in Path(__file__).parent.glob("*.py")
    )
    checkpoint_size = checkpoint.stat().st_size
    runtime_size = sum(
        _tree_size(Path(module.__file__).parent)
        for module in (torch, np, bs4)
        if module.__file__ is not None
    )
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_rss_bytes = int(peak_rss if sys.platform == "darwin" else peak_rss * 1024)
    report: dict[str, object] = {
        "scope": (
            "held-out test websites with Jev pseudo-labels"
            if include_jev_drafts
            else "held-out test websites with human-reviewed labels only"
        ),
        "label_policy": (
            "human_reviewed_and_jev_drafts"
            if include_jev_drafts
            else "human_reviewed_only"
        ),
        "test_pages": len(pages),
        "fields": {field.value: _finish(counts[field]) for field in FIELDS},
        "by_website": {
            website: {field.value: _finish(values[field]) for field in FIELDS}
            for website, values in website_counts.items()
        },
        "size_and_speed": {
            "checkpoint_bytes": checkpoint_size,
            "application_source_bytes": source_size,
            "runtime_packages_bytes": runtime_size,
            "estimated_deployment_bytes": source_size + checkpoint_size + runtime_size,
            "mean_page_latency_ms": statistics.mean(latencies) if latencies else None,
            "p95_page_latency_ms": sorted(latencies)[int(0.95 * (len(latencies) - 1))]
            if latencies
            else None,
            "process_peak_rss_bytes": peak_rss_bytes,
        },
        "known_limitation": "v1 uses structural features and tag embeddings, not article words",
        "next_experiment": (
            "Add compact class/id token embeddings and neighboring-node context, then "
            "re-evaluate title/date errors; do not enlarge or quantize the MLP first."
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "failures.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Extraction failures</title>"
        "<style>body{font:15px system-ui;max-width:1100px;margin:auto}section{border-bottom:1px solid #ccc;padding:20px}pre{white-space:pre-wrap;background:#f5f5f5;padding:10px}</style>"
        + "".join(failures),
        encoding="utf-8",
    )
    return report


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--include-jev-drafts",
        action="store_true",
        help="Evaluate against Jev-backed test drafts as pseudo-ground-truth.",
    )
    args = parser.parse_args(argv)
    print(
        json.dumps(
            evaluate(
                args.dataset_dir,
                args.checkpoint,
                args.output_dir,
                include_jev_drafts=args.include_jev_drafts,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
