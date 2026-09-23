"""Create conservative draft annotations from semantic DOM conventions.

Every generated annotation is an explicit draft and remains excluded from
training until a person saves it in the interactive labeler.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
from bs4 import Tag

from eng_universe.extraction.contract import (
    FIELDS,
    Field,
    Annotation,
    load_annotation,
    save_annotation,
)
from eng_universe.extraction.dom import ParsedPage, parse_html
from eng_universe.extraction.features import DATE_LIKE_RE
from modeling.dom_extractor.manifest import DatasetManifest


CONTENT_TOKENS = re.compile(
    r"(?:article|post|story|entry|prose|rich)[-_ ]*(?:body|content|text)|"
    r"(?:body|content)[-_ ]*(?:article|post|story|entry)",
    re.IGNORECASE,
)
AUTHOR_TOKENS = re.compile(r"author|byline|writer", re.IGNORECASE)
DATE_TOKENS = re.compile(r"date|publish|posted|timestamp", re.IGNORECASE)


def _attrs(element: Tag) -> str:
    # URLs and arbitrary data values create false positives (for example an
    # article about "authorization" is not an author element).
    values: list[str] = []
    for key in ("class", "id", "itemprop", "rel"):
        value = element.get(key)
        if value is None:
            continue
        values.append(key)
        values.extend(value if isinstance(value, list) else [str(value)])
    return " ".join(values)


def _visible_text(element: Tag) -> str:
    return element.get_text(" ", strip=True)


def _schema_author_names(page: ParsedPage) -> set[str]:
    names: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            author = value.get("author")
            if isinstance(author, str):
                names.add(author.strip())
            elif isinstance(author, dict):
                name = author.get("name")
                if isinstance(name, str):
                    names.add(name.strip())
            elif isinstance(author, list):
                for item in author:
                    if isinstance(item, str):
                        names.add(item.strip())
                    elif isinstance(item, dict) and isinstance(item.get("name"), str):
                        names.add(item["name"].strip())
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    visit(nested)

    for script in page.dom.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            visit(json.loads(script.get_text()))
        except (json.JSONDecodeError, TypeError):
            continue
    return {name for name in names if name}


def _node_id(page: ParsedPage, element: Tag | None) -> int | None:
    return None if element is None else page.id_by_element_identity.get(id(element))


def _title(page: ParsedPage) -> Tag | None:
    choices: list[tuple[float, Tag]] = []
    for candidate in page.candidates:
        element = candidate.element
        text = _visible_text(element)
        if element.name not in {"h1", "h2"} or not 5 <= len(text) <= 500:
            continue
        if any(parent.name == "nav" for parent in element.parents if isinstance(parent, Tag)):
            continue
        score = (100 if element.name == "h1" else 20) - candidate.node_id / 1000
        score += min(len(text), 120) / 5
        if len(text) < 12:
            score -= 25
        if re.search(r"headline|title", _attrs(element), re.IGNORECASE):
            score += 20
        choices.append((score, element))
    return max(choices, key=lambda item: item[0])[1] if choices else None


def _article(page: ParsedPage, title: Tag | None) -> Tag | None:
    choices: list[tuple[float, Tag]] = []
    for candidate in page.candidates:
        element = candidate.element
        paragraphs = (1 if element.name == "p" else 0) + len(element.find_all("p"))
        text_length = len(_visible_text(element))
        if paragraphs < 3 or text_length < 500:
            continue
        if title is not None and (element is title or title in element.descendants):
            continue
        attrs = _attrs(element)
        score = math.log1p(text_length) + math.log1p(paragraphs) * 4
        if element.name in {"article", "main"}:
            score += 8
        if CONTENT_TOKENS.search(attrs):
            score += 18
        if element.name in {"body", "html"}:
            score -= 30
        link_text = sum(len(_visible_text(link)) for link in element.find_all("a"))
        score -= (link_text / max(1, text_length)) * 12
        # Prefer the tighter of otherwise similar nested wrappers.
        score += candidate.node_id / max(1, len(page.candidates))
        choices.append((score, element))
    return max(choices, key=lambda item: item[0])[1] if choices else None


def _small_semantic_element(
    page: ParsedPage,
    token_pattern: re.Pattern[str],
    *,
    date: bool,
    preferred_texts: set[str] | None = None,
) -> Tag | None:
    choices: list[tuple[float, Tag]] = []
    for candidate in page.candidates:
        element = candidate.element
        text = _visible_text(element)
        if not text or len(text) > 350:
            continue
        if not date and re.search(r"\babout\s+the\s+author", text, re.IGNORECASE):
            continue
        attrs = _attrs(element)
        attr_match = bool(token_pattern.search(attrs))
        if date:
            semantic = element.name == "time" or element.has_attr("datetime")
            text_match = bool(DATE_LIKE_RE.search(text))
        else:
            rel = element.get("rel", [])
            href = str(element.get("href", ""))
            semantic = (
                element.get("itemprop") == "author"
                or "author" in rel
                or bool(re.search(r"/authors?/", href, re.IGNORECASE))
            )
            text_match = bool(
                re.match(r"^(?i:(?:written\s+)?by)\s+:?\s*[A-Z]", text)
            )
        if date and not (semantic or text_match):
            continue
        if not date and not (attr_match or semantic or text_match):
            continue
        normalized = re.sub(r"^(?:written\s+)?by\s+:?\s*", "", text, flags=re.IGNORECASE)
        preferred = bool(preferred_texts and normalized in preferred_texts)
        score = (
            (30 if semantic else 0)
            + (30 if attr_match else 0)
            + (12 if text_match else 0)
            + (45 if preferred else 0)
        )
        score -= math.log1p(len(text))
        score -= candidate.node_id / max(1, len(page.candidates))
        choices.append((score, element))
    return max(choices, key=lambda item: item[0])[1] if choices else None


def draft_annotation(page_id: str, page: ParsedPage, is_article: bool) -> Annotation:
    if not is_article:
        return Annotation(
            page_id,
            page.html_hash,
            {field: None for field in FIELDS},
            needs_review=True,
            review_status="draft",
        )
    title = _title(page)
    article = _article(page, title)
    authors = _small_semantic_element(
        page,
        AUTHOR_TOKENS,
        date=False,
        preferred_texts=_schema_author_names(page),
    )
    date = _small_semantic_element(page, DATE_TOKENS, date=True)
    labels = {
        Field.ARTICLE: _node_id(page, article),
        Field.TITLE: _node_id(page, title),
        Field.AUTHORS: _node_id(page, authors),
        Field.DATE: _node_id(page, date),
    }
    return Annotation(
        page_id,
        page.html_hash,
        labels,
        needs_review=True,
        review_status="draft",
    )


def bootstrap(dataset_dir: Path, overwrite: bool) -> dict[str, object]:
    manifest = DatasetManifest.load(dataset_dir / "manifest.json")
    annotation_dir = dataset_dir / "annotations"
    counts = {
        "written": 0,
        "preserved": 0,
        "migrated_drafts": 0,
        "migrated_reviewed": 0,
        "needs_review": 0,
    }
    field_counts = {field.value: 0 for field in FIELDS}
    for record in manifest.pages:
        annotation_path = annotation_dir / f"{record.page_id}.json"
        page = parse_html((dataset_dir / record.html_path).read_text(encoding="utf-8"))
        draft = draft_annotation(record.page_id, page, record.is_article)
        if annotation_path.exists() and not overwrite:
            existing = load_annotation(annotation_path)
            if existing.review_status != "legacy":
                counts["preserved"] += 1
                continue
            unchanged_bootstrap = existing.labels == draft.labels
            annotation = Annotation(
                existing.page_id,
                existing.html_hash,
                existing.labels,
                needs_review=True if unchanged_bootstrap else existing.needs_review,
                review_status="draft" if unchanged_bootstrap else "reviewed",
            )
            counts[
                "migrated_drafts" if unchanged_bootstrap else "migrated_reviewed"
            ] += 1
        else:
            annotation = draft
        save_annotation(annotation_path, annotation)
        counts["written"] += 1
        counts["needs_review"] += int(annotation.needs_review)
        for field, node_id in annotation.labels.items():
            field_counts[field.value] += int(node_id is not None)
    return {**counts, "selected_fields": field_counts}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=Path("data/learned_extraction/raw"))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    print(json.dumps(bootstrap(args.dataset_dir, args.overwrite), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
