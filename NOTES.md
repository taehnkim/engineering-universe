# Project Architecture Summary

## ASCII Component Diagram

```
                              SEED PHASE
                                  │
    ┌─────────────────────────────▼─────────────────────────────┐
    │  main.py seed / scripts/seed_urls.py                      │
    │  ┌─────────────────────────────────────────────────────┐  │
    │  │ Curated catalog: eng_universe/ingest/sources.py     │  │
    │  │ • listing roots (stripe.dev/blog, …)                │  │
    │  │ • article path regexes per host                     │  │
    │  │ • optional SEED_START_URLS override                 │  │
    │  └──────────────────────┬──────────────────────────────┘  │
    │                         │ seed_queue() / seed_catalog()   │
    │                         ▼                                 │
    │  ┌─────────────────────────────────────────────────────┐  │
    │  │ Listing seeds also enqueue configured sitemaps      │  │
    │  └──────────────────────┬──────────────────────────────┘  │
    └─────────────────────────┼─────────────────────────────────┘
                              │
                              ▼
    ┌─────────────────────────────────────────────────────────────┐
    │                     REDIS QUEUES                            │
    │  ┌──────────────────┐  ┌──────────────────┐                 │
    │  │ crawl:queue      │  │ crawl:delay      │                 │
    │  │ (FIFO list)      │  │ (sorted set by   │                 │
    │  │                  │  │  wake-up time)   │                 │
    │  └────────┬─────────┘  └────────┬─────────┘                 │
    │           │     promote_due()   │                           │
    │           │◄────────────────────┘                           │
    │           │                                                 │
    │  ┌────────▼─────────┐                                       │
    │  │ crawl:seen       │  (SET - deduplication)                │
    │  └──────────────────┘                                       │
    └─────────────────────────────────────────────────────────────┘
                              │
                              │ dequeue() (queue.py:45)
                              ▼
    ┌─────────────────────────────────────────────────────────────┐
    │           CRAWL WORKERS (N concurrent, default 10)          │
    │           crawl_worker() - crawler.py:238-365               │
    │                                                             │
    │  ┌──────────────────────────────────────────────────────┐   │
    │  │ CHECK 1: robots.txt (robots.py:121)                  │   │
    │  │ • Fetch & cache in Redis: robots:{domain}            │   │
    │  │ • Check can_fetch() for this URL                     │   │
    │  │ • If denied → skip URL, continue                     │   │
    │  └──────────────────────┬───────────────────────────────┘   │
    │                         ▼                                   │
    │  ┌──────────────────────────────────────────────────────┐   │
    │  │ CHECK 2: Rate limit (robots.py:163)                  │   │
    │  │ • Use crawl_delay from robots.txt                    │   │
    │  │ • Lua script: robots:next_allowed:{domain}           │   │
    │  │ • If too soon → delay() back to crawl:delay          │   │
    │  └──────────────────────┬───────────────────────────────┘   │
    │                         ▼                                   │
    │  ┌──────────────────────────────────────────────────────┐   │
    │  │ FETCH: fetch_html() (crawler.py:229)                 │   │
    │  │ • aiohttp GET with timeout                           │   │
    │  │ • Returns CrawlResult(url, status, html)             │   │
    │  └──────────────────────┬───────────────────────────────┘   │
    │                         ▼                                   │
    │  ┌──────────────────────────────────────────────────────┐   │
    │  │ EXTRACT LINKS (crawler.py:133)                       │   │
    │  │ • BeautifulSoup finds all <a href>                   │   │
    │  │ • Normalize URLs, filter by allowed patterns         │   │
    │  │ • Enqueue child links (if depth < limit)             │   │
    │  └──────────────────────┬───────────────────────────────┘   │
    │                         ▼                                   │
    │  ┌──────────────────────────────────────────────────────┐   │
    │  │ CLEAN HTML: _clean_container() (crawler.py:94)       │   │
    │  │ • Remove: nav, footer, aside, script, style          │   │
    │  │ • Keep: <article> or <main> or <body>                │   │
    │  └──────────────────────┬───────────────────────────────┘   │
    │                         ▼                                   │
    │  ┌──────────────────────────────────────────────────────┐   │
    │  │ STORE (crawler.py:318-336)                           │   │
    │  │                                                      │   │
    │  │ Filesystem:                                          │   │
    │  │   data/crawl/{doc_id}/raw_{hash}.html                │   │
    │  │   data/crawl/{doc_id}/clean_{hash}.html              │   │
    │  │                                                      │   │
    │  │ Redis hash: crawl:doc:{doc_id}                       │   │
    │  │   url, domain, source, depth, paths, fetched_at      │   │
    │  └──────────────────────┬───────────────────────────────┘   │
    └─────────────────────────┼───────────────────────────────────┘
                              │
                              │ rpush(raw:queue, doc_id)
                              ▼
    ┌─────────────────────────────────────────────────────────────┐
    │                     REDIS: raw:queue                        │
    │                 (doc IDs ready for indexing)                │
    └─────────────────────────┬───────────────────────────────────┘
                              │
                              │ [SEPARATE STEP - index_worker()]
                              ▼
    ┌─────────────────────────────────────────────────────────────┐
    │              INDEX WORKER (pipeline.py:22-70)               │
    │                                                             │
    │  ┌──────────────────────────────────────────────────────┐   │
    │  │ 1. Pop doc_id from raw:queue                         │   │
    │  │ 2. Read crawl:doc:{doc_id} metadata                  │   │
    │  │ 3. Read HTML files from filesystem                   │   │
    │  └──────────────────────┬───────────────────────────────┘   │
    │                         ▼                                   │
    │  ┌──────────────────────────────────────────────────────┐   │
    │  │ ETL: parse_html() (etl.py:79-100)                    │   │
    │  │ Extract: title, content, authors, company,           │   │
    │  │          published_at, canonical_url, language       │   │
    │  └──────────────────────┬───────────────────────────────┘   │
    │                         ▼                                   │
    │  ┌──────────────────────────────────────────────────────┐   │
    │  │ EMBED: get_embedding_provider() (indexer.py:66)      │   │
    │  │ • Generate vector from title + content               │   │
    │  │ • Normalize to configured dimensions                 │   │
    │  └──────────────────────┬───────────────────────────────┘   │
    │                         ▼                                   │
    │  ┌──────────────────────────────────────────────────────┐   │
    │  │ TOPICS: extract_topics() (entities.py)               │   │
    │  │ • Regex match against DEFAULT_TOPICS                 │   │
    │  │   (Kafka, Redis, Spark, Flink, etc.)                 │   │
    │  └──────────────────────┬───────────────────────────────┘   │
    │                         ▼                                   │
    │  ┌──────────────────────────────────────────────────────┐   │
    │  │ STORE INDEX RECORD (indexer.py:118)                  │   │
    │  │ Redis hash: doc:{doc_id}                             │   │
    │  │   title, content, topics, embedding, url, etc.       │   │
    │  └──────────────────────────────────────────────────────┘   │
    └─────────────────────────────────────────────────────────────┘
                              │
                              ▼
    ┌─────────────────────────────────────────────────────────────┐
    │              REDIS SEARCH INDEX: idx:blogs                  │
    │  • Text fields: title, content, url                         │
    │  • Tag fields: topics, source, company, authors             │
    │  • Vector field: embedding (for semantic search)            │
    └─────────────────────────────────────────────────────────────┘
```

## Data Flow Q&A

### 1. Which URLs get seeded?
- Curated listing roots in `eng_universe/ingest/sources.py` (`SOURCES`)
- Default CLI: `python main.py seed` seeds the full catalog
- Override: `SEED_START_URLS` or `python main.py seed --url <listing-or-article>`
- Listing seeds also enqueue configured sitemap.xml URLs for that host
- See `docs/seeding.md` for listing vs article vs reject rules

### 2. How does it get added to queue?
- `seed_queue()` (crawler.py:399) normalizes URL and calls `enqueue()`
- `enqueue()` (queue.py:35-42) does:
  - `SADD crawl:seen {url}` — dedup check (returns 0 if already seen)
  - `RPUSH crawl:queue {serialized_item}` — add to FIFO queue

### 3. What pulls from queue?
- `crawl_worker()` (crawler.py:238) runs N concurrent workers (default 10)
- Each worker calls `dequeue()` → `LPOP crawl:queue`
- Also calls `promote_due()` to move delayed items back when ready

### 4. What checks before processing?
1. **Dedup**: Already handled at enqueue time via `crawl:seen` set
2. **robots.txt**: `get_or_fetch_robots()` fetches & caches, then `can_fetch()` check
3. **Rate limit**: `reserve_next_allowed()` Lua script checks domain timing
   - If too soon → `delay()` puts item in `crawl:delay` sorted set with wake-up timestamp

### 5. What happens when worker processes URL?
1. **Download**: `fetch_html()` — aiohttp GET, returns raw HTML
2. **Normalize**: `_clean_container()` — removes nav/footer/script/style, keeps article/main
3. **Store**:
   - Files: `data/crawl/{doc_id}/raw_{hash}.html` and `clean_{hash}.html`
   - Redis: `crawl:doc:{doc_id}` hash with metadata
4. **Queue for index**: `RPUSH raw:queue {doc_id}`

### 6. Immediate index or separate step?
**Separate step.** Crawling pushes doc_ids to `raw:queue`. Indexing is a separate worker that:
- Pops from `raw:queue`
- Reads HTML from filesystem
- Parses, embeds, extracts topics
- Stores to `doc:{doc_id}` hash
- Creates Redis Search index

In `scripts/crawl_then_index.py`, indexing runs **after** crawling completes (sequential).
In production, you could run them in parallel as separate processes.

## CLI Commands (main.py)

| Command | What it does |
|---------|--------------|
| `seed` | Populate crawl:queue from curated catalog or `--url` |
| `crawl` | Run crawler workers |
| `index` | Run index workers |
| `init-index` | Create Redis Search index schema |
| `reindex` | init-index + index |
| `metrics` | Start Prometheus metrics server |
