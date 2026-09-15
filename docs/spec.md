# Eng Universe Spec

## Scope

- Crawl engineering blogs from the curated source catalog in
  `eng_universe/ingest/source_catalog.py` (Meta, Ramp, Anthropic, OpenAI, Stripe,
  Airbnb Medium publication, and others).
- Provide hybrid search (BM25 + vector) with Redis Stack/RediSearch.
- Serve a minimal HTML/JS frontend and a Vercel-hosted API.

## Modules and Contracts

### Source Discovery

- Input: curated `SourceConfig` rows (host, listing roots, article path rules).
- Operator seeds a listing root such as `https://stripe.dev/blog`.
- Output: URLs to crawl (sitemap locs + in-domain links that pass `classify_url`).
- Emit: `CrawlQueue` events with `{url, source, discovered_at}`.
- Non-matching paths (careers, other Medium pubs, marketing) are rejected.
- Details: `docs/seeding.md`.

### Scraper

- Input: URL from `CrawlQueue`.
- Output: raw HTML stored in Redis (`raw:{doc_id}`).
- Use BeautifulSoup and retain `main` content when present.
- Remove nav/footer/aside/script/style to reduce boilerplate.
- Respect robots.txt (crawl-delay, allow/deny).

### ETL Parser

- Input: raw HTML.
- Output: structured document: title, authors, company, published_at, content, canonical URL.
- Normalize: language/locale, whitespace, dedupe by canonical URL.

### Entity Extractor

- Input: parsed content.
- Output: topics/entities (Kafka, Flink, etc.).
- Lightweight extraction for low latency.

### Indexer

- Input: structured doc + entities.
- Output: Redis hashes and embeddings.
- Fields: `doc_id`, `title`, `content`, `topics`, `source`, `company`, `authors`,
  `published_at`, `url`, `lang`, `embedding`.
- Embedding provider is swappable via env config.

### Search API

- `GET /api/search?q=...&mode=keyword|hybrid|semantic&limit=...`
- Keyword path targets sub-50ms. Hybrid best-effort, cached.

### Web UI

- Single page: search input, mode dropdown, results list.
- Calls `/api/search`.

### Snapshot/Backup

- Redis RDB snapshots to object storage (hourly + daily retention).
- Restore runbook documented in deployment notes.

### Metrics

- Track: crawler pages/sec, indexer docs/sec, search latency (ms).
- Export Prometheus metrics for dashboards.
