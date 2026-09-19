#!/usr/bin/env python3
"""Run Jev on ten HTML files and seed core annotations for human review."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

from jev_labeler import (  # noqa: E402
    FIELDS,
    LabelerBot,
    MAX_PAGES,
    PreparedPage,
    load_records,
    prepare_html,
    run_entry,
    save_json,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=ROOT / "data/learned_extraction/raw",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "data",
    )
    parser.add_argument("--limit", type=int, default=MAX_PAGES)
    parser.add_argument("--page-id", action="append", default=[])
    parser.add_argument("--model", default="jev-1.13.0")
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Prepare the ten inputs without calling Jev.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing bot label and call Jev again.",
    )
    parser.add_argument(
        "--separate-only",
        action="store_true",
        help="Keep Jev output separate and do not seed the core annotation drafts.",
    )
    return parser


def _print_annotation(annotation: dict[str, Any], prepared: PreparedPage) -> None:
    labels = annotation["labels"]
    metadata = annotation["metadata"]
    page = prepared.page
    for field in FIELDS:
        node_id = labels[field.value]
        confidence = metadata[field.value]["confidence"]
        if node_id is None:
            print(f"  {field.value.upper():13} missing  confidence={confidence:.0%}")
            continue
        text = " ".join(page.candidate(node_id).get_text(" ", strip=True).split())
        if len(text) > 110:
            text = text[:109] + "…"
        print(
            f"  {field.value.upper():13} node={node_id:<5} "
            f"confidence={confidence:.0%}  {text}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv(Path(__file__).resolve().parent / ".env")
    if not args.prepare_only and not os.environ.get("TYPESAFE_API_KEY"):
        print("TYPESAFE_API_KEY is not set. Add it to .env or export it.", file=sys.stderr)
        return 2

    try:
        records = load_records(args.dataset_dir, args.page_id, args.limit)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2

    output_dir: Path = args.output_dir
    bot = LabelerBot(model=args.model)
    prepared_dir = output_dir / "prepared"
    annotations_dir = output_dir / "annotations"
    entries = [run_entry(record) for record in records]
    run = {
        "version": 1,
        "model": args.model,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_dir": str(args.dataset_dir.resolve()),
        "prepare_only": args.prepare_only,
        "seed_core_annotations": not args.separate_only,
        "pages": entries,
    }
    save_json(output_dir / "run.json", run)

    failures = 0
    for index, (record, entry) in enumerate(zip(records, entries, strict=True), start=1):
        annotation_path = annotations_dir / f"{record.page_id}.json"
        print(f"[{index}/{len(records)}] {record.page_id} ({record.split})")
        try:
            html = (args.dataset_dir / record.html_path).read_text(encoding="utf-8")
            prepared = prepare_html(html)
            prepared_path = prepared_dir / f"{record.page_id}.html"
            prepared_path.parent.mkdir(parents=True, exist_ok=True)
            prepared_path.write_text(prepared.html, encoding="utf-8")
            entry["prepared_char_count"] = prepared.prepared_char_count
            entry["candidate_count"] = len(prepared.candidate_ids)
            if args.prepare_only:
                entry["status"] = "prepared"
            elif annotation_path.exists() and not args.overwrite:
                entry["status"] = "already_labeled"
                print("  kept existing bot label (use --overwrite to call Jev again)")
                annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
            else:
                annotation = bot.label_prepared(prepared, record)
                save_json(annotation_path, annotation)
                entry["status"] = "labeled"
            if not args.prepare_only:
                if args.separate_only:
                    entry["core_annotation"] = "not_requested"
                elif bot.seed_core_annotation(annotation, args.dataset_dir):
                    entry["core_annotation"] = "seeded_draft"
                else:
                    entry["core_annotation"] = "preserved_reviewed"
                _print_annotation(annotation, prepared)
                print(
                    f"  inference={annotation['latency_ms']:.1f} ms  "
                    f"core={entry['core_annotation']}"
                )
        except Exception as error:  # Keep the small batch inspectable after one failure.
            failures += 1
            entry["status"] = "error"
            entry["error"] = f"{type(error).__name__}: {error}"
            print(f"  ERROR: {entry['error']}", file=sys.stderr)
        finally:
            save_json(output_dir / "run.json", run)

    print(f"\nReview: uv run python labeler-bot/app.py --output-dir {output_dir}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
