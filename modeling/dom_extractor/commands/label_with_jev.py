"""Use Jev to seed editable annotations in the core extraction dataset."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any, Sequence

from dotenv import load_dotenv
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from eng_universe.extraction.contract import FIELDS
from modeling.dom_extractor.labeler_bot import LabelerBot, save_json
from modeling.dom_extractor.manifest import DatasetManifest, PageRecord


MODELING_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATASET_DIR = REPOSITORY_ROOT / "data/learned_extraction/raw"


def _select_records(
    records: Sequence[PageRecord],
    *,
    splits: set[str],
    page_ids: Sequence[str],
    limit: int,
    include_listings: bool = False,
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
                if (include_listings or record.is_article) and record.split == split
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
    if not include_listings and any(not record.is_article for record in selected):
        raise ValueError(
            "listing pages require --include-listings; Jev should mark their fields missing"
        )
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
    parser.add_argument(
        "--include-listings",
        action="store_true",
        help=(
            "Include listing-page negatives. Without this flag, only article pages "
            "are sent to Jev."
        ),
    )
    parser.add_argument("--page-id", action="append", default=[])
    parser.add_argument("--model", default="jev-1.13.0")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Maximum concurrent Jev requests. Default: 5; maximum: 32.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Retries for rate limits, server failures, and connection errors.",
    )
    parser.add_argument(
        "--audit-dir",
        type=Path,
        help="Jev audit output. Default: <dataset-dir>/jev_annotations.",
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


async def _run(args: argparse.Namespace) -> int:
    if args.limit < 0:
        raise SystemExit("--limit must be zero or positive")
    if not 1 <= args.concurrency <= 32:
        raise SystemExit("--concurrency must be between 1 and 32")
    if args.max_retries < 0:
        raise SystemExit("--max-retries must be zero or positive")
    load_dotenv(MODELING_ROOT / ".env")
    manifest = DatasetManifest.load(args.dataset_dir / "manifest.json")
    try:
        records = _select_records(
            manifest.pages,
            splits=set(args.split or ("train", "validation", "test")),
            page_ids=args.page_id,
            limit=args.limit,
            include_listings=args.include_listings,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error

    audit_dir = args.audit_dir or args.dataset_dir / "jev_annotations"
    bot = LabelerBot(model=args.model)
    jobs: list[tuple[int, PageRecord, dict[str, Any] | None, Path | None]] = []
    for index, record in enumerate(records, start=1):
        audit_path = audit_dir / f"{record.page_id}.json"
        result = None
        reused_path = None
        if not args.overwrite_bot:
            result, reused_path = _load_cached((audit_path,), record)
        jobs.append((index, record, result, reused_path))

    needs_api = any(result is None for _, _, result, _ in jobs)
    if needs_api and not os.environ.get("TYPESAFE_API_KEY"):
        raise SystemExit(
            "TYPESAFE_API_KEY is required because at least one page has no cached result"
        )

    semaphore = asyncio.Semaphore(args.concurrency)
    failures: list[str] = []

    async def process(
        job: tuple[int, PageRecord, dict[str, Any] | None, Path | None],
        client: AsyncTypeSafeClient | None,
    ) -> None:
        index, record, result, reused_path = job
        audit_path = audit_dir / f"{record.page_id}.json"
        try:
            if result is None:
                assert client is not None
                async with semaphore:
                    html = await asyncio.to_thread(
                        (args.dataset_dir / record.html_path).read_text,
                        encoding="utf-8",
                    )
                    prepared = await asyncio.to_thread(bot.prepare, html)
                    result = await bot.label_prepared_async(
                        prepared, record, client=client
                    )
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
            failures.append(record.page_id)
            print(f"[{index}/{len(records)}] {record.page_id}: ERROR {error}")

    if needs_api:
        retry = RetryPolicy(
            max_retries=args.max_retries,
            http_statuses={429, 500, 502, 503, 504},
            respect_retry_after=True,
            timeout=120.0,
        )
        async with AsyncTypeSafeClient(model=args.model, retry=retry) as client:
            await asyncio.gather(*(process(job, client) for job in jobs))
    else:
        await asyncio.gather(*(process(job, None) for job in jobs))

    print(f"\nJev audit data: {audit_dir.resolve()}")
    print("Review and edit: http://127.0.0.1:8765/")
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_run(build_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
