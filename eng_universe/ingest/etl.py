from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlparse

from bs4 import BeautifulSoup

_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_CONTENT_FIELD_NAMES = frozenset(
    {"content", "body", "html", "markdown", "articlebody", "article_body", "postbody"}
)
_MIN_EMBEDDED_CHARS = 120
_MARKDOWN_HINT_RE = re.compile(
    r"(^|\n)#{1,6}\s|(^|\n)```|(^|\n)[-*+]\s|\[[^\]]+\]\([^)]+\)",
)
_HTML_TAG_RE = re.compile(r"<[a-zA-Z][^>]*>")


@dataclass
class ParsedDocument:
    url: str
    title: str
    content: str
    authors: list[str]
    company: str
    published_at: str | None
    canonical_url: str | None
    language: str | None


def _remove_unwanted_tags(soup: BeautifulSoup) -> None:
    for tag_name in ("nav", "footer", "aside", "script", "style", "noscript"):
        for tag in soup.find_all(tag_name):
            tag.decompose()


def _select_main(soup: BeautifulSoup) -> BeautifulSoup:
    main = soup.find("main")
    if main:
        return main
    return soup


def _extract_meta_content(soup: BeautifulSoup, names: Iterable[str]) -> str | None:
    for name in names:
        meta = soup.find("meta", attrs={"property": name}) or soup.find(
            "meta", attrs={"name": name}
        )
        if meta and meta.get("content"):
            return str(meta["content"])
    return None


def _extract_authors(soup: BeautifulSoup) -> list[str]:
    authors = []
    meta_author = _extract_meta_content(soup, ["author", "article:author"])
    if meta_author:
        authors.extend([part.strip() for part in meta_author.split(",") if part.strip()])
    for tag in soup.select("[rel='author']"):
        text = tag.get_text(strip=True)
        if text and text not in authors:
            authors.append(text)
    return authors


def _extract_published_at(soup: BeautifulSoup) -> str | None:
    meta_date = _extract_meta_content(
        soup, ["article:published_time", "article:modified_time", "publish_date"]
    )
    if meta_date:
        return meta_date
    time_tag = soup.find("time")
    if time_tag and time_tag.get("datetime"):
        return str(time_tag["datetime"])
    if time_tag:
        text = time_tag.get_text(strip=True)
        return text or None
    return None


def _company_from_url(url: str) -> str:
    domain = urlparse(url).netloc
    if "fb.com" in domain or "meta" in domain:
        return "Meta"
    return domain


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _markdown_to_text(markdown: str) -> str:
    """Best-effort Markdown → plain text without a Markdown dependency."""
    text = markdown.replace("\r\n", "\n")
    # Fenced code blocks → keep inner text
    text = re.sub(
        r"```[^\n]*\n(.*?)```",
        lambda m: "\n" + m.group(1).strip() + "\n",
        text,
        flags=re.DOTALL,
    )
    # Images ![alt](url) → alt
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    # Links [label](url) → label
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Headings / emphasis / inline code
    text = re.sub(r"(^|\n)#{1,6}\s+", r"\1", text)
    text = re.sub(r"[*`_]{1,3}", "", text)
    # List markers
    text = re.sub(r"(^|\n)\s*[-*+]\s+", r"\1", text)
    text = re.sub(r"(^|\n)\s*\d+\.\s+", r"\1", text)
    # Blockquote markers
    text = re.sub(r"(^|\n)>\s?", r"\1", text)
    return _normalize_text(text)


def _payload_to_text(payload: str) -> str:
    stripped = payload.strip()
    if not stripped:
        return ""
    if _HTML_TAG_RE.search(stripped[:800]):
        soup = BeautifulSoup(stripped, "html.parser")
        return _normalize_text(soup.get_text(" ", strip=True))
    if _MARKDOWN_HINT_RE.search(stripped):
        return _markdown_to_text(stripped)
    return _normalize_text(stripped)


def _walk_content_strings(
    obj: Any, path: str = ""
) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{path}.{key}" if path else str(key)
            if isinstance(value, str) and key.lower() in _CONTENT_FIELD_NAMES:
                if len(value.strip()) >= _MIN_EMBEDDED_CHARS:
                    found.append((child, value))
            found.extend(_walk_content_strings(value, child))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            # Bound breadth; deep trees still covered via recursion on dicts.
            if index > 80:
                break
            found.extend(_walk_content_strings(value, f"{path}[{index}]"))
    return found


def _extract_next_data_body(html: str) -> str | None:
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None

    # Prefer the known Stripe path when present.
    preferred_paths = (
        ("props", "pageProps", "postData", "content"),
        ("props", "pageProps", "post", "content"),
        ("props", "pageProps", "page", "content"),
    )
    for parts in preferred_paths:
        cursor: Any = data
        for part in parts:
            if not isinstance(cursor, dict) or part not in cursor:
                cursor = None
                break
            cursor = cursor[part]
        if isinstance(cursor, str) and len(cursor.strip()) >= _MIN_EMBEDDED_CHARS:
            return _payload_to_text(cursor)

    candidates = _walk_content_strings(data)
    if not candidates:
        return None
    # Longest content-like field wins (article body beats short bios).
    candidates.sort(key=lambda item: len(item[1]), reverse=True)
    return _payload_to_text(candidates[0][1])


def _extract_json_ld_article_body(soup: BeautifulSoup) -> str | None:
    best: str | None = None
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            types = item.get("@type", "")
            type_names = (
                {types.lower()}
                if isinstance(types, str)
                else {str(t).lower() for t in types}
            )
            if not type_names.intersection(
                {"blogposting", "techarticle", "article", "newsarticle"}
            ):
                continue
            body = item.get("articleBody")
            if isinstance(body, str) and len(body.strip()) >= _MIN_EMBEDDED_CHARS:
                text = _payload_to_text(body)
                if best is None or len(text) > len(best):
                    best = text
    return best


def _article_body_dom_empty(html: str) -> bool:
    """True when a CSS-module articleBody node exists but has no text."""
    match = re.search(
        r'class="[^"]*articleBody[^"]*"[^>]*>(.*?)</div>',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return False
    inner = re.sub(r"<[^>]+>", "", match.group(1))
    return len(inner.strip()) == 0


def _choose_body(dom_text: str, embedded_text: str | None, html: str) -> str:
    if not embedded_text:
        return dom_text
    if not dom_text:
        return embedded_text
    # Stripe-style SSR shells: empty articleBody + long embedded Markdown.
    if _article_body_dom_empty(html) and len(embedded_text) > len(dom_text):
        return embedded_text
    # Thin DOM chrome vs richer embedded payload (JSON-LD / NEXT_DATA).
    if len(dom_text) < 200 and len(embedded_text) > len(dom_text):
        return embedded_text
    # Prefer embedded when it is substantially longer (DOM is chrome/teasers).
    if len(embedded_text) >= max(800, int(len(dom_text) * 1.5)):
        return embedded_text
    return dom_text


def parse_html(url: str, html: str) -> ParsedDocument:
    # Embedded-data hooks must run before script tags are stripped.
    next_body = _extract_next_data_body(html)
    soup = BeautifulSoup(html, "html.parser")
    json_ld_body = _extract_json_ld_article_body(soup)
    embedded = next_body or json_ld_body
    if next_body and json_ld_body and len(json_ld_body) > len(next_body) * 1.2:
        embedded = json_ld_body

    _remove_unwanted_tags(soup)
    main = _select_main(soup)
    title = _extract_meta_content(soup, ["og:title", "twitter:title"]) or (
        soup.title.string.strip() if soup.title and soup.title.string else ""
    )
    canonical = _extract_meta_content(soup, ["og:url"])
    if not canonical:
        link = soup.find("link", rel="canonical")
        canonical = link.get("href") if link else None
    dom_content = _normalize_text(main.get_text(" ", strip=True))
    content = _choose_body(dom_content, embedded, html)
    return ParsedDocument(
        url=url,
        title=title,
        content=content,
        authors=_extract_authors(soup),
        company=_company_from_url(url),
        published_at=_extract_published_at(soup),
        canonical_url=canonical,
        language=_extract_meta_content(soup, ["og:locale", "language"]),
    )
