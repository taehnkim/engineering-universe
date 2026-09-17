from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlparse

from bs4 import BeautifulSoup


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
    # Prefer <main> (stricter page container). Then article, then body.
    # Taking the first <article> before <main> can grab related-post teasers.
    return soup.find("main") or soup.find("article") or soup.body or soup


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


def parse_html(url: str, html: str) -> ParsedDocument:
    soup = BeautifulSoup(html, "html.parser")
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
