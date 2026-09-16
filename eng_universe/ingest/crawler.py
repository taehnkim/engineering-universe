import asyncio
import hashlib
import time
import uuid
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlparse

import aiohttp
import redis.asyncio as redis
from bs4 import BeautifulSoup

import eng_universe.storage.r2 as r2
from eng_universe.config import Settings
from eng_universe.ingest.queue import (
    CrawlItem,
    acknowledge,
    delay,
    dequeue,
    enqueue,
    enqueue_raw_index,
    requeue_delayed_items,
    stage_queue,
)
from eng_universe.ingest.queue_models import CRAWL_STAGE, StageLease
from eng_universe.ingest.robots import (
    can_fetch_path,
    get_or_fetch_robots,
    parse_domain,
    reserve_next_allowed,
)
from eng_universe.ingest.sources import (
    UrlKind,
    all_seed_urls,
    classify_url,
    is_allowed_url,
    is_sitemap_url,
    resolve_seed_url,
    sitemap_urls_for_host,
)
from eng_universe.monitoring.logging_utils import get_event_logger
from eng_universe.monitoring.metrics import record_crawl


@dataclass
class CrawlResult:
    """Stores one successful HTTP crawl response."""

    url: str
    status: int
    html: str


UNWANTED_TAGS = ("nav", "footer", "aside", "script", "style", "noscript")

log_event = get_event_logger("crawler")


def _clean_container(soup: BeautifulSoup) -> BeautifulSoup:
    for tag_name in UNWANTED_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()
    return soup.find("article") or soup.find("main") or soup.body or soup


def clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return str(_clean_container(soup))


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    container = _clean_container(soup)
    return " ".join(container.get_text(" ", strip=True).split())


def normalize_url(url: str) -> str | None:
    url = url.strip()
    if not url:
        return None
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.netloc:
        return None
    netloc = parsed.netloc.lower()
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    elif netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    normalized = parsed._replace(scheme=scheme, netloc=netloc, path=path)
    return normalized.geturl()


def extract_links_from_soup(soup: BeautifulSoup, base_url: str) -> set[str]:
    links: set[str] = set()
    for tag in soup.find_all("a", href=True):
        href = tag.get("href")
        if not href:
            continue
        href = href.strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(base_url, href)
        normalized = normalize_url(absolute)
        if normalized:
            links.add(normalized)
    return links


def sitemap_urls_for_domain(domain: str) -> set[str]:
    urls = sitemap_urls_for_host(domain)
    if not urls:
        print(f"[sitemap_urls_for_domain] Ignored {domain}")
    return urls


def parse_sitemap_links(xml_text: str) -> set[str]:
    if not xml_text.lstrip().startswith("<"):
        return set()
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return set()

    def tag_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    root_tag = tag_name(root.tag)
    if root_tag not in {"urlset", "sitemapindex"}:
        return set()

    urls: set[str] = set()
    for loc in root.iter():
        if tag_name(loc.tag) != "loc":
            continue
        if loc.text:
            urls.add(loc.text.strip())
    return urls


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


async def fetch_html(
    session: aiohttp.ClientSession, url: str
) -> tuple[CrawlResult | None, Exception | None]:
    try:
        async with session.get(url, timeout=Settings.request_timeout_s) as response:
            html = await response.text()
            return CrawlResult(url=url, status=response.status, html=html), None
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        return None, exc


async def check_robots_txt(
    redis_client: redis.Redis,
    session: aiohttp.ClientSession,
    item: CrawlItem,
    lease: StageLease,
) -> str | None:
    domain = parse_domain(item.url)
    rules = await get_or_fetch_robots(redis_client, session, domain)
    if not can_fetch_path(rules.text, Settings.user_agent, item.url):
        log_event("deny", url=item.url, reason="robots")
        return None
    min_delay_s = max(rules.crawl_delay_s, rules.request_rate_s)
    allowed, next_allowed = await reserve_next_allowed(
        redis_client, domain, min_delay_s
    )
    if not allowed:
        await delay(redis_client, lease, next_allowed)
        log_event("delay", url=item.url, until=next_allowed)
        return None
    return domain


async def parse_sitemap(
    redis_client: redis.Redis, item: CrawlItem, result: CrawlResult
) -> None:
    sitemap_links = parse_sitemap_links(result.html)
    for link in sitemap_links:
        normalized = normalize_url(link)
        if not normalized or not is_allowed_url(normalized):
            continue
        await enqueue(
            redis_client,
            CrawlItem(url=normalized, source="sitemap", depth=item.depth + 1),
        )
    log_event("sitemap", url=item.url, links=len(sitemap_links))


async def extract_links(
    redis_client: redis.Redis,
    item: CrawlItem,
    result: CrawlResult,
    domain: str,
) -> None:
    soup = BeautifulSoup(result.html, "html.parser")
    links = extract_links_from_soup(soup, result.url)
    if not Settings.crawl_allow_external:
        links = {link for link in links if parse_domain(link) == domain}
    links = {link for link in links if is_allowed_url(link)}
    if item.depth < Settings.crawl_depth_limit:
        next_depth = item.depth + 1
        for link in links:
            if link == item.url:
                continue
            await enqueue(
                redis_client,
                CrawlItem(url=link, source=item.source, depth=next_depth),
            )


async def upload_r2(
    redis_client: redis.Redis,
    doc_key_prefix: str,
    item: CrawlItem,
    result: CrawlResult,
    domain: str,
) -> bool:
    kind = classify_url(item.url)
    should_store = kind == UrlKind.ARTICLE
    if not should_store:
        log_event("skip", url=item.url, reason=kind.value)
        return False
    if not r2.r2_enabled():
        log_event("skip", url=item.url, reason="r2_disabled")
        return False

    doc_id = int(await redis_client.incr(Settings.crawl_doc_seq_key))
    raw_key = f"raw/{doc_id}.html"
    clean_key = f"clean/{doc_id}.txt"
    try:
        await asyncio.to_thread(
            r2.upload_html,
            result.html,
            raw_key,
        )
    except Exception as exc:
        log_event("r2_fail", url=item.url, error=type(exc).__name__)
        return False

    await redis_client.hset(
        f"{doc_key_prefix}{doc_id}",
        mapping={
            "url": item.url,
            "domain": domain,
            "source": item.source,
            "depth": item.depth,
            "raw_key": raw_key,
            "clean_key": clean_key,
            "url_hash": url_hash(item.url),
            "fetched_at": int(time.time()),
            "status": result.status,
        },
    )
    await enqueue_raw_index(redis_client, doc_id)
    log_event(
        "stored",
        id=doc_id,
        url=item.url,
        raw=raw_key,
    )
    return True


async def crawl_worker(
    redis_client: redis.Redis,
    session: aiohttp.ClientSession,
    doc_key_prefix: str,
    stop_event: asyncio.Event | None = None,
    max_docs: int | None = None,
    counter: list[int] | None = None,
    counter_lock: asyncio.Lock | None = None,
) -> None:
    worker_id = f"crawler:{uuid.uuid4().hex}"
    while True:
        if stop_event and stop_event.is_set():
            return
        await requeue_delayed_items(redis_client)
        await stage_queue(redis_client).reclaim_expired(CRAWL_STAGE)
        claimed = await dequeue(redis_client, worker_id=worker_id)
        if claimed is None:
            await asyncio.sleep(0.2)
            continue
        item, lease = claimed
        log_event(
            "pick",
            url=item.url,
            depth=item.depth,
            source=item.source,
        )

        # Check robots.txt for rate limit
        domain = await check_robots_txt(redis_client, session, item, lease)
        if domain is None:
            run = await stage_queue(redis_client).get_run(lease.run.run_id)
            if run is not None and run.state.value != "queued":
                await acknowledge(redis_client, lease)
            continue

        # Fetch url
        result, fetch_error = await fetch_html(session, item.url)
        if result is None or result.status >= 400:
            log_payload = {
                "url": item.url,
                "status": result.status if result else "error",
            }
            if fetch_error is not None:
                log_payload["error"] = str(fetch_error)
                log_payload["error_type"] = type(fetch_error).__name__
            log_event("fail", **log_payload)
            await acknowledge(redis_client, lease)
            continue

        # Parse sitemap
        if is_sitemap_url(result.url):
            await parse_sitemap(redis_client, item, result)
            await acknowledge(redis_client, lease)
            continue

        # Extract links from url n levels deep
        await extract_links(redis_client, item, result, domain)

        # Store raw html to r2
        stored = await upload_r2(redis_client, doc_key_prefix, item, result, domain)
        record_crawl(domain)
        await acknowledge(redis_client, lease)

        # Check if max crawl limit reached
        if (
            stored
            and max_docs is not None
            and stop_event is not None
            and counter is not None
            and counter_lock is not None
        ):
            async with counter_lock:
                counter[0] += 1
                if counter[0] >= max_docs:
                    stop_event.set()


async def run_crawlers(
    doc_key_prefix: str | None = None, max_docs: int | None = None
) -> None:
    redis_client = redis.from_url(Settings.redis_url)
    prefix = doc_key_prefix or Settings.crawl_doc_key_prefix
    if max_docs is not None and max_docs <= 0:
        return
    stop_event = asyncio.Event() if max_docs is not None else None
    counter = [0] if max_docs is not None else None
    counter_lock = asyncio.Lock() if max_docs is not None else None
    async with aiohttp.ClientSession(
        headers={"User-Agent": Settings.user_agent}
    ) as session:
        workers = [
            asyncio.create_task(
                crawl_worker(
                    redis_client,
                    session,
                    prefix,
                    stop_event=stop_event,
                    max_docs=max_docs,
                    counter=counter,
                    counter_lock=counter_lock,
                )
            )
            for _ in range(Settings.max_workers)
        ]
        await asyncio.gather(*workers)


async def seed_queue(seed_url: str, source: str = "seed") -> None:
    redis_client = redis.from_url(Settings.redis_url)
    normalized = normalize_url(seed_url)
    log_event("seed_queue", normalized_url=normalized)
    if not normalized:
        return
    kind, error = resolve_seed_url(normalized)
    if error:
        log_event("seed_reject", url=normalized, reason=error)
        raise ValueError(error)
    item_source = "sitemap" if kind == UrlKind.SITEMAP else source
    await enqueue(
        redis_client, CrawlItem(url=normalized, source=item_source, depth=0)
    )
    if kind != UrlKind.LISTING:
        return
    for sitemap_url in sitemap_urls_for_domain(parse_domain(normalized)):
        log_event(
            "seed_queue", domain=parse_domain(normalized), sitemap_url=sitemap_url
        )
        await enqueue(
            redis_client,
            CrawlItem(url=sitemap_url, source="sitemap", depth=0),
        )


async def seed_catalog() -> list[str]:
    """Enqueue every curated listing root from the source catalog."""
    seeded: list[str] = []
    for url in all_seed_urls():
        await seed_queue(url)
        seeded.append(url)
    return seeded
