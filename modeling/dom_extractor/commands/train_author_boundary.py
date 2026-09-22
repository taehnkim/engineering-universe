"""Train and evaluate a local author boundary ranker around a frozen checkpoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from torch import nn

from eng_universe.extraction.author_boundary import (
    AUTHOR_FIELD_INDEX,
    BOUNDARY_VERSION,
    AuthorBoundaryRanker,
    BoundaryFeatures,
    boundary_features,
    select_boundary,
)
from eng_universe.extraction.contract import Field
from eng_universe.extraction.dom import ParsedPage
from eng_universe.extraction.features import featurize_page
from eng_universe.extraction.inference import DOMExtractor
from modeling.dom_extractor.dataset import iter_labeled_pages

MARGINS = (0.0, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0)


@dataclass(frozen=True)
class Sample:
    page_id: str
    split: str
    expected: int | None
    baseline: int | None
    candidates: BoundaryFeatures | None


def _baseline_author(
    extractor: DOMExtractor, page: ParsedPage
) -> tuple[int | None, np.ndarray, np.ndarray]:
    features = featurize_page(
        page, extractor.vocabulary, extractor.semantic_vocabulary, extractor.normalizer
    )
    if not page.candidates:
        return None, np.empty(0, dtype=np.float32), features.numeric
    with torch.inference_mode():
        scores = extractor.model(
            torch.from_numpy(features.tag_ids).unsqueeze(0),
            torch.from_numpy(features.parent_tag_ids).unsqueeze(0),
            torch.from_numpy(features.grandparent_tag_ids).unsqueeze(0),
            torch.from_numpy(features.previous_tag_ids).unsqueeze(0),
            torch.from_numpy(features.next_tag_ids).unsqueeze(0),
            torch.from_numpy(features.attribute_token_ids).unsqueeze(0),
            torch.from_numpy(features.text_shape_token_ids).unsqueeze(0),
            torch.from_numpy(features.numeric).unsqueeze(0),
            torch.ones((1, len(page.candidates)), dtype=torch.bool),
        )
    author_scores = scores[0, :-1, AUTHOR_FIELD_INDEX].numpy()
    chosen_index = int(scores[0, :, AUTHOR_FIELD_INDEX].argmax())
    chosen = (
        None
        if chosen_index == len(page.candidates)
        else int(features.node_ids[chosen_index])
    )
    return chosen, author_scores, features.numeric


def collect_samples(dataset_dir: Path, extractor: DOMExtractor) -> list[Sample]:
    samples: list[Sample] = []
    for index, item in enumerate(iter_labeled_pages(dataset_dir), start=1):
        baseline, author_scores, numeric = _baseline_author(extractor, item.page)
        candidates = (
            boundary_features(item.page, baseline, author_scores, numeric)
            if baseline is not None
            else None
        )
        samples.append(
            Sample(
                item.record.page_id,
                item.record.split,
                item.annotation.labels[Field.AUTHORS],
                baseline,
                candidates,
            )
        )
        if index % 100 == 0:
            print(f"prepared {index} pages", flush=True)
    return samples


def _covered(samples: list[Sample]) -> list[Sample]:
    return [
        sample
        for sample in samples
        if sample.expected is not None
        and sample.candidates is not None
        and sample.expected in sample.candidates.node_ids
    ]


def _normalizer(samples: list[Sample]) -> tuple[np.ndarray, np.ndarray]:
    values = np.concatenate([sample.candidates.values for sample in samples])
    mean = values.mean(axis=0)
    std = values.std(axis=0)
    std[std < 1e-4] = 1.0
    return mean, std


def _normalized(sample: Sample, mean: np.ndarray, std: np.ndarray) -> torch.Tensor:
    return torch.from_numpy(
        np.clip((sample.candidates.values - mean) / std, -6.0, 6.0).astype(np.float32)
    )


def _train_epoch(
    ranker: AuthorBoundaryRanker,
    samples: list[Sample],
    mean: np.ndarray,
    std: np.ndarray,
    optimizer: torch.optim.Optimizer,
    rng: random.Random,
) -> float:
    ranker.train()
    order = samples.copy()
    rng.shuffle(order)
    losses: list[float] = []
    for start in range(0, len(order), 8):
        group = order[start : start + 8]
        matrices = [_normalized(sample, mean, std) for sample in group]
        max_candidates = max(len(matrix) for matrix in matrices)
        batch = torch.zeros((len(group), max_candidates, matrices[0].shape[1]))
        mask = torch.zeros((len(group), max_candidates), dtype=torch.bool)
        targets = torch.empty(len(group), dtype=torch.long)
        for i, (sample, matrix) in enumerate(zip(group, matrices, strict=True)):
            batch[i, : len(matrix)] = matrix
            mask[i, : len(matrix)] = True
            targets[i] = sample.candidates.node_ids.index(sample.expected)
        scores = ranker(batch).masked_fill(~mask, -1e9)
        loss = nn.functional.cross_entropy(scores, targets)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        losses.append(float(loss.detach()))
    return sum(losses) / max(1, len(losses))


def _validation_loss(
    ranker: AuthorBoundaryRanker,
    samples: list[Sample],
    mean: np.ndarray,
    std: np.ndarray,
) -> float:
    ranker.eval()
    losses: list[float] = []
    with torch.inference_mode():
        for sample in samples:
            scores = ranker(_normalized(sample, mean, std))
            target = sample.candidates.node_ids.index(sample.expected)
            losses.append(
                float(nn.functional.cross_entropy(scores[None], torch.tensor([target])))
            )
    return sum(losses) / max(1, len(losses))


def evaluate(
    ranker: AuthorBoundaryRanker,
    samples: list[Sample],
    mean: np.ndarray,
    std: np.ndarray,
    margin: float,
) -> dict[str, object]:
    counts: Counter[str] = Counter()
    for sample in samples:
        selected = sample.baseline
        if sample.candidates is not None:
            selected = select_boundary(
                sample.candidates, sample.baseline, ranker, mean, std, margin
            )
        counts["pages"] += 1
        counts["baseline_correct"] += sample.baseline == sample.expected
        counts["refined_correct"] += selected == sample.expected
        counts["changed"] += selected != sample.baseline
        counts["fixed"] += (
            selected == sample.expected and sample.baseline != sample.expected
        )
        counts["broken"] += (
            selected != sample.expected and sample.baseline == sample.expected
        )
        counts["present"] += sample.expected is not None
        counts["present_correct"] += (
            sample.expected is not None and selected == sample.expected
        )
        counts["present_baseline_correct"] += (
            sample.expected is not None and sample.baseline == sample.expected
        )
        counts["fixable"] += (
            sample.baseline != sample.expected
            and sample.expected is not None
            and sample.candidates is not None
            and sample.expected in sample.candidates.node_ids
        )
    return {
        **counts,
        "baseline_accuracy": counts["baseline_correct"] / max(1, counts["pages"]),
        "refined_accuracy": counts["refined_correct"] / max(1, counts["pages"]),
        "present_baseline_accuracy": counts["present_baseline_correct"]
        / max(1, counts["present"]),
        "present_refined_accuracy": counts["present_correct"]
        / max(1, counts["present"]),
    }


def run(
    dataset_dir: Path, base_checkpoint: Path, output_dir: Path
) -> dict[str, object]:
    torch.manual_seed(17)
    np.random.seed(17)
    rng = random.Random(17)
    extractor = DOMExtractor(base_checkpoint)
    samples = collect_samples(dataset_dir, extractor)
    by_split = {
        split: [sample for sample in samples if sample.split == split]
        for split in ("train", "validation", "test")
    }
    train_samples = _covered(by_split["train"])
    validation_samples = _covered(by_split["validation"])
    mean, std = _normalizer(train_samples)
    ranker = AuthorBoundaryRanker(len(mean))
    optimizer = torch.optim.AdamW(ranker.parameters(), lr=0.003, weight_decay=0.01)
    best_validation_loss = float("inf")
    best_state = None
    best_epoch = 0
    history = []
    for epoch in range(1, 31):
        train_loss = _train_epoch(ranker, train_samples, mean, std, optimizer, rng)
        validation_loss = _validation_loss(ranker, validation_samples, mean, std)
        history.append(
            {
                "epoch": epoch,
                "train_loss": train_loss,
                "validation_loss": validation_loss,
            }
        )
        print(
            f"epoch {epoch}: train {train_loss:.3f}, validation {validation_loss:.3f}",
            flush=True,
        )
        if validation_loss < best_validation_loss:
            best_validation_loss = validation_loss
            best_epoch = epoch
            best_state = {
                key: value.detach().clone()
                for key, value in ranker.state_dict().items()
            }
    ranker.load_state_dict(best_state)
    ranker.eval()
    validation_by_margin = {
        str(margin): evaluate(ranker, by_split["validation"], mean, std, margin)
        for margin in MARGINS
    }
    chosen_margin = max(
        MARGINS,
        key=lambda margin: (
            validation_by_margin[str(margin)]["refined_correct"],
            -validation_by_margin[str(margin)]["changed"],
        ),
    )
    report = {
        "version": BOUNDARY_VERSION,
        "base_checkpoint": str(base_checkpoint),
        "best_epoch": best_epoch,
        "best_validation_loss": best_validation_loss,
        "train_covered_pages": len(train_samples),
        "validation_covered_pages": len(validation_samples),
        "chosen_margin": chosen_margin,
        "validation_by_margin": validation_by_margin,
        "by_split": {
            split: evaluate(ranker, rows, mean, std, chosen_margin)
            for split, rows in by_split.items()
        },
        "history": history,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "version": BOUNDARY_VERSION,
            "base_sha256": hashlib.sha256(base_checkpoint.read_bytes()).hexdigest(),
            "feature_count": len(mean),
            "mean": mean,
            "std": std,
            "change_margin": chosen_margin,
            "model_state": ranker.state_dict(),
        },
        output_dir / "author_boundary.pt",
    )
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--base-checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.dataset_dir, args.base_checkpoint, args.output_dir)
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "history"}, indent=2
        )
    )


if __name__ == "__main__":
    main()
