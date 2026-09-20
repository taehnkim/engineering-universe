"""Use Jev to seed editable annotations in the core extraction dataset."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Sequence

from dotenv import load_dotenv

from eng_universe.extraction.contract import FIELDS
from eng_universe.extraction.labeler_bot import LabelerBot, save_json
from eng_universe.extraction.manifest import DatasetManifest, PageRecord


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_DIR = ROOT / "data/learned_extraction/raw"
DEFAULT_CACHE_DIR = ROOT / "labeler-bot/data/annotations"


def _select_records(
    records: Sequence[PageRecord],
    *,
    splits: set[str],
    page_ids: Sequence[str],
    limit: int,
) -> list[PageRecord]:
    by_id = {record.page_id: record for record in records}
    if page_ids:
        missing = [page_id for page_id in page_ids if page_id not in by_id]
        if missing:
            raise ValueError(f"unknown page IDs: {', '.join(missing)}")
        selected = [by_id[page_id] for page_id in page_ids]
    else:
        buckets = {
            split: [
                record
                for record in records
                if record.is_article and record.split == split
            ]
            for split in ("train", "validation", "test")
            if split in splits
        }
        selected = []
        offsets = {split: 0 for split in buckets}
        target = sum(len(bucket) for bucket in buckets.values()) if limit == 0 else limit
        while len(selected) < target:
            added = False
            for split, bucket in buckets.items():
                offset = offsets[split]
                if offset < len(bucket) and len(selected) < target:
                    selected.append(bucket[offset])
                    offsets[split] += 1
                    added = True
            if not added:
                break
    if any(not record.is_article for record in selected):
        raise ValueError("listing pages cannot be labeled as articles")
    return selected[:limit] if limit else selected


def _load_cached(
    paths: Sequence[Path], record: PageRecord
) -> tuple[dict[str, Any] | None, Path | None]:
    for path in paths:
        if not path.exists():
            continue
        result = json.loads(path.read_text(encoding="utf-8"))
        if result.get("page_id") != record.page_id:
            continue
        if result.get("html_hash") != record.html_hash:
            continue
        if set(result.get("labels", {})) != {field.value for field in FIELDS}:
            continue
        return result, path
    return None, None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument(
        "--split",
        action="append",
        choices=("train", "validation", "test"),
        help="Dataset split to label. Repeat for several splits. Default: all.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum pages to label. Use 0 for every selected page.",
    )
    parser.add_argument("--page-id", action="append", default=[])
    parser.add_argument("--model", default="jev-1.13.0")
    parser.add_argument(
        "--audit-dir",
        type=Path,
        help="Jev audit output. Default: <dataset-dir>/jev_annotations.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help="Optional previous Jev results to reuse without an API call.",
    )
    parser.add_argument(
        "--overwrite-bot",
        action="store_true",
        help="Call Jev again even when a matching audit result exists.",
    )
    parser.add_argument(
        "--overwrite-reviewed",
        action="store_true",
        help="Replace a human-reviewed core annotation with a Jev draft.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit < 0:
        raise SystemExit("--limit must be zero or positive")
    load_dotenv(ROOT / "labeler-bot/.env")
    manifest = DatasetManifest.load(args.dataset_dir / "manifest.json")
    try:
        records = _select_records(
            manifest.pages,
            splits=set(args.split or ("train", "validation", "test")),
            page_ids=args.page_id,
            limit=args.limit,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error

    audit_dir = args.audit_dir or args.dataset_dir / "jev_annotations"
    bot = LabelerBot(model=args.model)
    failures = 0
    for index, record in enumerate(records, start=1):
        audit_path = audit_dir / f"{record.page_id}.json"
        cache_paths = (audit_path, args.cache_dir / f"{record.page_id}.json")
        result = None
        reused_path = None
        if not args.overwrite_bot:
            result, reused_path = _load_cached(cache_paths, record)
        try:
            if result is None:
                if not os.environ.get("TYPESAFE_API_KEY"):
                    raise RuntimeError(
                        "TYPESAFE_API_KEY is required for pages without a cached result"
                    )
                html = (args.dataset_dir / record.html_path).read_text(encoding="utf-8")
                result = bot.label(html, record)
                source = "Jev API"
            else:
                source = f"cache {reused_path}"
            save_json(audit_path, result)
            seeded = bot.seed_core_annotation(
                result,
                args.dataset_dir,
                overwrite_reviewed=args.overwrite_reviewed,
            )
            core_status = "seeded editable draft" if seeded else "kept human review"
            mean_confidence = sum(
                float(result["metadata"][field.value]["confidence"])
                for field in FIELDS
            ) / len(FIELDS)
            print(
                f"[{index}/{len(records)}] {record.page_id} [{record.split}] "
                f"{mean_confidence:.0%} mean confidence | {core_status} | {source}"
            )
        except Exception as error:
            failures += 1
            print(f"[{index}/{len(records)}] {record.page_id}: ERROR {error}")
    print(f"\nJev audit data: {audit_dir.resolve()}")
    print("Review and edit: http://127.0.0.1:8765/")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
