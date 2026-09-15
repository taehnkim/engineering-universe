# Ingestion architecture

## Purpose

`contracts.py` defines the common language for ingestion stages.
It does not fetch, parse, store, or index data.

```text
Python / CLI / worker / API
             |
        StageRequest
             |
       Redis stage runner
             |
        stage handler
             |
        StageResult
```

All callers use the same contract. This prevents duplicate stage logic.

## Source catalog and seeding

Company roots are human-curated. They live in
`eng_universe/ingest/source_catalog.py` as `SOURCES`.

Each `SourceConfig` names:

- `seed_urls` — listing roots (example: `https://stripe.dev/blog`)
- `listing_paths` / `follow_path_patterns` — hubs to crawl for links only
- `article_path_patterns` — path regexes that mark storeable posts
- `sitemap_paths` — optional sitemaps enqueued with a listing seed

`sources.py` classifies normalized URLs as `listing` | `article` | `sitemap` |
`reject`. Only articles are stored. Irrelevant links on a listing page are
dropped at classify time; they are never enqueued.

```text
human finds blog root
        |
        v
source_catalog.SOURCES
        |
        v
seed listing @ depth 0
        |
        +--> follow allowed links (depth < CRAWL_DEPTH_LIMIT)
        |
        +--> store articles only
```

Operator entry points (legacy crawl CLI):

```bash
uv run python main.py seed --url https://stripe.dev/blog   # one company
uv run python main.py seed --catalog                       # all companies
uv run python main.py crawl --max-docs 20 --concurrency 4
```

Already-seen URLs are skipped via Redis set `crawl:seen`.
See `docs/seeding.md` for listing vs article rules and Medium publication scope.

The leased Redis stage queue (`fetch_raw`) still expects exact article URLs as
inputs. Connecting `source_catalog` discovery into that queue remains a later
step; until then, the legacy `seed` / `crawl` path uses the catalog above.

## Legacy crawl worker loop

`python main.py crawl` runs `run_crawlers()` with `max_workers` asyncio
workers and one shared `aiohttp` session. Per queue item:

1. **Promote delays** — `requeue_delayed_items()` moves due work from
   `crawl:delay` → `crawl:queue`.
2. **Dequeue** — `dequeue()` pops one `CrawlItem(url, source, depth)`.
3. **Robots** — `get_or_fetch_robots()` + `can_fetch()`; if denied, drop.
   `reserve_next_allowed()` enforces crawl-delay / request-rate; if too soon,
   `delay()` requeues to `crawl:delay`.
4. **Fetch** — `fetch_html()`; non-200 or transport failure drops the URL.
5. **Discover** — sitemaps parse `<loc>` values; HTML pages extract `<a href>`
   links, keep same-host when `CRAWL_ALLOW_EXTERNAL=false`, keep only paths
   that pass `classify_url()`, and enqueue at `depth + 1` while under
   `CRAWL_DEPTH_LIMIT`.
6. **Store** — only `UrlKind.ARTICLE` pages are stored. Listings and sitemaps
   are discovery-only. `_clean_container()` keeps article → main → body
   (removes nav/footer/aside/script/style/noscript). `doc_id = INCR
   crawl:doc_seq`; raw/clean artifacts write when storage is enabled.
7. **Metadata** — Redis `crawl:doc:{doc_id}` stores url, domain, source,
   depth, paths, url_hash, fetched_at, status.

## Main design

- `StageIdentity` gives each stage a stable name and version.
- `StageInput` exposes only values that can change the result.
- `StageRequest` joins the stage identity, semantic input, and configuration version.
- `StageResult` returns typed output and artifact references.
- `ArtifactRef` points to an immutable object, such as a Cloudflare R2 object.
- `StageStatus` defines states that the Redis runner persists.

Generic input and output types let type checkers find invalid stage connections.
Frozen data classes prevent accidental field reassignment.
Slots keep the contract objects small.

## Idempotency

The key identifies equivalent work:

```text
stage name
    + stage version
    + configuration version
    + semantic input
              |
       canonical JSON
              |
           SHA-256
              |
       idempotency key
```

Canonical JSON sorts mapping keys and rejects unsupported or unstable values.
The same semantic input produces the same key.
A stage, configuration, or input change produces a different key.

A forced run adds a nonce to the run key. It does not change the semantic input.
Forced runs do not promote their result by default.

## Artifact references

Large content does not move through stage requests.
Stages pass an `ArtifactRef` with an object key, content hash, media type, and size.
The hash verifies content identity and supports immutable, content-addressed storage.

## Redis queue and workers

`stage_queue.py` stores versioned run hashes under `eu:v1:`.
Ready and leased work use sorted sets, so due work and expired leases stay bounded.
Lua scripts make enqueue, claim, heartbeat, completion, retry, and reclaim atomic.
Readiness means `state=queued` and `due_at_ms` is not later than Redis server time.
The ready sorted-set score is the same `due_at_ms` value.

`fetch_raw` has one sorted set per origin and a global origin schedule.
The scheduler rotates ready origins, applies request spacing, and limits in-flight work.
A lease token must match before a completion or failure releases an origin slot.

`worker.py` runs asynchronous handler pools and heartbeats active leases.
Handlers are plain async functions. `StageError(kind=...)` selects retryable, permanent, or blocked outcomes.
`fetch_worker.py` exposes `run_fetch_worker()` as the single fetch composition root.
`application.py` builds the configured queue and the `fetch_raw` request.
`eng_universe.cli` exposes that application through the installed `eng-universe` command.

```text
Settings (env)
   |
run_fetch_worker(queue, stop)        fn: lease + session + pool
   |- ProcessLease                   class (token)
   |- make_fetch_handler(...)        fn: robots + HTTP + R2
   `- StageWorkerPool / run_pool     claim -> heartbeat -> complete/fail
         `- StageQueue               class: thin wrapper over the Lua scripts
```

### Run states and failure paths

`StageStatus` values that the queue scripts drive:

```text
enqueue → queued + due_at_ms ──due claim──► leased ──heartbeat──► running
              ▲                                │                      │
              │                                └──── fail/reclaim ────┤
              │                                                       │
              └──── retryable + attempts left + future due_at_ms ─────┘
                                                                      │
                                              complete ──► succeeded  │
                                              permanent/exhausted ◄───┤
                                                    │                 │
                                                    ▼                 │
                                              failed (dead)           │
                                              blocked ◄───────────────┘
```

Lease expiry (watchdog reclaim):

```text
leased/running ──lease expired──► attempt=expired
                     │
         attempts left → queued + new due_at_ms
         none left     → failed
```

Failure kinds:

```text
leased / running
       |
   fail(kind)
       |
 +-----+-----+---------------------+
 |           |                     |
retryable   permanent           blocked
 |           |                     |
 |      state=failed        state=blocked
 |      → dead ZSET        → blocked ZSET
 |
 +-- attempts left → queued + future due_at_ms
 +-- exhausted     → failed     → dead ZSET
```

Default `max_attempts` is 5. Retry delay uses exponential backoff with full jitter.
Queued work is claimable only when its ready sorted-set score is due.
`cancelled` and `skipped` exist on `StageStatus` but have no queue transition yet.

`fetch_boundary.py` applies the existing robots parser to the exact request path.
Redirect handlers must call the checker again before each redirected request.

`fetch_worker.py` owns the v1 fetch process:

- One process holds a token-safe Redis process lease.
- The process starts 100 configurable asyncio fetch workers.
- All workers share one `aiohttp` session and one 100-connection connector.
- Redis also caps global leased fetch runs at 100 across queue clients.
- Redis origin queues still enforce per-origin in-flight and spacing limits.
- Blocking R2 SDK calls use bounded background threads behind a separate semaphore.
- HTTP fetch concurrency uses asyncio coroutines, not thread or process workers.
- The runtime rejects a configured process count other than one.
- A second process also fails the Redis lease, so it cannot multiply the global limit.
- The fetch lease must outlive the HTTP timeout before the runtime can start.
- CPU parsing stays in downstream parser workers, outside the fetch event loop.

## Durability

Redis AOF is enabled with `appendfsync everysec`, and the Redis `/data` directory uses a named Docker volume so restarts keep queue state.

Succeeded run hashes receive a TTL (`STAGE_SUCCEEDED_RUN_TTL_S`, default 7 days). Failed, dead, and blocked runs keep no TTL so operators can inspect them.

Queue record schema version 2 removes the separate retry-wait state.
Schema version 1 run hashes are not read as version 2 records.
PR #3 had no application entrypoint, so this change does not add an in-place data migration.
Clear an experimental version 1 namespace before using the new entrypoint.

## Boundaries

The contract module has no Redis, HTTP, R2, database, or model client.
Tests can use it without infrastructure.
The queue is Redis-only and stores no raw artifact body.
Artifact publication and downstream stage scheduling remain separate concerns.

This change adds only these application commands:

```text
eng-universe ingest enqueue-fetch <url> --config-version <version>
eng-universe ingest run-fetch-worker
```

The enqueue command writes only Redis queue state.
The worker command exposes the existing `fetch_raw` handler.
Publishing HTTP response bytes to R2 and publishing Redis artifact records remain deferred.
The legacy `seed`, `crawl`, and `index` paths remain separate.
This change does not add dual writes, migration, or legacy cutover.
