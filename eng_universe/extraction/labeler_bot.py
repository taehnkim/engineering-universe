"""Prepare DOM candidates and use Jev to seed reviewable extraction labels."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from typesafe_sdk import AsyncTypeSafeClient, Choice, TypeSafeClient

from eng_universe.extraction.contract import (
    FIELDS,
    Annotation,
    Field,
    load_annotation,
    save_annotation,
)
from eng_universe.extraction.dom import ParsedPage, parse_html
from eng_universe.extraction.manifest import DatasetManifest, PageRecord

MAX_PAGES = 10
MAX_CHOICES = 129
MAX_STATE_CHARS = 75_000
MISSING_CHOICE = "missing"
NODE_PREFIX = "node_"

ARTICLE_TOKENS = re.compile(r"article|body|content|entry|main|post", re.IGNORECASE)
AUTHOR_TOKENS = re.compile(r"author|byline", re.IGNORECASE)
DATE_TOKENS = re.compile(r"date|publish|time", re.IGNORECASE)
TITLE_TOKENS = re.compile(r"headline|title", re.IGNORECASE)
SUMMARY_TOKENS = re.compile(
    r"subtitle|sub-title|subhead|standfirst|dek|excerpt|description|lead",
    re.IGNORECASE,
)
DATE_TEXT = re.compile(
    r"\b(?:19|20)\d{2}\b|\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|"
    r"may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\b",
    re.IGNORECASE,
)
AUTHOR_TEXT = re.compile(r"^(?:by|written by|author)\b", re.IGNORECASE)
KEEP_ATTRIBUTES = frozenset(
    {"aria-label", "class", "datetime", "id", "itemprop", "role"}
)


@dataclass(frozen=True, slots=True)
class PreparedPage:
    page: ParsedPage
    html: str
    candidate_ids: tuple[int, ...]
    original_char_count: int
    prepared_char_count: int


def select_records(
    records: Sequence[PageRecord], limit: int = MAX_PAGES
) -> list[PageRecord]:
    """Select a stable mix of article pages from all three dataset splits."""

    if not 1 <= limit <= MAX_PAGES:
        raise ValueError(f"limit must be between 1 and {MAX_PAGES}")
    buckets = {
        split: [
            record for record in records if record.is_article and record.split == split
        ]
        for split in ("train", "validation", "test")
    }
    selected: list[PageRecord] = []
    offsets = {split: 0 for split in buckets}
    while len(selected) < limit:
        added = False
        for split in ("train", "validation", "test"):
            offset = offsets[split]
            if offset < len(buckets[split]) and len(selected) < limit:
                selected.append(buckets[split][offset])
                offsets[split] += 1
                added = True
        if not added:
            break
    if len(selected) != limit:
        raise ValueError(f"dataset has only {len(selected)} selectable article pages")
    return selected


def _semantic_value(tag: Tag) -> str:
    values = [tag.name or ""]
    for attribute in KEEP_ATTRIBUTES:
        value = tag.get(attribute)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value:
            values.append(str(value))
    return " ".join(values)


def _candidate_score(tag: Tag) -> tuple[int, int]:
    text = tag.get_text(" ", strip=True)
    text_length = len(text)
    name = (tag.name or "").lower()
    score = {
        "article": 150,
        "main": 140,
        "h1": 150,
        "time": 145,
        "address": 120,
        "header": 100,
        "section": 65,
        "h2": 55,
        "p": 25,
        "div": 20,
    }.get(name, 5)
    semantic_value = _semantic_value(tag)
    if ARTICLE_TOKENS.search(semantic_value):
        score += 140
    if AUTHOR_TOKENS.search(semantic_value):
        score += 250
    if DATE_TOKENS.search(semantic_value):
        score += 220
    if TITLE_TOKENS.search(semantic_value):
        score += 220
    if SUMMARY_TOKENS.search(semantic_value):
        score += 220
    if text_length >= 3_000:
        score += 90
    elif text_length >= 1_000:
        score += 70
    elif text_length >= 300:
        score += 45
    elif text_length >= 60:
        score += 20
    if DATE_TEXT.search(text[:160]):
        score += 65
    if AUTHOR_TEXT.search(text[:160]):
        score += 85
    if name in {"html", "body"}:
        score -= 80
    node_id = int(tag["data-jev-node-id"])
    return score, -node_id


def _clean_attributes(tag: Tag, keep_node_id: bool) -> None:
    cleaned: dict[str, Any] = {}
    if keep_node_id:
        cleaned["data-jev-node-id"] = tag["data-jev-node-id"]
    for name in KEEP_ATTRIBUTES:
        value = tag.get(name)
        if not value:
            continue
        if isinstance(value, list):
            cleaned[name] = value[:6]
        else:
            cleaned[name] = str(value)[:180]
    tag.attrs = cleaned


def _normalize_text(soup: BeautifulSoup) -> None:
    for value in list(soup.find_all(string=True)):
        if isinstance(value, Comment):
            value.extract()
            continue
        if not isinstance(value, NavigableString):
            continue
        compact = re.sub(r"\s+", " ", str(value))
        if len(compact) > 2_000:
            compact = compact[:1_997] + "..."
        value.replace_with(compact)


def _render_within_limit(root: Tag, max_chars: int) -> str:
    """Compact prepared HTML without removing any selected candidate element."""

    prepared_html = str(root)
    if len(prepared_html) <= max_chars:
        return prepared_html

    overflow = len(prepared_html) - max_chars
    for value in reversed(list(root.find_all(string=True))):
        if overflow <= 0:
            break
        text = str(value)
        if len(text) <= 80:
            continue
        remove_count = min(len(text) - 80, overflow)
        value.replace_with(text[: len(text) - remove_count] + "…")
        overflow -= remove_count

    prepared_html = str(root)
    if len(prepared_html) <= max_chars:
        return prepared_html

    for tag in reversed(list(root.find_all(True))):
        if tag is root or tag.has_attr("data-jev-node-id"):
            continue
        tag.unwrap()

    prepared_html = str(root)
    if len(prepared_html) <= max_chars:
        return prepared_html

    overflow = len(prepared_html) - max_chars
    for value in reversed(list(root.find_all(string=True))):
        if overflow <= 0:
            break
        text = str(value)
        if not text:
            continue
        keep_count = max(0, len(text) - overflow - 1)
        value.replace_with((text[:keep_count] + "…") if keep_count else "")
        overflow -= len(text) - keep_count

    prepared_html = str(root)
    if len(prepared_html) > max_chars:
        raise ValueError(
            f"prepared HTML is {len(prepared_html):,} characters; limit is "
            f"{max_chars:,}"
        )
    return prepared_html


def prepare_html(
    html: str,
    *,
    max_candidates: int = MAX_CHOICES - 1,
    max_state_chars: int = MAX_STATE_CHARS,
) -> PreparedPage:
    """Remove page chrome and retain original IDs on likely extraction nodes."""

    if not 4 <= max_candidates < MAX_CHOICES:
        raise ValueError(f"max_candidates must be between 4 and {MAX_CHOICES - 1}")
    if max_state_chars < 1_000:
        raise ValueError("max_state_chars must be at least 1,000")
    page = parse_html(html)
    copy = parse_html(html, strip_chrome=True)
    soup = copy.dom
    for candidate in copy.candidates:
        candidate.element["data-jev-node-id"] = str(candidate.node_id)

    surviving = [
        tag
        for tag in soup.find_all(True)
        if tag.has_attr("data-jev-node-id") and tag.get_text(" ", strip=True)
    ]
    ranked = sorted(surviving, key=_candidate_score, reverse=True)
    selected_ids = {int(tag["data-jev-node-id"]) for tag in ranked[:max_candidates]}
    for tag in soup.find_all(True):
        raw_id = tag.get("data-jev-node-id")
        keep_node_id = raw_id is not None and int(raw_id) in selected_ids
        _clean_attributes(tag, keep_node_id)
    _normalize_text(soup)

    root = soup.body or soup
    prepared_html = _render_within_limit(root, max_state_chars)

    return PreparedPage(
        page=page,
        html=prepared_html,
        candidate_ids=tuple(sorted(selected_ids)),
        original_char_count=len(html),
        prepared_char_count=len(prepared_html),
    )


def _choice_key(node_id: int | None) -> str:
    return MISSING_CHOICE if node_id is None else f"{NODE_PREFIX}{node_id}"


def _node_id(choice: str, valid_ids: set[int]) -> int | None:
    if choice == MISSING_CHOICE:
        return None
    if not choice.startswith(NODE_PREFIX):
        raise ValueError(f"Jev returned an invalid choice: {choice}")
    node_id = int(choice.removeprefix(NODE_PREFIX))
    if node_id not in valid_ids:
        raise ValueError(f"Jev returned an unavailable node ID: {node_id}")
    return node_id


def build_questions(
    candidate_ids: Iterable[int], fields: Iterable[Field] = FIELDS
) -> dict[str, Choice]:
    criteria = {_choice_key(node_id): None for node_id in candidate_ids}
    criteria[MISSING_CHOICE] = "The page does not contain this field."
    instructions = {
        Field.ARTICLE: (
            "Which `prepared_html` element is the tightest wrapper around the complete "
            "article content? Include the title or byline only when they are inside the "
            "same article wrapper. Exclude navigation, recommendations, and page chrome."
        ),
        Field.TITLE: "Which `prepared_html` element is the article's primary title?",
        Field.AUTHORS: (
            "Which `prepared_html` element is the tightest wrapper around the article "
            "byline containing all authors? Select one wrapper containing every author, "
            "not one author link. Choose missing when no authors are present."
        ),
        Field.DATE: (
            "Which `prepared_html` element is the tightest wrapper around the article's "
            "absolute publication date? Choose missing when only a relative date is present."
        ),
        Field.SUMMARY: (
            "Which `prepared_html` element is the article subtitle, standfirst, deck, or "
            "short summary directly associated with the title? Choose missing when absent."
        ),
        Field.RELATIVE_DATE: (
            "Which `prepared_html` element contains a relative publication date such as "
            "'2 days ago'? Do not select reading times such as '5 min read'. Choose "
            "missing when no relative publication date is present."
        ),
    }
    return {
        field.value: Choice(instructions=instructions[field], criteria=criteria)
        for field in fields
    }


def _answer_metadata(answer: Any) -> tuple[str, dict[str, Any]]:
    probabilities = dict(answer.probabilities)
    return answer.choice, {
        "choice": answer.choice,
        "confidence": answer.confidence,
        "selected_probability": probabilities[answer.choice],
        "probabilities": probabilities,
    }


def label_with_jev(
    prepared: PreparedPage,
    record: PageRecord,
    *,
    model: str = "jev-1.13.0",
) -> dict[str, Any]:
    """Call Jev once for all extraction fields and return a bot annotation."""

    state = {
        "page_url": record.url,
        "prepared_html": prepared.html,
    }
    started = time.perf_counter()
    with TypeSafeClient(model=model) as client:
        response = client.system_one(
            state=state,
            questions=build_questions(prepared.candidate_ids),
        )
    latency_ms = (time.perf_counter() - started) * 1000

    return _annotation_from_response(response, prepared, record, latency_ms)


def _annotation_from_response(
    response: Any,
    prepared: PreparedPage,
    record: PageRecord,
    latency_ms: float,
) -> dict[str, Any]:
    valid_ids = set(prepared.candidate_ids)
    labels: dict[str, int | None] = {}
    metadata: dict[str, dict[str, Any]] = {}
    for field in FIELDS:
        answer = response.answers[field.value]
        node_id = _node_id(answer.choice, valid_ids)  # type: ignore[union-attr]
        labels[field.value] = node_id
        _, metadata[field.value] = _answer_metadata(answer)

    return {
        "page_id": record.page_id,
        "html_hash": prepared.page.html_hash,
        "needs_review": True,
        "review_status": "draft",
        "labels": labels,
        "label_source": "jev",
        "model": response.model,
        "latency_ms": latency_ms,
        "labeled_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata,
        "usage": response.usage.model_dump(),
        "preparation": {
            "candidate_count": len(prepared.candidate_ids),
            "original_char_count": prepared.original_char_count,
            "prepared_char_count": prepared.prepared_char_count,
        },
    }


@dataclass(slots=True)
class LabelerBot:
    """Run Jev and seed its selections into the human-review workflow."""

    model: str = "jev-1.13.0"

    def prepare(self, html: str) -> PreparedPage:
        return prepare_html(html)

    def label(self, html: str, record: PageRecord) -> dict[str, Any]:
        return label_with_jev(self.prepare(html), record, model=self.model)

    def label_prepared(
        self, prepared: PreparedPage, record: PageRecord
    ) -> dict[str, Any]:
        return label_with_jev(prepared, record, model=self.model)

    async def label_prepared_async(
        self,
        prepared: PreparedPage,
        record: PageRecord,
        *,
        client: AsyncTypeSafeClient | None = None,
    ) -> dict[str, Any]:
        """Label one prepared page with an optional shared async client."""

        state = {"page_url": record.url, "prepared_html": prepared.html}
        started = time.perf_counter()
        if client is None:
            async with AsyncTypeSafeClient(model=self.model) as owned_client:
                response = await owned_client.system_one(
                    state=state,
                    questions=build_questions(prepared.candidate_ids),
                )
        else:
            response = await client.system_one(
                state=state,
                questions=build_questions(prepared.candidate_ids),
            )
        latency_ms = (time.perf_counter() - started) * 1000
        return _annotation_from_response(response, prepared, record, latency_ms)

    async def label_field_prepared_async(
        self,
        prepared: PreparedPage,
        record: PageRecord,
        field: Field,
        *,
        client: AsyncTypeSafeClient | None = None,
    ) -> dict[str, Any]:
        """Run a fresh Jev request for one extraction field."""

        state = {"page_url": record.url, "prepared_html": prepared.html}
        questions = build_questions(prepared.candidate_ids, (field,))
        started = time.perf_counter()
        if client is None:
            async with AsyncTypeSafeClient(model=self.model) as owned_client:
                response = await owned_client.system_one(
                    state=state,
                    questions=questions,
                )
        else:
            response = await client.system_one(state=state, questions=questions)
        latency_ms = (time.perf_counter() - started) * 1000
        answer = response.answers[field.value]
        node_id = _node_id(answer.choice, set(prepared.candidate_ids))
        _, metadata = _answer_metadata(answer)
        metadata["latency_ms"] = latency_ms
        metadata["labeled_at"] = datetime.now(timezone.utc).isoformat()
        return {
            "field": field.value,
            "node_id": node_id,
            "metadata": metadata,
            "model": response.model,
            "latency_ms": latency_ms,
            "usage": response.usage.model_dump(),
        }

    def seed_core_annotation(
        self,
        result: dict[str, Any],
        dataset_dir: Path,
        *,
        overwrite_reviewed: bool = False,
    ) -> bool:
        """Save Jev labels as a draft, while preserving reviewed human work."""

        annotation_path = (
            dataset_dir / "annotations" / f"{result['page_id']}.json"
        )
        if annotation_path.exists():
            existing = load_annotation(annotation_path)
            if existing.review_status == "reviewed" and not overwrite_reviewed:
                return False
        annotation = Annotation.from_dict(
            {
                "page_id": result["page_id"],
                "html_hash": result["html_hash"],
                "labels": result["labels"],
                "needs_review": True,
                "review_status": "draft",
            }
        )
        save_annotation(annotation_path, annotation)
        return True


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def run_entry(record: PageRecord) -> dict[str, Any]:
    return {
        "page_id": record.page_id,
        "split": record.split,
        "website": record.website,
        "url": record.url,
        "source_html_path": record.html_path,
        "status": "pending",
        "error": None,
    }


def load_records(
    dataset_dir: Path, page_ids: Sequence[str], limit: int
) -> list[PageRecord]:
    manifest = DatasetManifest.load(dataset_dir / "manifest.json")
    if not page_ids:
        return select_records(manifest.pages, limit)
    if len(page_ids) > MAX_PAGES:
        raise ValueError(f"at most {MAX_PAGES} page IDs are allowed")
    by_id = {record.page_id: record for record in manifest.pages}
    missing = [page_id for page_id in page_ids if page_id not in by_id]
    if missing:
        raise ValueError(f"unknown page IDs: {', '.join(missing)}")
    records = [by_id[page_id] for page_id in page_ids]
    if any(not record.is_article for record in records):
        raise ValueError("listing pages cannot be labeled as articles")
    return records
