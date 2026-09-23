"""Fine-tune the author refiner on broad-parent mistakes without changing runtime features."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import numpy as np
import torch

from eng_universe.extraction.author_boundary import (
    BOUNDARY_VERSION,
    AuthorBoundaryRefiner,
    select_boundary,
)
from eng_universe.extraction.inference import DOMExtractor
from modeling.dom_extractor.commands.train_author_boundary import (
    Sample,
    _covered,
    _train_epoch,
    collect_samples,
)

HARD_PARENT_WEIGHTS = (0.25, 1.0)
CHANGE_MARGINS = (0.0, 0.25, 0.5)
EPOCHS = 12


def predictions(
    samples: list[Sample],
    ranker: torch.nn.Module,
    mean: np.ndarray,
    std: np.ndarray,
    margin: float,
) -> dict[str, int | None]:
    return {
        sample.page_id: (
            select_boundary(
                sample.candidates, sample.baseline, ranker, mean, std, margin
            )
            if sample.candidates is not None
            else sample.baseline
        )
        for sample in samples
    }


def compare(
    samples: list[Sample],
    reference: dict[str, int | None],
    candidate: dict[str, int | None],
) -> dict[str, object]:
    present = [sample for sample in samples if sample.expected is not None]
    fixed = [
        sample.page_id
        for sample in samples
        if reference[sample.page_id] != sample.expected
        and candidate[sample.page_id] == sample.expected
    ]
    broken = [
        sample.page_id
        for sample in samples
        if reference[sample.page_id] == sample.expected
        and candidate[sample.page_id] != sample.expected
    ]
    return {
        "pages": len(samples),
        "present_pages": len(present),
        "reference_exact": sum(
            reference[sample.page_id] == sample.expected for sample in samples
        ),
        "candidate_exact": sum(
            candidate[sample.page_id] == sample.expected for sample in samples
        ),
        "reference_present_exact": sum(
            reference[sample.page_id] == sample.expected for sample in present
        ),
        "candidate_present_exact": sum(
            candidate[sample.page_id] == sample.expected for sample in present
        ),
        "hard_parent_pages": sum(bool(sample.hard_parent_ids) for sample in samples),
        "fixed": fixed,
        "broken": broken,
    }


def by_site(
    samples: list[Sample],
    reference: dict[str, int | None],
    candidate: dict[str, int | None],
) -> dict[str, dict[str, object]]:
    grouped: dict[str, list[Sample]] = defaultdict(list)
    for sample in samples:
        grouped[sample.website].append(sample)
    return {
        site: compare(grouped[site], reference, candidate)
        for site in sorted(grouped)
    }


def promotion_gate(report: dict[str, object]) -> list[str]:
    """Require unseen-site gains and no hidden site-level regression."""

    failures: list[str] = []
    validation = report["by_split"]["validation"]
    test = report["by_split"]["test"]
    if validation["candidate_exact"] <= validation["reference_exact"]:
        failures.append("validation author exact-node accuracy did not improve")
    if validation["candidate_present_exact"] <= validation["reference_present_exact"]:
        failures.append("validation present-author accuracy did not improve")
    if test["candidate_exact"] <= test["reference_exact"]:
        failures.append("held-out website author accuracy did not improve")
    if test["candidate_present_exact"] <= test["reference_present_exact"]:
        failures.append("held-out website present-author accuracy did not improve")
    for split in ("validation", "test"):
        for site, row in report["by_site"][split].items():
            if row["candidate_exact"] < row["reference_exact"]:
                failures.append(f"{split} website regressed: {site}")
    return failures


def run(
    dataset_dir: Path,
    base_checkpoint: Path,
    reference_checkpoint: Path,
    output_dir: Path,
) -> dict[str, object]:
    torch.set_num_threads(1)
    torch.manual_seed(17)
    np.random.seed(17)
    base = DOMExtractor(base_checkpoint)
    reference = AuthorBoundaryRefiner(reference_checkpoint, base_checkpoint)
    samples = collect_samples(dataset_dir, base)
    by_split = {
        split: [sample for sample in samples if sample.split == split]
        for split in ("train", "validation", "test")
    }
    train = _covered(by_split["train"])
    if not train or not by_split["validation"] or not by_split["test"]:
        raise ValueError("train, validation, and held-out test pages are required")
    reference_predictions = predictions(
        samples, reference.ranker, reference.mean, reference.std,
        reference.change_margin,
    )
    # Mine actual parent-over-child errors, not every page that has a broad ancestor.
    hard_train = [
        replace(
            sample,
            hard_parent_ids=(reference_predictions[sample.page_id],),
        )
        for sample in train
        if reference_predictions[sample.page_id] in sample.hard_parent_ids
        and reference_predictions[sample.page_id] != sample.expected
    ]
    if not hard_train:
        raise ValueError("no train pages have a predicted broad parent over gold")
    best_key: tuple[int, int, int] | None = None
    best_state = None
    best_config: dict[str, float | int] = {}
    history: list[dict[str, object]] = []
    for weight in HARD_PARENT_WEIGHTS:
        ranker = copy.deepcopy(reference.ranker)
        optimizer = torch.optim.AdamW(
            ranker.parameters(), lr=0.0001, weight_decay=0.01
        )
        rng = random.Random(17)
        for epoch in range(1, EPOCHS + 1):
            loss = _train_epoch(
                ranker, hard_train, reference.mean, reference.std, optimizer, rng,
                hard_parent_weight=weight,
            )
            for margin in CHANGE_MARGINS:
                candidate = predictions(
                    by_split["validation"], ranker,
                    reference.mean, reference.std, margin,
                )
                metrics = compare(
                    by_split["validation"], reference_predictions, candidate
                )
                entry = {
                    "weight": weight,
                    "epoch": epoch,
                    "margin": margin,
                    "train_loss": loss,
                    "validation_exact": metrics["candidate_exact"],
                    "validation_present_exact": metrics["candidate_present_exact"],
                    "validation_fixed": len(metrics["fixed"]),
                    "validation_broken": len(metrics["broken"]),
                }
                history.append(entry)
                key = (
                    int(metrics["candidate_exact"]),
                    -len(metrics["broken"]),
                    int(metrics["candidate_present_exact"]),
                )
                if best_key is None or key > best_key:
                    best_key = key
                    best_state = copy.deepcopy(ranker.state_dict())
                    best_config = {"weight": weight, "epoch": epoch, "margin": margin}
            print(
                f"weight {weight:.2f}, epoch {epoch}: train loss {loss:.3f}, "
                f"best validation exact {best_key[0]}",
                flush=True,
            )
    ranker = copy.deepcopy(reference.ranker)
    ranker.load_state_dict(best_state)
    ranker.eval()
    candidate_predictions = predictions(
        samples, ranker, reference.mean, reference.std,
        float(best_config["margin"]),
    )
    report: dict[str, object] = {
        "experiment": "author-parent-hard-negatives-v1",
        "base_sha256": hashlib.sha256(base_checkpoint.read_bytes()).hexdigest(),
        "reference_sha256": hashlib.sha256(reference_checkpoint.read_bytes()).hexdigest(),
        "reference_margin": reference.change_margin,
        "selected": best_config,
        "train_covered_pages": len(train),
        "mined_parent_mistakes": [sample.page_id for sample in hard_train],
        "by_split": {
            split: compare(rows, reference_predictions, candidate_predictions)
            for split, rows in by_split.items()
        },
        "by_site": {
            split: by_site(rows, reference_predictions, candidate_predictions)
            for split, rows in by_split.items()
        },
        "history": history,
    }
    report["promotion_gate_failures"] = promotion_gate(report)
    report["promote"] = not report["promotion_gate_failures"]
    output_dir.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "version": BOUNDARY_VERSION,
            "base_sha256": report["base_sha256"],
            "feature_count": len(reference.mean),
            "mean": reference.mean,
            "std": reference.std,
            "change_margin": float(best_config["margin"]),
            "model_state": ranker.state_dict(),
        },
        output_dir / "author_boundary.pt",
    )
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--base-checkpoint", type=Path, required=True)
    parser.add_argument("--reference-checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run(
        args.dataset_dir, args.base_checkpoint, args.reference_checkpoint,
        args.output_dir,
    )
    for split, counts in report["by_split"].items():
        print(
            f"{split}: authors {counts['reference_exact']}/{counts['pages']} -> "
            f"{counts['candidate_exact']}/{counts['pages']} "
            f"(fixed {len(counts['fixed'])}, broken {len(counts['broken'])})"
        )
    print(f"promotion gate: {'PASS' if report['promote'] else 'FAIL'}")
    for failure in report["promotion_gate_failures"]:
        print(f"  - {failure}")
    print(f"report: {args.output_dir / 'report.json'}")


if __name__ == "__main__":
    main()
