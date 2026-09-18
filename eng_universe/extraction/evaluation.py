"""Held-out website evaluation, heuristic baseline, and failure inspection."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from html import escape
import json
from pathlib import Path
import re
import resource
import statistics
import sys
import time
from typing import Sequence

from bs4 import Tag
import bs4
import numpy as np
import torch

from eng_universe.extraction.contract import FIELDS, Field
from eng_universe.extraction.dataset import iter_labeled_pages
from eng_universe.extraction.dom import ParsedPage
from eng_universe.extraction.inference import DOMExtractor


WORD_RE = re.compile(r"\w+", re.UNICODE)


def heuristic_select(page: ParsedPage, field: Field) -> int | None:
    best: tuple[float, int] | None = None
    for candidate in page.candidates:
        element = candidate.element
        tag = element.name.lower()
        text = element.get_text(" ", strip=True)
        attrs = " ".join(
            (str(element.get("id", "")), " ".join(element.get("class", [])))
        ).lower()
        score = -1000.0
        if field == Field.ARTICLE:
            paragraphs = len(element.find_all("p"))
            score = min(len(text), 20000) / 1000 + paragraphs * 1.5
            score += 8 if tag in {"article", "main"} else 0
            score -= len(element.find_all("a")) * 0.2
        elif field == Field.TITLE:
            if tag in {"h1", "h2"} and 4 <= len(text) <= 300:
                score = (12 if tag == "h1" else 6) - candidate.node_id / 1000
        elif field == Field.AUTHOR:
            if "author" in attrs or "byline" in attrs or element.get("rel") == ["author"]:
                score = 10 - len(text) / 1000
        elif field == Field.DATE:
            if tag == "time" or element.has_attr("datetime") or "date" in attrs:
                score = 10 - len(text) / 1000
        if best is None or score > best[0]:
            best = (score, candidate.node_id)
    return None if best is None or best[0] < 0 else best[1]


def _word_error(page: ParsedPage, expected: int | None, predicted: int | None) -> tuple[int, int]:
    expected_words = Counter(
        WORD_RE.findall(page.candidate(expected).get_text(" ", strip=True).lower())
        if expected is not None else []
    )
    predicted_words = Counter(
        WORD_RE.findall(page.candidate(predicted).get_text(" ", strip=True).lower())
        if predicted is not None else []
    )
    overlap = expected_words & predicted_words
    return sum((expected_words - overlap).values()), sum((predicted_words - overlap).values())


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


def _finish(values: dict[str, float]) -> dict[str, float]:
    return {
        "exact_selected_node_accuracy": values["correct"] / max(1, values["count"]),
        "missing_precision": values["missing_true_positive"] / max(1, values["missing_predicted"]),
        "missing_recall": values["missing_true_positive"] / max(1, values["missing_expected"]),
        "missing_desired_words": values["missing_words"],
        "included_unwanted_words": values["unwanted_words"],
        "examples": values["count"],
    }


def _tree_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def evaluate(dataset_dir: Path, checkpoint: Path, output_dir: Path) -> dict[str, object]:
    extractor = DOMExtractor(checkpoint)
    counts = {field: _empty_counts() for field in FIELDS}
    baseline_counts = {field: _empty_counts() for field in FIELDS}
    website_counts: dict[str, dict[Field, dict[str, float]]] = defaultdict(
        lambda: {field: _empty_counts() for field in FIELDS}
    )
    failures: list[str] = []
    latencies: list[float] = []
    pages = list(iter_labeled_pages(dataset_dir, "test"))
    for item in pages:
        started = time.perf_counter()
        predicted = extractor.predict_ids(item.page.original_html)
        latencies.append((time.perf_counter() - started) * 1000)
        for field in FIELDS:
            expected_id = item.annotation.labels[field]
            predicted_id = predicted[field]
            baseline_id = heuristic_select(item.page, field)
            for bucket, actual in (
                (counts[field], predicted_id),
                (baseline_counts[field], baseline_id),
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
                    "MISSING" if expected_id is None else item.page.candidate(expected_id).get_text(" ", strip=True)[:1000]
                )
                predicted_text = (
                    "MISSING" if predicted_id is None else item.page.candidate(predicted_id).get_text(" ", strip=True)[:1000]
                )
                failures.append(
                    f"<section><h2>{escape(item.record.page_id)} — {field.value}</h2>"
                    f"<p><a href='{escape(item.record.url)}'>{escape(item.record.url)}</a></p>"
                    f"<h3>Expected node {expected_id}</h3><pre>{escape(expected_text)}</pre>"
                    f"<h3>Predicted node {predicted_id}</h3><pre>{escape(predicted_text)}</pre></section>"
                )
    source_size = sum(path.stat().st_size for path in Path(__file__).parent.glob("*.py"))
    checkpoint_size = checkpoint.stat().st_size
    runtime_size = sum(
        _tree_size(Path(module.__file__).parent)
        for module in (torch, np, bs4)
        if module.__file__ is not None
    )
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_rss_bytes = int(peak_rss if sys.platform == "darwin" else peak_rss * 1024)
    report: dict[str, object] = {
        "scope": "held-out test websites only",
        "test_pages": len(pages),
        "fields": {field.value: _finish(counts[field]) for field in FIELDS},
        "heuristic_baseline": {
            field.value: _finish(baseline_counts[field]) for field in FIELDS
        },
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
            "p95_page_latency_ms": sorted(latencies)[int(0.95 * (len(latencies) - 1))] if latencies else None,
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
    args = parser.parse_args(argv)
    print(json.dumps(evaluate(args.dataset_dir, args.checkpoint, args.output_dir), indent=2))


if __name__ == "__main__":
    main()
