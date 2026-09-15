1. Seed and start

- `python main.py seed` → `seed_catalog()` enqueues each curated listing root at depth=0.
- `python main.py seed --url URL` → enqueue one known listing or article URL.
- Listing seeds also enqueue configured sitemap URLs for that host.
- `python main.py crawl` → `run_crawlers()` starts `max_workers` workers and a shared aiohttp session.

See `docs/seeding.md` for listing vs article rules and depth policy.

2. Worker loop (per item)

- `requeue_delayed_items()` moves delayed items whose time has arrived from `crawl:delay` → `crawl:queue`.
- `dequeue()` pops one `CrawlItem(url, source, depth)`.

3. Robots compliance

- `get_or_fetch_robots()` loads robots.txt from cache or fetches it and stores it in Redis.
- `RobotFileParser.can_fetch()` checks the URL; if disallowed → drop URL.
- `reserve_next_allowed()` atomically enforces crawl-delay + request-rate.
  - If not allowed yet, `delay()` requeues to `crawl:delay` with the next allowed timestamp.

4. Fetch

- `fetch_html()` downloads HTML (timeout guarded).
- Non-200 or fetch failure → drop URL.

5. Link discovery (bounded)

- BeautifulSoup parses HTML.
- `extract_links()` collects anchor URLs, normalizes, filters out mailto/tel/js.
- If `CRAWL_ALLOW_EXTERNAL=false`, keeps only same domain.
- `classify_url()` keeps only listing/article/sitemap paths from the curated catalog.
- If `depth < CRAWL_DEPTH_LIMIT`, enqueue links with depth + 1.

6. Clean and store

- Only `UrlKind.ARTICLE` pages are stored.
- Listings and sitemaps are discovery-only.
- `_clean_container()` keeps article → main → body (removes nav/footer/aside/script/style/noscript).
- `doc_id = INCR crawl:doc_seq`.
- Write raw/clean artifacts when storage is enabled.

7. Metadata record

- Redis `crawl:doc:{doc_id}` stores: url, domain, source, depth, paths, url_hash, fetched_at, status.
