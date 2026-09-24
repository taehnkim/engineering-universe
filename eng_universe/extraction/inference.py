"""End-to-end inference using the exact training preprocessing."""

from __future__ import annotations

import os
import time
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

FieldName = Literal["article", "title", "authors", "date"]
DEFAULT_CHECKPOINT = Path(__file__).parent / "checkpoints" / "best.pt"
DEFAULT_AUTHOR_BOUNDARY_CHECKPOINT = (
    Path(__file__).parent / "checkpoints" / "author_boundary.pt"
)


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    article_text: str | None
    article_html: str | None
    article_confidence: float | None
    title_text: str | None
    title_html: str | None
    title_confidence: float | None
    authors_text: str | None
    authors_html: str | None
    authors_confidence: float | None
    date_text: str | None
    date_html: str | None
    date_confidence: float | None
    scraped_at: str
    published_at: str | None


class DOMExtractor:
    def __init__(
        self,
        checkpoint_path: str | Path | None = None,
        *,
        author_boundary_checkpoint: str | Path | None = None,
        dom_backend: str = "python",
    ) -> None:
        if dom_backend not in {"python", "go"}:
            raise ValueError(f"unknown DOM backend: {dom_backend}")
        self.dom_backend = dom_backend
        if checkpoint_path is None:
            checkpoint_path = DEFAULT_CHECKPOINT
            if author_boundary_checkpoint is None:
                author_boundary_checkpoint = DEFAULT_AUTHOR_BOUNDARY_CHECKPOINT
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
        page = parse_html(html, strip_chrome=True, backend=self.dom_backend)
        return self.predict_page(page)

    def predict_page(self, page: ParsedPage) -> dict[Field, int | None]:
        """Predict node IDs from an already cleaned and parsed page."""

        return self._predict_page(page, None, None)

    def predict_page_with_confidence(
        self, page: ParsedPage
    ) -> tuple[dict[Field, int | None], dict[Field, float]]:
        """Return selected IDs and uncalibrated softmax scores for final nodes."""

        confidence: dict[Field, float] = {}
        return self._predict_page(page, None, confidence), confidence

    def predict_page_profiled(
        self, page: ParsedPage
    ) -> tuple[dict[Field, int | None], list[dict[str, str | float]]]:
        """Predict with per-stage wall times for interactive diagnostics."""

        timings: list[dict[str, str | float]] = []
        return self._predict_page(page, timings, None), timings

    def predict_page_profiled_with_confidence(
        self, page: ParsedPage
    ) -> tuple[dict[Field, int | None], dict[Field, float], list[dict[str, str | float]]]:
        timings: list[dict[str, str | float]] = []
        confidence: dict[Field, float] = {}
        predictions = self._predict_page(page, timings, confidence)
        return predictions, confidence, timings

    def _predict_page(
        self, page: ParsedPage, timings: list[dict[str, str | float]] | None,
        confidence: dict[Field, float] | None,
    ) -> dict[Field, int | None]:
        started = time.perf_counter() if timings is not None else 0.0

        features = featurize_page(
            page, self.vocabulary, self.semantic_vocabulary, self.normalizer
        )
        if timings is not None:
            timings.append(
                {
                    "step": "Feature extraction",
                    "ms": (time.perf_counter() - started) * 1_000,
                }
            )
        if not page.candidates:
            if confidence is not None:
                confidence.update({field: 1.0 for field in FIELDS})
            return {field: None for field in FIELDS}
        started = time.perf_counter() if timings is not None else 0.0
        inputs = (
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
        if timings is not None:
            timings.append(
                {"step": "Tensor setup", "ms": (time.perf_counter() - started) * 1_000}
            )
        with torch.inference_mode():
            if timings is None:
                scores = self.model(*inputs)
            else:
                scores, embedding_ms, scoring_ms = self.model.forward_profiled(*inputs)
                timings.extend(
                    (
                        {"step": "Embeddings", "ms": embedding_ms},
                        {"step": "Neural scoring", "ms": scoring_ms},
                    )
                )
        started = time.perf_counter() if timings is not None else 0.0
        predictions: dict[Field, int | None] = {}
        for field_index, selected_field in enumerate(FIELDS):
            selected_index = int(scores[0, :, field_index].argmax())
            predictions[selected_field] = (
                None
                if selected_index == len(page.candidates)
                else int(features.node_ids[selected_index])
            )
        selected_title = predictions[Field.TITLE]
        if selected_title is not None and not page.candidate(selected_title).get_text(
            " ", strip=True
        ):
            # An image-only site logo can be an empty <h1> after media cleanup.
            # Keep the model's scores, but require a populated heading for title.
            title_index = FIELDS.index(Field.TITLE)
            headings = [
                index
                for index, candidate in enumerate(page.candidates)
                if (
                    candidate.element.name == "h1"
                    or "headline" in str(candidate.element.get("itemprop", "")).lower()
                )
                and candidate.element.get_text(" ", strip=True)
            ]
            if headings:
                best_heading = max(
                    headings, key=lambda index: float(scores[0, index, title_index])
                )
                predictions[Field.TITLE] = int(features.node_ids[best_heading])
        if timings is not None:
            timings.append(
                {"step": "Select nodes", "ms": (time.perf_counter() - started) * 1_000}
            )
        selected_author = predictions[Field.AUTHORS]
        if self.author_boundary is not None and selected_author is not None:
            started = time.perf_counter() if timings is not None else 0.0
            predictions[Field.AUTHORS] = self.author_boundary.refine(
                page,
                selected_author,
                scores[0, :-1, AUTHOR_FIELD_INDEX].numpy(),
                features.numeric,
            )
            if timings is not None:
                timings.append(
                    {
                        "step": "Author refinement",
                        "ms": (time.perf_counter() - started) * 1_000,
                    }
                )
        if confidence is not None:
            index_by_id = {candidate.node_id: index for index, candidate in enumerate(page.candidates)}
            for field_index, field in enumerate(FIELDS):
                selected_id = predictions[field]
                selected_index = len(page.candidates) if selected_id is None else index_by_id[selected_id]
                confidence[field] = float(torch.softmax(scores[0, :, field_index], dim=0)[selected_index])
        return predictions

    def extract(
        self, html: str, field: FieldName = "article"
    ) -> dict[str, str | int | float] | None:
        page = parse_html(html, strip_chrome=True, backend=self.dom_backend)
        predictions, confidence = self.predict_page_with_confidence(page)
        selected_id = predictions[Field(field)]
        return None if selected_id is None else {
            **page.selected_content(selected_id), "confidence": confidence[Field(field)]
        }

    def extract_all(self, html: str) -> dict[str, dict[str, str | int | float] | None]:
        page = parse_html(html, strip_chrome=True, backend=self.dom_backend)
        predictions, confidence = self.predict_page_with_confidence(page)
        return {
            field.value: (None if node_id is None else {
                **page.selected_content(node_id), "confidence": confidence[field]
            })
            for field, node_id in predictions.items()
        }

    def extract_document(self, html: str, scraped_at: str) -> ExtractedDocument:
        """Return text values and derive an absolute publication timestamp."""

        selections = self.extract_all(html)
        values = {
            f"{field.value}_{key}": (
                None if selections[field.value] is None else selections[field.value][key]
            )
            for field in FIELDS for key in ("text", "html", "confidence")
        }
        normalized_scraped_at = normalize_scraped_at(scraped_at)
        return ExtractedDocument(
            **values,
            scraped_at=normalized_scraped_at,
            published_at=derive_published_at(values["date_text"]),
        )

    def extract_document_dict(
        self, html: str, scraped_at: str
    ) -> dict[str, str | float | None]:
        return asdict(self.extract_document(html, scraped_at))


_DEFAULT_EXTRACTOR: DOMExtractor | None = None


def extract(html: str, field: FieldName = "article") -> dict[str, str | int | float] | None:
    """Select a wrapper and return its original-DOM HTML and plain text.

    Use the bundled base checkpoint and author-boundary refiner by default.
    Set ``ENG_UNIVERSE_EXTRACTOR_CHECKPOINT`` for a custom base checkpoint,
    and ``ENG_UNIVERSE_AUTHOR_BOUNDARY_CHECKPOINT`` for its matching refiner.
    Reuse ``DOMExtractor`` directly when making many calls to avoid reloading.
    """

    global _DEFAULT_EXTRACTOR
    if _DEFAULT_EXTRACTOR is None:
        checkpoint = os.environ.get("ENG_UNIVERSE_EXTRACTOR_CHECKPOINT")
        author_boundary_checkpoint = os.environ.get(
            "ENG_UNIVERSE_AUTHOR_BOUNDARY_CHECKPOINT"
        )
        _DEFAULT_EXTRACTOR = DOMExtractor(
            checkpoint, author_boundary_checkpoint=author_boundary_checkpoint
        )
    return _DEFAULT_EXTRACTOR.extract(html, field)
