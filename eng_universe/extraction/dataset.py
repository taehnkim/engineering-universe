"""Annotation validation and per-page feature-matrix preparation."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterator, Sequence

import numpy as np

from eng_universe.extraction.contract import FIELDS, Annotation, load_annotation
from eng_universe.extraction.dom import ParsedPage, parse_html
from eng_universe.extraction.features import (
    FeatureNormalizer,
    PageFeatures,
    TagVocabulary,
    featurize_page,
)
from eng_universe.extraction.manifest import DatasetManifest, PageRecord, Split


@dataclass(frozen=True, slots=True)
class LabeledPage:
    record: PageRecord
    page: ParsedPage
    annotation: Annotation


@dataclass(frozen=True, slots=True)
class PreparedPage:
    record: PageRecord
    features: PageFeatures
    targets: np.ndarray


def iter_labeled_pages(
    dataset_dir: Path,
    split: Split | None = None,
) -> Iterator[LabeledPage]:
    manifest = DatasetManifest.load(dataset_dir / "manifest.json")
    for record in manifest.pages:
        if split is not None and record.split != split:
            continue
        annotation_path = dataset_dir / "annotations" / f"{record.page_id}.json"
        if not annotation_path.exists():
            continue
        html = (dataset_dir / record.html_path).read_text(encoding="utf-8")
        page = parse_html(html)
        annotation = load_annotation(annotation_path)
        if annotation.page_id != record.page_id:
            raise ValueError(f"annotation page mismatch for {record.page_id}")
        if annotation.html_hash != page.html_hash or record.html_hash != page.html_hash:
            raise ValueError(f"stale HTML or annotation for {record.page_id}")
        if annotation.review_status != "reviewed" or annotation.needs_review:
            continue
        for field, node_id in annotation.labels.items():
            if node_id is not None and node_id not in page.node_by_id:
                raise ValueError(
                    f"{record.page_id}: {field.value} references missing node {node_id}"
                )
        yield LabeledPage(record, page, annotation)


def fit_preprocessing(train_pages: Sequence[LabeledPage]) -> tuple[TagVocabulary, FeatureNormalizer]:
    vocabulary = TagVocabulary.fit(item.page for item in train_pages)
    raw_matrices = [
        featurize_page(item.page, vocabulary).numeric for item in train_pages
    ]
    return vocabulary, FeatureNormalizer.fit(raw_matrices)


def prepare_page(
    item: LabeledPage,
    vocabulary: TagVocabulary,
    normalizer: FeatureNormalizer,
) -> PreparedPage:
    features = featurize_page(item.page, vocabulary, normalizer)
    missing_index = len(item.page.candidates)
    targets = np.asarray(
        [
            missing_index
            if item.annotation.labels[field] is None
            else int(item.annotation.labels[field])
            for field in FIELDS
        ],
        dtype=np.int64,
    )
    return PreparedPage(item.record, features, targets)


def save_prepared_page(path: Path, item: PreparedPage) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        page_id=np.asarray(item.record.page_id),
        website=np.asarray(item.record.website),
        split=np.asarray(item.record.split),
        node_ids=item.features.node_ids,
        tag_ids=item.features.tag_ids,
        parent_tag_ids=item.features.parent_tag_ids,
        numeric=item.features.numeric,
        targets=item.targets,
    )


def prepare_dataset(dataset_dir: Path, output_dir: Path) -> dict[str, int]:
    by_split = {
        split: list(iter_labeled_pages(dataset_dir, split))
        for split in ("train", "validation", "test")
    }
    if not by_split["train"]:
        raise ValueError("no reviewed training annotations found")
    vocabulary, normalizer = fit_preprocessing(by_split["train"])
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "preprocessing.json").write_text(
        json.dumps(
            {
                "fields": [field.value for field in FIELDS],
                "vocabulary": vocabulary.to_dict(),
                "normalizer": normalizer.to_dict(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    counts: dict[str, int] = {}
    for split, pages in by_split.items():
        split_dir = output_dir / split
        split_dir.mkdir(parents=True, exist_ok=True)
        for stale_path in split_dir.glob("*.npz"):
            stale_path.unlink()
        counts[split] = len(pages)
        for item in pages:
            prepared = prepare_page(item, vocabulary, normalizer)
            save_prepared_page(split_dir / f"{item.record.page_id}.npz", prepared)
    return counts
