# Lua functions

Redis Lua scripts used by ingest. All scripts live in `lua_fns.py` and are loaded via `EVALSHA`.

| Script | Module | Purpose |
|--------|--------|---------|
| `ENQUEUE_RUN` | `lua_fns.py` | Create run hash, idempotency key, ready-queue membership |
| `CLAIM_RUN` | `lua_fns.py` | Claim one due general-stage run (ready → leased) |
| `CLAIM_FETCH_RUN` | `lua_fns.py` | Claim fetch_raw with origin/global concurrency limits |
| `HEARTBEAT_RUN` | `lua_fns.py` | Extend lease; mark run as running |
| `DEFER_RUN` | `lua_fns.py` | Return a leased run to queued with a future due time |
| `COMPLETE_RUN` | `lua_fns.py` | Mark succeeded; release origin slots; optional TTL |
| `FAIL_RUN` | `lua_fns.py` | Retry, dead-letter, or block a failed run |
| `RECLAIM_RUN` | `lua_fns.py` | Recover expired leases into retry_wait or failed |
| `CONFIGURE_ORIGIN` | `lua_fns.py` | Update origin rate limits; reschedule fetch work |
| `RESERVE_NEXT_ALLOWED` | `lua_fns.py` | Atomically reserve next allowed crawl time per domain |
| `COMPARE_EXPIRE` | `lua_fns.py` | Extend lease TTL when holder token still matches |
| `COMPARE_DELETE` | `lua_fns.py` | Delete lease key when holder token still matches |
