"""Reproducible training for the structural DOM-node selector."""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

from eng_universe.extraction.contract import FIELDS, Field
from eng_universe.extraction.dom import DOM_CLEANUP_VERSION
from eng_universe.extraction.features import FEATURE_VERSION
from eng_universe.extraction.model import DOMNodeSelector

FIELD_LOSS_WEIGHTS = {
    Field.ARTICLE: 1.0,
    Field.TITLE: 1.0,
    Field.AUTHORS: 3.0,
    Field.DATE: 2.0,
    Field.SUMMARY: 1.0,
    Field.RELATIVE_DATE: 1.0,
}


@dataclass(frozen=True, slots=True)
class MatrixPage:
    page_id: str
    website: str
    tag_ids: torch.Tensor
    parent_tag_ids: torch.Tensor
    grandparent_tag_ids: torch.Tensor
    previous_tag_ids: torch.Tensor
    next_tag_ids: torch.Tensor
    semantic_token_ids: torch.Tensor
    numeric: torch.Tensor
    targets: torch.Tensor


class MatrixDataset(Dataset[MatrixPage]):
    def __init__(self, paths: Sequence[Path]) -> None:
        self.paths = tuple(paths)

    def __len__(self) -> int:
        return len(self.paths)

    def __getitem__(self, index: int) -> MatrixPage:
        with np.load(self.paths[index], allow_pickle=False) as value:
            return MatrixPage(
                page_id=str(value["page_id"]),
                website=str(value["website"]),
                tag_ids=torch.from_numpy(value["tag_ids"].copy()).long(),
                parent_tag_ids=torch.from_numpy(value["parent_tag_ids"].copy()).long(),
                grandparent_tag_ids=torch.from_numpy(
                    value["grandparent_tag_ids"].copy()
                ).long(),
                previous_tag_ids=torch.from_numpy(
                    value["previous_tag_ids"].copy()
                ).long(),
                next_tag_ids=torch.from_numpy(value["next_tag_ids"].copy()).long(),
                semantic_token_ids=torch.from_numpy(
                    value["semantic_token_ids"].copy()
                ).long(),
                numeric=torch.from_numpy(value["numeric"].copy()).float(),
                targets=torch.from_numpy(value["targets"].copy()).long(),
            )


@dataclass(frozen=True, slots=True)
class Batch:
    page_ids: tuple[str, ...]
    websites: tuple[str, ...]
    tag_ids: torch.Tensor
    parent_tag_ids: torch.Tensor
    grandparent_tag_ids: torch.Tensor
    previous_tag_ids: torch.Tensor
    next_tag_ids: torch.Tensor
    semantic_token_ids: torch.Tensor
    numeric: torch.Tensor
    mask: torch.Tensor
    targets: torch.Tensor

    def to(self, device: torch.device) -> Batch:
        return Batch(
            self.page_ids,
            self.websites,
            self.tag_ids.to(device),
            self.parent_tag_ids.to(device),
            self.grandparent_tag_ids.to(device),
            self.previous_tag_ids.to(device),
            self.next_tag_ids.to(device),
            self.semantic_token_ids.to(device),
            self.numeric.to(device),
            self.mask.to(device),
            self.targets.to(device),
        )


def collate_pages(items: list[MatrixPage]) -> Batch:
    max_candidates = max(len(item.tag_ids) for item in items)
    feature_count = items[0].numeric.shape[1]
    tag_ids = torch.zeros((len(items), max_candidates), dtype=torch.long)
    parent_tag_ids = torch.zeros_like(tag_ids)
    grandparent_tag_ids = torch.zeros_like(tag_ids)
    previous_tag_ids = torch.zeros_like(tag_ids)
    next_tag_ids = torch.zeros_like(tag_ids)
    max_semantic_tokens = items[0].semantic_token_ids.shape[1]
    semantic_token_ids = torch.zeros(
        (len(items), max_candidates, max_semantic_tokens), dtype=torch.long
    )
    numeric = torch.zeros(
        (len(items), max_candidates, feature_count), dtype=torch.float32
    )
    mask = torch.zeros((len(items), max_candidates), dtype=torch.bool)
    targets = torch.empty((len(items), len(FIELDS)), dtype=torch.long)
    for index, item in enumerate(items):
        count = len(item.tag_ids)
        tag_ids[index, :count] = item.tag_ids
        parent_tag_ids[index, :count] = item.parent_tag_ids
        grandparent_tag_ids[index, :count] = item.grandparent_tag_ids
        previous_tag_ids[index, :count] = item.previous_tag_ids
        next_tag_ids[index, :count] = item.next_tag_ids
        semantic_token_ids[index, :count] = item.semantic_token_ids
        numeric[index, :count] = item.numeric
        mask[index, :count] = True
        targets[index] = torch.where(
            item.targets == count,
            torch.tensor(max_candidates),
            item.targets,
        )
    return Batch(
        tuple(item.page_id for item in items),
        tuple(item.website for item in items),
        tag_ids,
        parent_tag_ids,
        grandparent_tag_ids,
        previous_tag_ids,
        next_tag_ids,
        semantic_token_ids,
        numeric,
        mask,
        targets,
    )


def _loss(scores: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
    # scores: [page, candidate-or-missing, field]
    per_field = nn.functional.cross_entropy(
        scores.permute(0, 2, 1).reshape(-1, scores.shape[1]),
        targets.reshape(-1),
        reduction="none",
    ).reshape(targets.shape)
    weights = torch.tensor(
        [FIELD_LOSS_WEIGHTS[field] for field in FIELDS],
        device=scores.device,
        dtype=scores.dtype,
    )
    return (per_field * weights).sum() / (targets.shape[0] * weights.sum())


def _run_epoch(
    model: DOMNodeSelector,
    loader: Iterable[Batch],
    device: torch.device,
    optimizer: torch.optim.Optimizer | None = None,
) -> dict[str, float]:
    training = optimizer is not None
    model.train(training)
    total_loss = 0.0
    correct = 0
    selections = 0
    for raw_batch in loader:
        batch = raw_batch.to(device)
        with torch.set_grad_enabled(training):
            scores = model(
                batch.tag_ids,
                batch.parent_tag_ids,
                batch.grandparent_tag_ids,
                batch.previous_tag_ids,
                batch.next_tag_ids,
                batch.semantic_token_ids,
                batch.numeric,
                batch.mask,
            )
            loss = _loss(scores, batch.targets)
            if optimizer is not None:
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                optimizer.step()
        total_loss += float(loss.detach()) * len(batch.page_ids)
        predicted = scores.argmax(dim=1)
        correct += int((predicted == batch.targets).sum())
        selections += batch.targets.numel()
    page_count = selections // len(FIELDS)
    return {
        "loss": total_loss / max(1, page_count),
        "exact_selection_accuracy": correct / max(1, selections),
    }


def _loader(
    paths: Sequence[Path], batch_size: int, shuffle: bool
) -> DataLoader[MatrixPage]:
    return DataLoader(
        MatrixDataset(paths),
        batch_size=batch_size,
        shuffle=shuffle,
        collate_fn=collate_pages,
    )


def _new_model(
    tag_count: int,
    semantic_token_count: int,
    feature_count: int,
    device: torch.device,
    field_count: int = len(FIELDS),
) -> DOMNodeSelector:
    return DOMNodeSelector(
        tag_count,
        feature_count,
        semantic_token_count,
        field_count=field_count,
    ).to(device)


def train(
    prepared_dir: Path,
    output_dir: Path,
    epochs: int = 30,
    overfit_epochs: int = 150,
    batch_size: int = 8,
    learning_rate: float = 1e-3,
    seed: int = 17,
) -> dict[str, object]:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    train_paths = sorted((prepared_dir / "train").glob("*.npz"))
    validation_paths = sorted((prepared_dir / "validation").glob("*.npz"))
    if not train_paths or not validation_paths:
        raise ValueError("training and validation matrices are required")
    preprocessing = json.loads(
        (prepared_dir / "preprocessing.json").read_text(encoding="utf-8")
    )
    fields = preprocessing.get("fields", [field.value for field in FIELDS])
    if fields != [field.value for field in FIELDS]:
        raise ValueError(
            f"prepared field schema does not match runtime schema: {fields}"
        )
    if preprocessing.get("dom_cleanup") != DOM_CLEANUP_VERSION:
        raise ValueError(
            "prepared DOM cleanup does not match runtime cleanup: "
            f"{preprocessing.get('dom_cleanup')!r}"
        )
    if preprocessing.get("feature_version") != FEATURE_VERSION:
        raise ValueError(
            "prepared feature version does not match runtime features: "
            f"{preprocessing.get('feature_version')!r}"
        )
    tag_count = len(preprocessing["vocabulary"]["tags"])
    semantic_token_count = len(preprocessing["semantic_vocabulary"]["tokens"])
    with np.load(train_paths[0], allow_pickle=False) as first:
        feature_count = int(first["numeric"].shape[1])

    # Required smoke test: independently prove the architecture can memorize a
    # few pages before spending time on the full split.
    overfit_paths = train_paths[: min(4, len(train_paths))]
    overfit_model = _new_model(
        tag_count, semantic_token_count, feature_count, device, len(fields)
    )
    overfit_optimizer = torch.optim.Adam(
        overfit_model.parameters(), lr=learning_rate * 3
    )
    overfit_loader = _loader(overfit_paths, len(overfit_paths), shuffle=True)
    overfit_metrics: dict[str, float] = {}
    for _ in range(overfit_epochs):
        overfit_metrics = _run_epoch(
            overfit_model, overfit_loader, device, overfit_optimizer
        )

    model = _new_model(
        tag_count, semantic_token_count, feature_count, device, len(fields)
    )
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    train_loader = _loader(train_paths, batch_size, shuffle=True)
    validation_loader = _loader(validation_paths, batch_size, shuffle=False)
    output_dir.mkdir(parents=True, exist_ok=True)
    best_loss = float("inf")
    history: list[dict[str, object]] = []
    for epoch in range(1, epochs + 1):
        train_metrics = _run_epoch(model, train_loader, device, optimizer)
        validation_metrics = _run_epoch(model, validation_loader, device)
        history.append(
            {"epoch": epoch, "train": train_metrics, "validation": validation_metrics}
        )
        if validation_metrics["loss"] < best_loss:
            best_loss = validation_metrics["loss"]
            torch.save(
                {
                    "model_state": model.state_dict(),
                    "tag_count": tag_count,
                    "semantic_token_count": semantic_token_count,
                    "numeric_feature_count": feature_count,
                    "model_config": {
                        "embedding_dim": 6,
                        "semantic_embedding_dim": 3,
                        "hidden_dim": 36,
                    },
                    "fields": fields,
                    "preprocessing": preprocessing,
                    "epoch": epoch,
                    "validation": validation_metrics,
                },
                output_dir / "best.pt",
            )
    metrics: dict[str, object] = {
        "seed": seed,
        "device": str(device),
        "fields": fields,
        "training_pages": len(train_paths),
        "validation_pages": len(validation_paths),
        "overfit_pages": len(overfit_paths),
        "overfit": overfit_metrics,
        "history": history,
        "best_validation_loss": best_loss,
    }
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return metrics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--overfit-epochs", type=int, default=150)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--seed", type=int, default=17)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    metrics = train(
        args.prepared_dir,
        args.output_dir,
        epochs=args.epochs,
        overfit_epochs=args.overfit_epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        seed=args.seed,
    )
    print(json.dumps(metrics, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
