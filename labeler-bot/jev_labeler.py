"""Prepare DOM candidates and ask Jev to label extraction targets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Iterable, Sequence

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from typesafe_sdk import Choice, TypeSafeClient

from eng_universe.extraction.contract import FIELDS, Field
from eng_universe.extraction.dom import ParsedPage, parse_html
from eng_universe.extraction.manifest import DatasetManifest, PageRecord


MAX_PAGES = 10
MAX_CHOICES = 255
MAX_STATE_CHARS = 145_000
MISSING_CHOICE = "missing"
NODE_PREFIX = "node_"

DROP_TAGS = frozenset(
    {
        "aside",
        "audio",
        "button",
        "canvas",
        "dialog",
        "footer",
        "form",
        "head",
        "iframe",
        "input",
        "nav",
        "noscript",
        "picture",
        "script",
        "select",
        "source",
        "style",
        "svg",
        "template",
        "video",
    }
)
CHROME_TOKENS = re.compile(
    r"(?:^|[-_\s])(?:breadcrumb|comment|consent|cookie|drawer|menu|modal|newsletter|"
    r"pagination|promo|recommend|related|share|sidebar|site-nav|social|subscribe|"
    r"tooltip)(?:$|[-_\s])",
    re.IGNORECASE,
)
ARTICLE_TOKENS = re.compile(r"article|body|content|entry|main|post", re.IGNORECASE)
AUTHOR_TOKENS = re.compile(r"author|byline", re.IGNORECASE)
DATE_TOKENS = re.compile(r"date|publish|time", re.IGNORECASE)
TITLE_TOKENS = re.compile(r"headline|title", re.IGNORECASE)
DATE_TEXT = re.compile(
    r"\b(?:19|20)\d{2}\b|\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|"
    r"may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\b",
    re.IGNORECASE,
)
AUTHOR_TEXT = re.compile(r"^(?:by|written by|author)\b", re.IGNORECASE)
KEEP_ATTRIBUTES = frozenset({"aria-label", "class", "datetime", "id", "itemprop", "role"})


@dataclass(frozen=True, slots=True)
class PreparedPage:
    page: ParsedPage
    html: str
    candidate_ids: tuple[int, ...]
    original_char_count: int
    prepared_char_count: int


def select_records(records: Sequence[PageRecord], limit: int = MAX_PAGES) -> list[PageRecord]:
    """Select a stable mix of article pages from all three dataset splits."""

    if not 1 <= limit <= MAX_PAGES:
        raise ValueError(f"limit must be between 1 and {MAX_PAGES}")
    buckets = {
        split: [record for record in records if record.is_article and record.split == split]
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


def _looks_like_chrome(tag: Tag) -> bool:
    role = str(tag.get("role", "")).lower()
    if role in {"contentinfo", "dialog", "navigation", "search"}:
        return True
    semantic_value = _semantic_value(tag)
    if (
        AUTHOR_TOKENS.search(semantic_value)
        or DATE_TOKENS.search(semantic_value)
        or TITLE_TOKENS.search(semantic_value)
    ):
        return False
    return bool(CHROME_TOKENS.search(semantic_value))


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


def prepare_html(html: str, *, max_candidates: int = MAX_CHOICES - 1) -> PreparedPage:
    """Remove page chrome and retain original IDs on likely extraction nodes."""

    if not 4 <= max_candidates < MAX_CHOICES:
        raise ValueError(f"max_candidates must be between 4 and {MAX_CHOICES - 1}")
    page = parse_html(html)
    copy = parse_html(html)
    soup = copy.dom
    for candidate in copy.candidates:
        candidate.element["data-jev-node-id"] = str(candidate.node_id)

    for tag in list(soup.find_all(DROP_TAGS)):
        tag.decompose()
    for tag in list(soup.find_all(True)):
        if tag.parent is not None and _looks_like_chrome(tag):
            tag.decompose()

    surviving = [
        tag
        for tag in soup.find_all(True)
        if tag.has_attr("data-jev-node-id") and tag.get_text(" ", strip=True)
    ]
    ranked = sorted(surviving, key=_candidate_score, reverse=True)
    selected_ids = {
        int(tag["data-jev-node-id"])
        for tag in ranked[:max_candidates]
    }
    for tag in soup.find_all(True):
        raw_id = tag.get("data-jev-node-id")
        keep_node_id = raw_id is not None and int(raw_id) in selected_ids
        _clean_attributes(tag, keep_node_id)
    _normalize_text(soup)

    root = soup.body or soup
    prepared_html = str(root)
    if len(prepared_html) > MAX_STATE_CHARS:
        overflow = len(prepared_html) - MAX_STATE_CHARS
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
    if len(prepared_html) > MAX_STATE_CHARS:
        raise ValueError(
            f"prepared HTML is {len(prepared_html):,} characters; limit is "
            f"{MAX_STATE_CHARS:,}"
        )

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


def build_questions(candidate_ids: Iterable[int]) -> dict[str, Choice]:
    criteria = {_choice_key(node_id): None for node_id in candidate_ids}
    criteria[MISSING_CHOICE] = "The page does not contain this field."
    instructions = {
        Field.ARTICLE: (
            "Which `prepared_html` element is the tightest wrapper around the complete "
            "article content? Include the title or byline only when they are inside the "
            "same article wrapper. Exclude navigation, recommendations, and page chrome."
        ),
        Field.TITLE: "Which `prepared_html` element is the article's primary title?",
        Field.AUTHOR: (
            "Which `prepared_html` element is the tightest wrapper around the article "
            "author or byline? Choose missing when no author is present."
        ),
        Field.DATE: (
            "Which `prepared_html` element is the tightest wrapper around the article's "
            "publication date? Choose missing when no publication date is present."
        ),
    }
    return {
        field.value: Choice(instructions=instructions[field], criteria=criteria)
        for field in FIELDS
    }


def label_with_jev(
    prepared: PreparedPage,
    record: PageRecord,
    *,
    model: str = "jev",
) -> dict[str, Any]:
    """Call Jev once for all four fields and return a bot annotation."""

    state = {
        "page_url": record.url,
        "prepared_html": prepared.html,
    }
    with TypeSafeClient(model=model) as client:
        response = client.system_one(
            state=state,
            questions=build_questions(prepared.candidate_ids),
        )

    valid_ids = set(prepared.candidate_ids)
    labels: dict[str, int | None] = {}
    metadata: dict[str, dict[str, Any]] = {}
    for field in FIELDS:
        answer = response.answers[field.value]
        node_id = _node_id(answer.choice, valid_ids)  # type: ignore[union-attr]
        labels[field.value] = node_id
        probabilities = dict(answer.probabilities)  # type: ignore[union-attr]
        metadata[field.value] = {
            "choice": answer.choice,  # type: ignore[union-attr]
            "confidence": answer.confidence,  # type: ignore[union-attr]
            "selected_probability": probabilities[answer.choice],  # type: ignore[union-attr]
            "probabilities": probabilities,
        }

    return {
        "page_id": record.page_id,
        "html_hash": prepared.page.html_hash,
        "needs_review": True,
        "review_status": "draft",
        "labels": labels,
        "label_source": "jev",
        "model": response.model,
        "labeled_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata,
        "usage": response.usage.model_dump(),
        "preparation": {
            "candidate_count": len(prepared.candidate_ids),
            "original_char_count": prepared.original_char_count,
            "prepared_char_count": prepared.prepared_char_count,
        },
    }


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def load_records(dataset_dir: Path, page_ids: Sequence[str], limit: int) -> list[PageRecord]:
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
