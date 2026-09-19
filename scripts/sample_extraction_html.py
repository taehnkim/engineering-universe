"""Collect rendered engineering-blog DOMs through browser-use/Chrome.

Each configured source contributes its listing page (a deliberate non-article
example) and up to 30 article pages by default. The collector is incremental:
it preserves existing page IDs and annotations, follows every configured seed,
and rejects duplicate canonical URLs across the whole corpus.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Sequence
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree
import re

from eng_universe.extraction.dom import html_sha256
from eng_universe.extraction.manifest import (
    DatasetManifest,
    PageRecord,
    canonical_url,
    website_splits,
)
from eng_universe.ingest.source_catalog import SOURCES, SourceConfig


LINKS_JS = "JSON.stringify(Array.from(document.querySelectorAll('a[href]')).map(a=>a.href).filter(Boolean))"
UBER_LINKS_JS = """JSON.stringify(Array.from(document.querySelectorAll('a[href]')).filter(a=>{let p=a;for(let i=0;i<6&&p;i++,p=p.parentElement){const t=p.innerText||'';const hs=new Set(Array.from(p.querySelectorAll('a[href*="/blog/"]')).map(x=>x.href.split('#')[0]));if(t.split('\\n').includes('Engineering')&&t.length<1200&&hs.size===1)return true}return false}).map(a=>a.href).filter(Boolean))"""
HTML_JS = "document.documentElement.outerHTML"
UTILITY_SLUGS = frozenset(
    {
        "about",
        "archive",
        "articles",
        "corporate",
        "discover",
        "engineering",
        "feed",
        "followers",
        "following",
        "industry",
        "latest",
        "login",
        "privacy",
        "product",
        "search",
        "subscribe",
    }
)


def _scraped_at() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class BrowserUse:
    def __init__(self, session: str, headed: bool) -> None:
        self.session = session
        self.headed = headed
        self.started = False

    def _command(self, *args: str) -> dict[str, Any]:
        command = ["browser-use", "--session", self.session]
        if self.headed and not self.started and args and args[0] == "open":
            command.append("--headed")
        command.extend(("--json", *args))
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"browser-use failed ({completed.returncode}): "
                f"{completed.stdout.strip()} {completed.stderr.strip()}"
            )
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        if not lines:
            raise RuntimeError("browser-use returned no JSON")
        payload = json.loads(lines[-1])
        if not payload.get("success"):
            raise RuntimeError(str(payload))
        self.started = True
        return payload["data"]

    def open(self, url: str) -> None:
        self._command("open", url)
        # State is intentionally read after every navigation. Besides following
        # the browser-use workflow, this waits for the interactive DOM snapshot.
        self._command("state")

    def evaluate(self, javascript: str) -> Any:
        result = self._command("eval", javascript).get("result")
        return result

    def scroll_down(self) -> None:
        self._command("scroll", "down")
        self._command("state")

    def close(self) -> None:
        subprocess.run(
            ["browser-use", "--session", self.session, "close"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )


def _matches_article(source: SourceConfig, url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    if parsed.netloc.lower() != source.host:
        return False
    slug = path.rsplit("/", 1)[-1].lower()
    if "." in slug and slug.rsplit(".", 1)[-1] in {"atom", "json", "rss", "xml"}:
        return False
    if slug in UTILITY_SLUGS or re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", slug):
        return False
    if source.host in {"medium.com", "netflixtechblog.com"}:
        return bool(re.search(r"-[0-9a-f]{8,}$", slug))
    if source.id == "uber-blog":
        if slug in {
            "advertising",
            "business",
            "community-support",
            "earn",
            "eat",
            "engineering",
            "health",
            "higher-education",
            "merchants",
            "ride",
            "transit",
        }:
            return False
        return bool(re.match(r"^/[a-z]{2}/[a-z]{2}/blog/[^/]+$", path))
    return any(pattern.match(path) for pattern in source.compiled_article_patterns())


def _sitemap_links(source: SourceConfig, limit: int) -> list[str]:
    """Discover article URLs from configured sitemap indexes.

    Sitemaps are only URL discovery. Every returned page is still opened and
    captured from rendered Chrome before it enters the dataset.
    """

    queue = [f"https://{source.host}{path}" for path in source.sitemap_paths]
    visited: set[str] = set()
    results: list[str] = []
    seen_results: set[str] = set()
    while queue and len(visited) < 60 and len(results) < limit:
        sitemap_url = queue.pop(0)
        if sitemap_url in visited:
            continue
        visited.add(sitemap_url)
        try:
            request = Request(sitemap_url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=25) as response:
                body = response.read(25_000_000)
            root = ElementTree.fromstring(body)
        except Exception:
            continue
        locations = [
            (node.text or "").strip()
            for node in root.iter()
            if node.tag.rsplit("}", 1)[-1] == "loc" and node.text
        ]
        if root.tag.rsplit("}", 1)[-1] == "sitemapindex":
            queue.extend(location for location in locations if location not in visited)
            continue
        for location in locations:
            normalized = canonical_url(location)
            if normalized not in seen_results and _matches_article(source, normalized):
                seen_results.add(normalized)
                results.append(normalized)
                if len(results) >= limit:
                    break
    return results


def _article_links(browser: BrowserUse, source: SourceConfig, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for attempt in range(12):
        raw = browser.evaluate(UBER_LINKS_JS if source.id == "uber-blog" else LINKS_JS)
        links = json.loads(raw) if isinstance(raw, str) else raw
        for link in links:
            normalized = canonical_url(str(link))
            if normalized not in seen and _matches_article(source, normalized):
                seen.add(normalized)
                result.append(normalized)
            if len(result) == limit:
                return result
        if attempt < 11:
            browser.scroll_down()
    return result


def _safe_html(browser: BrowserUse) -> str:
    html = browser.evaluate(HTML_JS)
    if not isinstance(html, str) or "<html" not in html.lower():
        raise RuntimeError("rendered page did not return an HTML document")
    return html


def collect(
    output_dir: Path,
    articles_per_source: int,
    session: str,
    headed: bool,
    source_ids: set[str] | None = None,
    refresh: bool = False,
) -> DatasetManifest:
    output_dir.mkdir(parents=True, exist_ok=True)
    html_dir = output_dir / "html"
    html_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    existing = DatasetManifest.load(manifest_path) if manifest_path.exists() else None
    splits = website_splits(
        [source.host for source in SOURCES], validation_count=8, test_count=1
    )
    records = (
        {
            page.page_id: replace(page, split=splits[page.website])
            for page in existing.pages
        }
        if existing
        else {}
    )
    source_by_id = {source.id: source for source in SOURCES}
    for page_id, record in list(records.items()):
        source = source_by_id.get(record.source_id)
        if record.is_article and source and not _matches_article(source, record.url):
            (output_dir / record.html_path).unlink(missing_ok=True)
            (output_dir / "annotations" / f"{page_id}.json").unlink(missing_ok=True)
            records.pop(page_id)
            print(f"pruned non-article {page_id}: {record.url}", flush=True)
    browser = BrowserUse(session, headed)
    selected_sources = [
        source for source in SOURCES if source_ids is None or source.id in source_ids
    ]
    if refresh:
        selected_ids = {source.id for source in selected_sources}
        for page_id, record in list(records.items()):
            if record.source_id in selected_ids:
                (output_dir / record.html_path).unlink(missing_ok=True)
                (output_dir / "annotations" / f"{page_id}.json").unlink(missing_ok=True)
                records.pop(page_id)
        DatasetManifest(2, tuple(sorted(records.values(), key=lambda item: item.page_id))).save(
            manifest_path
        )
    try:
        for source in selected_sources:
            try:
                source_records = [
                    record
                    for record in records.values()
                    if record.source_id == source.id
                ]
                existing_article_count = sum(page.is_article for page in source_records)
                needed = max(0, articles_per_source - existing_article_count)
                seen_urls = {canonical_url(page.url) for page in records.values()}
                if needed == 0 and all(
                    canonical_url(seed_url) in seen_urls
                    for seed_url in source.seed_urls
                ):
                    print(f"complete {source.id}: {existing_article_count} articles")
                    continue
                next_article_number = max(
                    (
                        int(page.page_id.rsplit("-", 1)[-1])
                        for page in source_records
                        if page.is_article
                        and page.page_id.rsplit("-", 1)[-1].isdigit()
                    ),
                    default=0,
                ) + 1
                next_listing_number = 2
                while f"{source.id}-listing-{next_listing_number:03d}" in records:
                    next_listing_number += 1

                links: list[str] = []
                link_set: set[str] = set()
                for raw_seed_url in source.seed_urls:
                    seed_url = canonical_url(raw_seed_url)
                    try:
                        browser.open(seed_url)
                        discovered = _article_links(
                            browser, source, max(needed * 2, articles_per_source)
                        )
                    except Exception as exc:
                        print(
                            f"error collecting seed {seed_url}: {exc}",
                            file=sys.stderr,
                            flush=True,
                        )
                        continue
                    for link in discovered:
                        identity = canonical_url(link)
                        if identity not in seen_urls and identity not in link_set:
                            links.append(identity)
                            link_set.add(identity)

                    if seed_url not in seen_urls:
                        if f"{source.id}-listing" not in records:
                            listing_id = f"{source.id}-listing"
                        else:
                            listing_id = (
                                f"{source.id}-listing-{next_listing_number:03d}"
                            )
                            next_listing_number += 1
                        html = _safe_html(browser)
                        html_path = html_dir / f"{listing_id}.html"
                        html_path.write_text(html, encoding="utf-8")
                        records[listing_id] = PageRecord(
                            page_id=listing_id,
                            source_id=source.id,
                            company=source.company,
                            website=source.host,
                            url=seed_url,
                            html_path=str(html_path.relative_to(output_dir)),
                            split=splits[source.host],
                            capture_kind="browser",
                            html_hash=html_sha256(html),
                            scraped_at=_scraped_at(),
                            is_article=False,
                        )
                        seen_urls.add(seed_url)
                        DatasetManifest(
                            2,
                            tuple(sorted(records.values(), key=lambda item: item.page_id)),
                        ).save(manifest_path)
                        print(f"saved {listing_id}: {seed_url}", flush=True)

                if len(links) < needed and source.sitemap_paths:
                    for link in _sitemap_links(source, max(needed * 4, 100)):
                        identity = canonical_url(link)
                        if identity not in seen_urls and identity not in link_set:
                            links.append(identity)
                            link_set.add(identity)
                    print(
                        f"{source.id}: {len(links)} candidate links after sitemap discovery",
                        flush=True,
                    )

                if len(links) < needed:
                    print(
                        f"warning: {source.id} needs {needed} new articles but exposed "
                        f"only {len(links)} unique links",
                        file=sys.stderr,
                    )
                saved_articles = 0
                for url in links:
                    if saved_articles >= needed:
                        break
                    while f"{source.id}-{next_article_number:03d}" in records:
                        next_article_number += 1
                    page_id = f"{source.id}-{next_article_number:03d}"
                    next_article_number += 1
                    html_path = html_dir / f"{page_id}.html"
                    try:
                        browser.open(url)
                        html = _safe_html(browser)
                    except Exception as exc:
                        print(
                            f"error collecting article {url}: {exc}",
                            file=sys.stderr,
                            flush=True,
                        )
                        continue
                    html_path.write_text(html, encoding="utf-8")
                    records[page_id] = PageRecord(
                        page_id=page_id,
                        source_id=source.id,
                        company=source.company,
                        website=source.host,
                        url=url,
                        html_path=str(html_path.relative_to(output_dir)),
                        split=splits[source.host],
                        capture_kind="browser",
                        html_hash=html_sha256(html),
                        scraped_at=_scraped_at(),
                        is_article=True,
                    )
                    DatasetManifest(2, tuple(sorted(records.values(), key=lambda item: item.page_id))).save(
                        manifest_path
                    )
                    print(f"saved {page_id}: {url}", flush=True)
                    saved_articles += 1
                    time.sleep(0.15)
            except Exception as exc:
                print(f"error collecting {source.id}: {exc}", file=sys.stderr, flush=True)
    finally:
        browser.close()
    manifest = DatasetManifest(
        2, tuple(sorted(records.values(), key=lambda item: item.page_id))
    )
    manifest.save(manifest_path)
    counts = Counter(page.split for page in manifest.pages)
    print(f"split counts: {dict(sorted(counts.items()))}", flush=True)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/learned_extraction/raw")
    )
    parser.add_argument("--articles-per-source", type=int, default=30)
    parser.add_argument("--session", default="embeddings-sample")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--source", action="append", dest="sources")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="replace already captured pages for the selected source(s)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    manifest = collect(
        args.output_dir,
        args.articles_per_source,
        args.session,
        not args.headless,
        set(args.sources) if args.sources else None,
        args.refresh,
    )
    print(f"manifest contains {len(manifest.pages)} pages")


if __name__ == "__main__":
    main()
