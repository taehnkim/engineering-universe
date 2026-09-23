"""Gate a candidate DOM checkpoint against reviewed pages by split, site, and field."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import torch

from eng_universe.extraction.contract import FIELDS, Field
from eng_universe.extraction.inference import DOMExtractor
from modeling.dom_extractor.dataset import iter_labeled_pages


def _bucket() -> dict[str, object]:
    return {
        "pages": 0,
        "present_pages": 0,
        "reference_exact": 0,
        "candidate_exact": 0,
        "reference_present_exact": 0,
        "candidate_present_exact": 0,
        "fixed": [],
        "broken": [],
    }


def _record(
    bucket: dict[str, object], page_id: str, expected: int | None,
    reference: int | None, candidate: int | None,
) -> None:
    bucket["pages"] += 1
    bucket["present_pages"] += expected is not None
    bucket["reference_exact"] += reference == expected
    bucket["candidate_exact"] += candidate == expected
    bucket["reference_present_exact"] += expected is not None and reference == expected
    bucket["candidate_present_exact"] += expected is not None and candidate == expected
    if reference != expected and candidate == expected:
        bucket["fixed"].append(page_id)
    if reference == expected and candidate != expected:
        bucket["broken"].append(page_id)


def gate(report: dict[str, object]) -> list[str]:
    """A pooled improvement cannot hide a held-out site or field regression."""

    failures: list[str] = []
    for split in ("validation", "test"):
        for site, fields in report["by_site"][split].items():
            for field, row in fields.items():
                if row["candidate_exact"] < row["reference_exact"]:
                    failures.append(f"{split}/{site}/{field} exact-node regression")
                if row["candidate_present_exact"] < row["reference_present_exact"]:
                    failures.append(f"{split}/{site}/{field} present-only regression")
    validation_authors = report["by_split"]["validation"][Field.AUTHORS.value]
    test_authors = report["by_split"]["test"][Field.AUTHORS.value]
    if validation_authors["candidate_present_exact"] <= validation_authors["reference_present_exact"]:
        failures.append("validation present-author exact-node count did not improve")
    if test_authors["candidate_exact"] <= test_authors["reference_exact"]:
        failures.append("held-out test author exact-node count did not improve")
    if test_authors["candidate_present_exact"] <= test_authors["reference_present_exact"]:
        failures.append("held-out test present-author count did not improve")
    return failures


def run(
    dataset_dir: Path,
    reference_base: Path,
    reference_author: Path,
    candidate_base: Path,
    candidate_author: Path,
) -> dict[str, object]:
    torch.set_num_threads(1)
    reference = DOMExtractor(
        reference_base, author_boundary_checkpoint=reference_author
    )
    candidate = DOMExtractor(
        candidate_base, author_boundary_checkpoint=candidate_author
    )
    by_split: dict[str, dict[str, dict[str, object]]] = defaultdict(dict)
    by_site: dict[str, dict[str, dict[str, dict[str, object]]]] = defaultdict(dict)
    count = 0
    for item in iter_labeled_pages(dataset_dir):
        count += 1
        split = item.record.split
        site = item.record.website
        old = reference.predict_page(item.page)
        new = candidate.predict_page(item.page)
        site_rows = by_site[split].setdefault(site, {})
        for field in FIELDS:
            field_name = field.value
            total = by_split[split].setdefault(field_name, _bucket())
            group = site_rows.setdefault(field_name, _bucket())
            for bucket in (total, group):
                _record(
                    bucket, item.record.page_id, item.annotation.labels[field],
                    old[field], new[field],
                )
        if count % 100 == 0:
            print(f"compared {count} pages", flush=True)
    report: dict[str, object] = {
        "pages": count,
        "reference_base_sha256": hashlib.sha256(reference_base.read_bytes()).hexdigest(),
        "candidate_base_sha256": hashlib.sha256(candidate_base.read_bytes()).hexdigest(),
        "reference_author_sha256": hashlib.sha256(reference_author.read_bytes()).hexdigest(),
        "candidate_author_sha256": hashlib.sha256(candidate_author.read_bytes()).hexdigest(),
        "by_split": dict(by_split),
        "by_site": {split: dict(sites) for split, sites in by_site.items()},
    }
    report["promotion_gate_failures"] = gate(report)
    report["promote"] = not report["promotion_gate_failures"]
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--reference-base", type=Path, required=True)
    parser.add_argument("--reference-author", type=Path, required=True)
    parser.add_argument("--candidate-base", type=Path, required=True)
    parser.add_argument("--candidate-author", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(
        args.dataset_dir, args.reference_base, args.reference_author,
        args.candidate_base, args.candidate_author,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for split, fields in report["by_split"].items():
        print(split)
        for field, row in fields.items():
            print(
                f"  {field}: {row['reference_exact']}/{row['pages']} -> "
                f"{row['candidate_exact']}/{row['pages']}"
            )
    print(f"promotion gate: {'PASS' if report['promote'] else 'FAIL'}")
    for failure in report["promotion_gate_failures"]:
        print(f"  - {failure}")
    print(f"report: {args.output}")


if __name__ == "__main__":
    main()
