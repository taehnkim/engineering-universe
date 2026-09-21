"""Build one normalized feature matrix per annotated page."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from modeling.dom_extractor.dataset import prepare_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=Path("data/learned_extraction/raw"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/learned_extraction/prepared"))
    parser.add_argument(
        "--include-jev-drafts",
        action="store_true",
        help="Include Jev-backed drafts as pseudo-labels. Human labels stay authoritative.",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            prepare_dataset(
                args.dataset_dir,
                args.output_dir,
                include_jev_drafts=args.include_jev_drafts,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
