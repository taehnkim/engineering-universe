from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from eng_universe.extraction.postprocess import (
    derive_published_at,
    extract_relative_publication_date,
    is_reading_time,
    normalize_scraped_at,
)


@dataclass
class ParsedDocument:
    url: str
    title: str
    content: str
    authors: list[str]
    summary: str | None
    company: str
    published_at: str | None
    relative_date: str | None
    scraped_at: str
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


def _extract_displayed_date(soup: BeautifulSoup) -> str | None:
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
        return None if is_reading_time(text) else (text or None)
    return None


def _extract_relative_date(soup: BeautifulSoup) -> str | None:
    candidates = list(soup.find_all("time"))
    candidates.extend(
        soup.select(
            '[class*="date" i], [class*="publish" i], [class*="timestamp" i], '
            '[itemprop="datePublished"]'
        )
    )
    seen: set[int] = set()
    for element in candidates:
        if id(element) in seen:
            continue
        seen.add(id(element))
        text = element.get_text(" ", strip=True)
        if len(text) <= 160:
            phrase = extract_relative_publication_date(text)
            if phrase:
                return phrase
    return None


def _extract_summary(soup: BeautifulSoup) -> str | None:
    return _extract_meta_content(
        soup, ["description", "og:description", "twitter:description"]
    )


def _company_from_url(url: str) -> str:
    domain = urlparse(url).netloc
    if "fb.com" in domain or "meta" in domain:
        return "Meta"
    return domain


def parse_html(
    url: str,
    html: str,
    scraped_at: str | datetime | None = None,
) -> ParsedDocument:
    soup = BeautifulSoup(html, "html.parser")
    normalized_scraped_at = normalize_scraped_at(
        scraped_at or datetime.now(timezone.utc)
    )
    _remove_unwanted_tags(soup)
    main = _select_main(soup)
    title = _extract_meta_content(soup, ["og:title", "twitter:title"]) or (
        soup.title.string.strip() if soup.title and soup.title.string else ""
    )
    canonical = _extract_meta_content(soup, ["og:url"])
    if not canonical:
        link = soup.find("link", rel="canonical")
        canonical = link.get("href") if link else None
    content = " ".join(main.get_text(" ", strip=True).split())
    displayed_date = _extract_displayed_date(soup)
    relative_date = _extract_relative_date(soup)
    return ParsedDocument(
        url=url,
        title=title,
        content=content,
        authors=_extract_authors(soup),
        summary=_extract_summary(soup),
        company=_company_from_url(url),
        published_at=derive_published_at(
            displayed_date, relative_date, normalized_scraped_at
        ),
        relative_date=relative_date,
        scraped_at=normalized_scraped_at,
        canonical_url=canonical,
        language=_extract_meta_content(soup, ["og:locale", "language"]),
    )
