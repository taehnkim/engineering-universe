import hashlib
import json
from pathlib import Path

import torch

from eng_universe.extraction.contract import FIELDS, Annotation, Field
from eng_universe.extraction.dom import parse_html
from eng_universe.extraction.features import (
    FeatureNormalizer,
    SemanticVocabulary,
    TagVocabulary,
    featurize_page,
)
from eng_universe.extraction.inference import DEFAULT_CHECKPOINT
from modeling.dom_extractor.dataset import LabeledPage, prepare_dataset, prepare_page
from modeling.dom_extractor.manifest import DatasetManifest, PageRecord


def test_prepared_targets_map_stable_node_ids_to_clean_candidate_indices() -> None:
    html = "<nav>Menu</nav><main><h1>Title</h1><p>Body</p></main>"
    page = parse_html(html, strip_chrome=True)
    title = next(item for item in page.candidates if item.element.name == "h1")
    labels = {field: None for field in FIELDS}
    labels[Field.TITLE] = title.node_id
    item = LabeledPage(
        record=PageRecord(
            page_id="page",
            source_id="source",
            company="Company",
            website="example.com",
            url="https://example.com/page",
            html_path="html/page.html",
            split="train",
            capture_kind="browser",
            html_hash=page.html_hash,
        ),
        page=page,
        annotation=Annotation("page", page.html_hash, labels),
    )
    vocabulary = TagVocabulary.fit([page])
    semantic_vocabulary = SemanticVocabulary.fit([page])
    normalizer = FeatureNormalizer.fit(
        [featurize_page(page, vocabulary, semantic_vocabulary).numeric]
    )

    prepared = prepare_page(item, vocabulary, semantic_vocabulary, normalizer)

    expected_index = list(prepared.features.node_ids).index(title.node_id)
    assert prepared.targets[list(FIELDS).index(Field.TITLE)] == expected_index


def test_preparation_can_reuse_checkpoint_vocabulary_for_fine_tuning(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw"
    (raw / "html").mkdir(parents=True)
    (raw / "annotations").mkdir()
    html = "<html><body><article><h1>Title</h1><p>Body</p></article></body></html>"
    (raw / "html/page.html").write_text(html, encoding="utf-8")
    page = parse_html(html, strip_chrome=True)
    record = PageRecord(
        page_id="page",
        source_id="source",
        company="Company",
        website="example.com",
        url="https://example.com/page",
        html_path="html/page.html",
        split="train",
        capture_kind="http",
        html_hash=page.html_hash,
    )
    DatasetManifest(1, (record,)).save(raw / "manifest.json")
    (raw / "annotations/page.json").write_text(
        json.dumps(
            {
                "page_id": "page",
                "html_hash": page.html_hash,
                "review_status": "reviewed",
                "needs_review": False,
                "labels": {field.value: None for field in FIELDS},
            }
        ),
        encoding="utf-8",
    )
    prepared = tmp_path / "prepared"
    assert prepare_dataset(
        raw, prepared, preprocessing_checkpoint=DEFAULT_CHECKPOINT
    ) == {"train": 1, "validation": 0, "test": 0}
    source = torch.load(DEFAULT_CHECKPOINT, map_location="cpu", weights_only=False)
    reused = json.loads((prepared / "preprocessing.json").read_text(encoding="utf-8"))
    assert reused["vocabulary"] == source["preprocessing"]["vocabulary"]
    assert (
        reused["semantic_vocabulary"] == source["preprocessing"]["semantic_vocabulary"]
    )
    assert reused["normalizer"] == source["preprocessing"]["normalizer"]
    assert (
        reused["preprocessing_source_sha256"]
        == hashlib.sha256(DEFAULT_CHECKPOINT.read_bytes()).hexdigest()
    )
