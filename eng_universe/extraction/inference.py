"""End-to-end inference using the exact training preprocessing."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import torch

from eng_universe.extraction.author_boundary import (
    AUTHOR_FIELD_INDEX,
    AuthorBoundaryRefiner,
)
from eng_universe.extraction.contract import FIELDS, Field
from eng_universe.extraction.dom import DOM_CLEANUP_VERSION, ParsedPage, parse_html
from eng_universe.extraction.features import (
    FEATURE_VERSION,
    FeatureNormalizer,
    SemanticVocabulary,
    TagVocabulary,
    featurize_page,
)
from eng_universe.extraction.model import DOMNodeSelector
from eng_universe.extraction.postprocess import (
    derive_published_at,
    normalize_scraped_at,
)

FieldName = Literal["article", "title", "authors", "date", "summary", "relative_date"]


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    article: str | None
    title: str | None
    authors: str | None
    date: str | None
    summary: str | None
    relative_date: str | None
    scraped_at: str
    published_at: str | None


class DOMExtractor:
    def __init__(
        self,
        checkpoint_path: str | Path,
        *,
        author_boundary_checkpoint: str | Path | None = None,
    ) -> None:
        checkpoint = torch.load(
            Path(checkpoint_path), map_location="cpu", weights_only=False
        )
        preprocessing = checkpoint["preprocessing"]
        checkpoint_fields = checkpoint.get("fields", preprocessing.get("fields"))
        expected_fields = [field.value for field in FIELDS]
        if checkpoint_fields != expected_fields:
            raise ValueError(
                f"checkpoint field schema {checkpoint_fields} does not match "
                f"runtime schema {expected_fields}"
            )
        if preprocessing.get("dom_cleanup") != DOM_CLEANUP_VERSION:
            raise ValueError(
                "checkpoint DOM cleanup does not match runtime cleanup: "
                f"{preprocessing.get('dom_cleanup')!r}"
            )
        if preprocessing.get("feature_version") != FEATURE_VERSION:
            raise ValueError(
                "checkpoint feature version does not match runtime features: "
                f"{preprocessing.get('feature_version')!r}"
            )
        self.vocabulary = TagVocabulary.from_dict(preprocessing["vocabulary"])
        self.semantic_vocabulary = SemanticVocabulary.from_dict(
            preprocessing["semantic_vocabulary"]
        )
        self.normalizer = FeatureNormalizer.from_dict(preprocessing["normalizer"])
        model_config = checkpoint.get("model_config", {})
        self.model = DOMNodeSelector(
            int(checkpoint["tag_count"]),
            int(checkpoint["numeric_feature_count"]),
            int(checkpoint["semantic_token_count"]),
            embedding_dim=int(model_config.get("embedding_dim", 6)),
            semantic_embedding_dim=int(model_config.get("semantic_embedding_dim", 4)),
            hidden_dim=int(model_config.get("hidden_dim", 36)),
            field_count=len(FIELDS),
        )
        self.model.load_state_dict(checkpoint["model_state"])
        self.model.eval()
        self.author_boundary_checkpoint = author_boundary_checkpoint
        self.author_boundary = (
            AuthorBoundaryRefiner(author_boundary_checkpoint, checkpoint_path)
            if author_boundary_checkpoint is not None
            else None
        )

    def predict_ids(self, html: str) -> dict[Field, int | None]:
        page = parse_html(html, strip_chrome=True)
        return self.predict_page(page)

    def predict_page(self, page: ParsedPage) -> dict[Field, int | None]:
        """Predict node IDs from an already cleaned and parsed page."""

        features = featurize_page(
            page, self.vocabulary, self.semantic_vocabulary, self.normalizer
        )
        if not page.candidates:
            return {field: None for field in FIELDS}
        with torch.inference_mode():
            scores = self.model(
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
        predictions: dict[Field, int | None] = {}
        for field_index, selected_field in enumerate(FIELDS):
            selected_index = int(scores[0, :, field_index].argmax())
            predictions[selected_field] = (
                None
                if selected_index == len(page.candidates)
                else int(features.node_ids[selected_index])
            )
        selected_author = predictions[Field.AUTHORS]
        if self.author_boundary is not None and selected_author is not None:
            predictions[Field.AUTHORS] = self.author_boundary.refine(
                page,
                selected_author,
                scores[0, :-1, AUTHOR_FIELD_INDEX].numpy(),
                features.numeric,
            )
        return predictions

    def extract(
        self, html: str, field: FieldName = "article"
    ) -> dict[str, str | int] | None:
        page = parse_html(html, strip_chrome=True)
        selected_id = self.predict_page(page)[Field(field)]
        return None if selected_id is None else page.selected_content(selected_id)

    def extract_all(self, html: str) -> dict[str, dict[str, str | int] | None]:
        page = parse_html(html, strip_chrome=True)
        return {
            field.value: (None if node_id is None else page.selected_content(node_id))
            for field, node_id in self.predict_page(page).items()
        }

    def extract_document(self, html: str, scraped_at: str) -> ExtractedDocument:
        """Return text values and derive an absolute publication timestamp."""

        selections = self.extract_all(html)
        values = {
            field.value: (
                None
                if selections[field.value] is None
                else str(selections[field.value]["text"])
            )
            for field in FIELDS
        }
        normalized_scraped_at = normalize_scraped_at(scraped_at)
        return ExtractedDocument(
            article=values["article"],
            title=values["title"],
            authors=values["authors"],
            date=values["date"],
            summary=values["summary"],
            relative_date=values["relative_date"],
            scraped_at=normalized_scraped_at,
            published_at=derive_published_at(
                values["date"], values["relative_date"], normalized_scraped_at
            ),
        )

    def extract_document_dict(
        self, html: str, scraped_at: str
    ) -> dict[str, str | None]:
        return asdict(self.extract_document(html, scraped_at))


_DEFAULT_EXTRACTOR: DOMExtractor | None = None


def extract(html: str, field: FieldName = "article") -> dict[str, str | int] | None:
    """Select a wrapper and return its original-DOM HTML and plain text.

    Set ``ENG_UNIVERSE_EXTRACTOR_CHECKPOINT`` to the trained ``best.pt`` path.
    Reuse ``DOMExtractor`` directly when making many calls to avoid reloading.
    """

    global _DEFAULT_EXTRACTOR
    if _DEFAULT_EXTRACTOR is None:
        checkpoint = os.environ.get("ENG_UNIVERSE_EXTRACTOR_CHECKPOINT")
        if not checkpoint:
            raise RuntimeError(
                "set ENG_UNIVERSE_EXTRACTOR_CHECKPOINT or construct DOMExtractor(path)"
            )
        _DEFAULT_EXTRACTOR = DOMExtractor(checkpoint)
    return _DEFAULT_EXTRACTOR.extract(html, field)
