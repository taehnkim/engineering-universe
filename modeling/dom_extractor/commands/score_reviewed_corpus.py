"""Score exact node selection for one reviewed dataset split.

Run on validation while choosing a checkpoint. Run on test only after that
choice is frozen. A custom base checkpoint has no author refiner by default.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from eng_universe.extraction.contract import FIELDS
from eng_universe.extraction.inference import (
    DEFAULT_AUTHOR_BOUNDARY_CHECKPOINT,
    DEFAULT_CHECKPOINT,
    DOMExtractor,
)
from modeling.dom_extractor.dataset import iter_labeled_pages


def score(
    dataset_dir: Path,
    split: str,
    checkpoint: Path = DEFAULT_CHECKPOINT,
    author_boundary_checkpoint: Path | None = None,
) -> dict[str, object]:
    if split not in {"train", "validation", "test"}:
        raise ValueError(f"unknown split: {split}")
    extractor = DOMExtractor(
        checkpoint, author_boundary_checkpoint=author_boundary_checkpoint
    )
    counts: dict[str, Counter[str]] = {field.value: Counter() for field in FIELDS}
    websites: set[str] = set()
    pages = 0
    for item in iter_labeled_pages(dataset_dir, split):
        pages += 1
        websites.add(item.record.website)
        predicted = extractor.predict_ids(item.page.original_html)
        for field in FIELDS:
            expected = item.annotation.labels[field]
            selected = predicted[field]
            values = counts[field.value]
            values["total"] += 1
            values["correct"] += selected == expected
            values["present"] += expected is not None
            values["present_correct"] += expected is not None and selected == expected
    return {
        "split": split,
        "pages": pages,
        "websites": len(websites),
        "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        "author_boundary_sha256": (
            hashlib.sha256(author_boundary_checkpoint.read_bytes()).hexdigest()
            if author_boundary_checkpoint is not None
            else None
        ),
        "fields": {
            field: {
                "correct": values["correct"],
                "total": values["total"],
                "exact_accuracy": values["correct"] / max(1, values["total"]),
                "present_correct": values["present_correct"],
                "present": values["present"],
                "present_accuracy": values["present_correct"]
                / max(1, values["present"]),
            }
            for field, values in counts.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-dir", type=Path, default=Path("data/learned_extraction/raw")
    )
    parser.add_argument(
        "--split", choices=("train", "validation", "test"), required=True
    )
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--author-boundary-checkpoint", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    boundary = args.author_boundary_checkpoint
    if boundary is None and args.checkpoint == DEFAULT_CHECKPOINT:
        boundary = DEFAULT_AUTHOR_BOUNDARY_CHECKPOINT
    report = score(args.dataset_dir, args.split, args.checkpoint, boundary)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
