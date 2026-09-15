# Eng Universe

Search engine for engineering blogs from companies like Meta, Anthropic, and OpenAI.

Combines keyword (BM25) and semantic search for hybrid results.

## Features

- Crawls engineering blogs with robots.txt compliance
- Hybrid search (keyword + semantic) via Redis
- FastAPI endpoint with configurable search modes
- Prometheus metrics

## Quickstart

1. Set `REDIS_URL` and optional embedding provider env vars
2. If you want R2 storage, set `R2_UPLOAD=true` plus `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` (optional: `R2_REGION`, `R2_ENDPOINT_URL`)
3. Add or review company blog roots in `eng_universe/ingest/source_catalog.py` (see `docs/seeding.md`)
4. Seed and crawl (see [Ingestion runs](#ingestion-runs) below)
5. `uv run python main.py index` (uploads clean text + index JSON to R2; reads raw HTML from R2)
6. `uvicorn api.search:app --reload`

## Ingestion runs

List curated company roots:

```bash
uv run python main.py seed --list
```

One company:

```bash
uv run python main.py seed --url https://stripe.dev/blog
uv run python main.py crawl --max-docs 20 --concurrency 4
```

All companies:

```bash
uv run python main.py seed --catalog
uv run python main.py crawl --max-docs 20 --concurrency 4
```

Optional index after crawl:

```bash
uv run python main.py index
```

| Question | Answer |
|----------|--------|
| Skip already seen URLs? | Yes. `enqueue` uses Redis set `crawl:seen`. Already-seen URLs are not re-queued. |
| Limit run size? | Yes. `--max-docs 20` stops after 20 **stored** articles (not every fetched listing). |
| Limit concurrency? | Yes. `--concurrency 4` (default is `MAX_WORKERS`). |

Reset crawl queues and seen state for a fresh test:

```bash
uv run python scripts/clear_crawl.py
```

## Redis ingestion queue

- `uv run eng-universe ingest enqueue-fetch <url> --config-version <version>` enqueues one `fetch_raw` run.
- `uv run eng-universe ingest run-fetch-worker` runs the Redis-backed fetch worker until it receives a stop signal.
- `LIVE_REDIS_URL=redis://default:devpass@localhost:6379/0 uv run pytest tests/ingest/test_live_redis_smoke.py` runs the isolated live-Redis smoke test.

These commands do not cut over the legacy crawler or indexer.
HTTP response artifact publication to R2 remains deferred.

## Docker

- `docker compose --profile api up` - API + Redis
- `docker compose --profile crawler up` - Crawler
- `docker compose --profile indexer up` - Indexer

## Docs

See `docs/` for detailed specs:

- `docs/seeding.md` — how company blog roots become crawlable posts
- `docs/ingest.md` — ingestion architecture (catalog, contracts, stage queue)
- `docs/spec.md` — product scope and module contracts

