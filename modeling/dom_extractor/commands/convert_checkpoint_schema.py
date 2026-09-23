"""Project a trained six-field checkpoint onto the four retained output heads.

The shared encoder and the retained output rows remain byte-for-byte equivalent
as tensors. This prevents a schema-only change from silently degrading the four
existing decisions. Train a matching author-boundary artifact after conversion.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
from pathlib import Path

import torch

from eng_universe.extraction.contract import FIELDS

OLD_FIELDS = ["article", "title", "authors", "date", "summary", "relative_date"]


def convert(
    source: Path, destination: Path,
    source_refiner: Path | None = None, destination_refiner: Path | None = None,
) -> None:
    if source.resolve() == destination.resolve():
        raise ValueError("output checkpoint must differ from the source")
    if source_refiner is not None and destination_refiner is not None:
        if source_refiner.resolve() == destination_refiner.resolve():
            raise ValueError("output refiner must differ from the source")
    checkpoint = torch.load(source, map_location="cpu", weights_only=False)
    if checkpoint["fields"] != OLD_FIELDS:
        raise ValueError(f"expected {OLD_FIELDS}, got {checkpoint['fields']}")
    result = copy.deepcopy(checkpoint)
    count = len(FIELDS)
    state = result["model_state"]
    for name in ("missing_scores", "network.4.weight", "network.4.bias"):
        state[name] = state[name][:count].clone()
    result["fields"] = [field.value for field in FIELDS]
    result["preprocessing"]["fields"] = result["fields"]
    result["conversion"] = {
        "kind": "retain-trained-output-heads",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "note": "Four retained logits are unchanged; discarded heads are not scored.",
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    torch.save(result, destination)
    if (source_refiner is None) != (destination_refiner is None):
        raise ValueError("source and output refiner paths must be specified together")
    if source_refiner is not None and destination_refiner is not None:
        refiner = torch.load(source_refiner, map_location="cpu", weights_only=False)
        if refiner["base_sha256"] != hashlib.sha256(source.read_bytes()).hexdigest():
            raise ValueError("source refiner does not match source base checkpoint")
        refiner["base_sha256"] = hashlib.sha256(destination.read_bytes()).hexdigest()
        refiner["conversion"] = {
            "kind": "retie-unchanged-ranker",
            "source_sha256": hashlib.sha256(source_refiner.read_bytes()).hexdigest(),
        }
        destination_refiner.parent.mkdir(parents=True, exist_ok=True)
        torch.save(refiner, destination_refiner)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-refiner", type=Path)
    parser.add_argument("--output-refiner", type=Path)
    args = parser.parse_args()
    convert(args.source, args.output, args.source_refiner, args.output_refiner)
    print(args.output)


if __name__ == "__main__":
    main()
