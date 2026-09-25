"""Apply the frozen website split for newly human-reviewed article pages.

Only pages from the plan's source prefix move. The original train, validation,
and test websites remain in their old splits. Run without --apply to preview.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

from eng_universe.extraction.contract import load_annotation
from modeling.dom_extractor.manifest import DatasetManifest, PageRecord

DEFAULT_PLAN = Path(__file__).resolve().parents[1] / "splits/reviewed-2026-09-25.json"


def rebalance(
    manifest: DatasetManifest,
    dataset_dir: Path,
    plan: dict[str, object],
) -> tuple[DatasetManifest, dict[str, object]]:
    if plan.get("version") != 1:
        raise ValueError("unsupported split plan version")
    prefix = str(plan["sourceIdPrefix"])
    sites = plan["sites"]
    if not isinstance(sites, dict):
        raise TypeError("split plan sites must be an object")
    new_sites: dict[str, list[PageRecord]] = defaultdict(list)
    for record in manifest.pages:
        if record.source_id.startswith(prefix):
            new_sites[record.website].append(record)
    if set(new_sites) != set(sites):
        raise ValueError(
            "split plan websites differ from new data: "
            f"missing={sorted(set(sites) - set(new_sites))}, "
            f"extra={sorted(set(new_sites) - set(sites))}"
        )
    for website, records in new_sites.items():
        site_plan = sites[website]
        if not isinstance(site_plan, dict):
            raise TypeError(f"invalid site plan: {website}")
        if len(records) != site_plan["pages"]:
            raise ValueError(f"page count changed for {website}")
        for record in records:
            annotation_path = dataset_dir / "annotations" / f"{record.page_id}.json"
            annotation = load_annotation(annotation_path)
            if (
                annotation.review_status != "reviewed"
                or annotation.needs_review
                or annotation.html_hash != record.html_hash
            ):
                raise ValueError(f"{record.page_id} is not a current human review")
    rewritten = tuple(
        replace(record, split=sites[record.website]["split"])
        if record.source_id.startswith(prefix)
        else record
        for record in manifest.pages
    )
    result = DatasetManifest(manifest.version, rewritten)
    result.validate()
    counts = dict(Counter(record.split for record in rewritten))
    if counts != plan["expectedFinalPages"]:
        raise ValueError(f"split totals {counts} differ from the frozen plan")
    changed = sum(
        before.split != after.split
        for before, after in zip(manifest.pages, rewritten, strict=True)
    )
    return result, {
        "pages": len(rewritten),
        "split_pages": counts,
        "new_websites": len(new_sites),
        "moved_pages": changed,
    }


def apply_plan(
    dataset_dir: Path,
    plan_path: Path,
    backup_dir: Path,
    *,
    apply: bool,
) -> dict[str, object]:
    manifest_path = dataset_dir / "manifest.json"
    original_bytes = manifest_path.read_bytes()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    result, summary = rebalance(DatasetManifest.load(manifest_path), dataset_dir, plan)
    summary["plan_sha256"] = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    summary["applied"] = bool(apply and summary["moved_pages"])
    if not summary["applied"]:
        return summary
    backup_dir.mkdir(parents=True, exist_ok=True)
    old_hash = hashlib.sha256(original_bytes).hexdigest()
    backup_path = backup_dir / f"manifest-before-rebalance-{old_hash[:12]}.json"
    if backup_path.exists():
        if backup_path.read_bytes() != original_bytes:
            raise ValueError(f"backup path has different content: {backup_path}")
    else:
        backup_path.write_bytes(original_bytes)
    summary["manifest_backup"] = str(backup_path)
    with tempfile.NamedTemporaryFile(
        prefix=".manifest-rebalance-", suffix=".json", dir=dataset_dir, delete=False
    ) as handle:
        temp_path = Path(handle.name)
    try:
        result.save(temp_path)
        os.replace(temp_path, manifest_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-dir", type=Path, default=Path("data/learned_extraction/raw")
    )
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument(
        "--backup-dir", type=Path, default=Path("data/learned_extraction/backups")
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            apply_plan(args.dataset_dir, args.plan, args.backup_dir, apply=args.apply),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
