# Ingestion contract architecture

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
        Stage.execute
             |
        StageResult
```

All callers use the same contract. This prevents duplicate stage logic.

## Main design

- `StageIdentity` gives each stage a stable name and version.
- `StageInput` exposes only values that can change the result.
- `StageRequest` joins the stage identity, semantic input, and configuration version.
- `ExecutionPolicy` keeps force and promotion controls out of semantic input.
- `StageContext` gives run metadata to the stage.
- `StageResult` returns typed output and artifact references.
- `ArtifactRef` points to an immutable object, such as a Cloudflare R2 object.
- `StageStatus` defines states that the Redis runner persists.
- `Stage` is a protocol. A stage does not need to inherit a base class.

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

`stage_queue.py` stores versioned run and attempt hashes under `eu:v1:`.
Ready and leased work use sorted sets, so due work and expired leases stay bounded.
Lua scripts make enqueue, claim, heartbeat, completion, retry, and reclaim atomic.

`fetch_raw` has one sorted set per origin and a global origin schedule.
The scheduler rotates ready origins, applies request spacing, and limits in-flight work.
A lease token must match before a completion or failure releases an origin slot.

`worker.py` runs asynchronous handler pools and heartbeats active leases.
`ContractStageHandler` adapts the typed `Stage` protocol to persisted queue records.
Typed failures select retryable, permanent, or blocked outcomes.

### Run states and failure paths

`StageStatus` values that the queue scripts drive:

```text
enqueue → queued ──claim──► leased ──heartbeat──► running ──complete──► succeeded
                              │                      │
                              └──────── fail / reclaim ────────┐
                                                               ▼
                                              retryable+attempts → retry_wait ──claim──► leased
                                              permanent/exhausted → failed (dead)
                                              blocked             → blocked
```

Lease expiry (watchdog reclaim):

```text
leased/running ──lease expired──► attempt=expired
                     │
         attempts left → retry_wait
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
 +-- attempts left → retry_wait → ready again
 +-- exhausted     → failed     → dead ZSET
```

Default `max_attempts` is 5. Retry delay uses exponential backoff with full jitter.
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


## Boundaries

The contract module has no Redis, HTTP, R2, database, or model client.
Tests can use it without infrastructure.
The queue is Redis-only and stores no raw artifact body.
Artifact publication and downstream stage scheduling remain separate concerns.
