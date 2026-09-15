from __future__ import annotations


ENQUEUE_RUN = r"""
local existing = redis.call("GET", KEYS[1])
if existing then
    return {0, existing}
end
if redis.call("EXISTS", KEYS[2]) == 1 then
    return redis.error_reply("run id already exists")
end

local clock = redis.call("TIME")
local now = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
local due_at = tonumber(ARGV[9])
if due_at <= 0 then
    due_at = now
end

redis.call(
    "HSET",
    KEYS[2],
    "schema_version", ARGV[1],
    "run_id", ARGV[2],
    "stage", ARGV[3],
    "stage_version", ARGV[4],
    "state", "queued",
    "semantic_idempotency_key", ARGV[5],
    "execution_idempotency_key", ARGV[6],
    "config_version", ARGV[7],
    "input_json", ARGV[8],
    "due_at_ms", due_at,
    "max_attempts", ARGV[10],
    "attempt_count", 0,
    "promote", ARGV[11],
    "force", ARGV[16],
    "created_at_ms", now
)
if ARGV[12] ~= "" then
    redis.call("HSET", KEYS[2], "origin_id", ARGV[12], "origin", ARGV[13])
end
if ARGV[17] ~= "" then
    redis.call("HSET", KEYS[2], "rerun_nonce", ARGV[17])
end
redis.call("SET", KEYS[1], ARGV[2])
redis.call("ZADD", KEYS[3], due_at, ARGV[2])

if ARGV[12] ~= "" then
    redis.call("HSETNX", KEYS[6], "schema_version", ARGV[1])
    redis.call("HSETNX", KEYS[6], "origin_id", ARGV[12])
    redis.call("HSETNX", KEYS[6], "origin", ARGV[13])
    redis.call("HSETNX", KEYS[6], "max_inflight", ARGV[14])
    redis.call("HSETNX", KEYS[6], "inflight", 0)
    redis.call("HSETNX", KEYS[6], "request_interval_ms", ARGV[15])
    redis.call("HSETNX", KEYS[6], "next_allowed_ms", 0)
    redis.call("HSETNX", KEYS[6], "backoff_until_ms", 0)
    redis.call("ZADD", KEYS[5], due_at, ARGV[2])

    local next_allowed = tonumber(redis.call("HGET", KEYS[6], "next_allowed_ms") or "0")
    local backoff_until = tonumber(redis.call("HGET", KEYS[6], "backoff_until_ms") or "0")
    local origin_due = math.max(due_at, next_allowed, backoff_until)
    redis.call("ZADD", KEYS[4], origin_due, ARGV[12])
end

return {1, ARGV[2]}
"""


CLAIM_RUN = r"""
local state = redis.call("HGET", KEYS[3], "state")
if (state == "leased" or state == "running")
    and redis.call("HGET", KEYS[3], "lease_owner") == ARGV[2]
    and redis.call("HGET", KEYS[3], "lease_token") == ARGV[3] then
    return {
        2,
        redis.call("HGET", KEYS[3], "attempt_count"),
        redis.call("HGET", KEYS[3], "lease_until_ms")
    }
end

local clock = redis.call("TIME")
local now = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
local score = redis.call("ZSCORE", KEYS[1], ARGV[1])
if not score or tonumber(score) > now then
    return {0}
end
if state ~= "queued" and state ~= "retry_wait" then
    redis.call("ZREM", KEYS[1], ARGV[1])
    return {0}
end
if redis.call("EXISTS", KEYS[4]) == 1 then
    return redis.error_reply("attempt id already exists")
end

redis.call("ZREM", KEYS[1], ARGV[1])
local attempt = redis.call("HINCRBY", KEYS[3], "attempt_count", 1)
local lease_until = now + tonumber(ARGV[4])
redis.call(
    "HSET",
    KEYS[3],
    "state", "leased",
    "lease_owner", ARGV[2],
    "lease_token", ARGV[3],
    "lease_until_ms", lease_until,
    "attempt_id", ARGV[5],
    "started_at_ms", now
)
redis.call(
    "HSET",
    KEYS[4],
    "schema_version", ARGV[6],
    "attempt_id", ARGV[5],
    "run_id", ARGV[1],
    "number", attempt,
    "worker_id", ARGV[2],
    "lease_token", ARGV[3],
    "state", "leased",
    "started_at_ms", now
)
redis.call("ZADD", KEYS[2], lease_until, ARGV[1])
return {1, attempt, lease_until}
"""


CLAIM_FETCH_RUN = r"""
local function schedule_origin(not_before)
    local next_item = redis.call("ZRANGE", KEYS[2], 0, 0, "WITHSCORES")
    if #next_item == 0 then
        redis.call("ZREM", KEYS[1], ARGV[1])
        return
    end
    local next_allowed = tonumber(redis.call("HGET", KEYS[7], "next_allowed_ms") or "0")
    local backoff_until = tonumber(redis.call("HGET", KEYS[7], "backoff_until_ms") or "0")
    local next_due = tonumber(next_item[2])
    redis.call(
        "ZADD",
        KEYS[1],
        math.max(next_due, next_allowed, backoff_until, not_before),
        ARGV[1]
    )
end

local state = redis.call("HGET", KEYS[5], "state")
if (state == "leased" or state == "running")
    and redis.call("HGET", KEYS[5], "lease_owner") == ARGV[3]
    and redis.call("HGET", KEYS[5], "lease_token") == ARGV[4] then
    return {
        2,
        redis.call("HGET", KEYS[5], "attempt_count"),
        redis.call("HGET", KEYS[5], "lease_until_ms")
    }
end

local clock = redis.call("TIME")
local now = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
local origin_score = redis.call("ZSCORE", KEYS[1], ARGV[1])
local run_score = redis.call("ZSCORE", KEYS[2], ARGV[2])
if not origin_score or tonumber(origin_score) > now or not run_score or tonumber(run_score) > now then
    return {0}
end
if state ~= "queued" and state ~= "retry_wait" then
    redis.call("ZREM", KEYS[2], ARGV[2])
    redis.call("ZREM", KEYS[3], ARGV[2])
    schedule_origin(now)
    return {0}
end
if redis.call("HGET", KEYS[5], "origin_id") ~= ARGV[1] then
    return redis.error_reply("run origin does not match queue origin")
end

local inflight = tonumber(redis.call("HGET", KEYS[7], "inflight") or "0")
local max_inflight = tonumber(redis.call("HGET", KEYS[7], "max_inflight") or "1")
local next_allowed = tonumber(redis.call("HGET", KEYS[7], "next_allowed_ms") or "0")
local backoff_until = tonumber(redis.call("HGET", KEYS[7], "backoff_until_ms") or "0")
if inflight >= max_inflight then
    schedule_origin(now + tonumber(ARGV[8]))
    return {0}
end
if next_allowed > now or backoff_until > now then
    schedule_origin(math.max(next_allowed, backoff_until))
    return {0}
end
if redis.call("EXISTS", KEYS[6]) == 1 then
    return redis.error_reply("attempt id already exists")
end

redis.call("ZREM", KEYS[2], ARGV[2])
redis.call("ZREM", KEYS[3], ARGV[2])
local attempt = redis.call("HINCRBY", KEYS[5], "attempt_count", 1)
local lease_until = now + tonumber(ARGV[5])
redis.call(
    "HSET",
    KEYS[5],
    "state", "leased",
    "lease_owner", ARGV[3],
    "lease_token", ARGV[4],
    "lease_until_ms", lease_until,
    "attempt_id", ARGV[6],
    "started_at_ms", now
)
redis.call(
    "HSET",
    KEYS[6],
    "schema_version", ARGV[7],
    "attempt_id", ARGV[6],
    "run_id", ARGV[2],
    "number", attempt,
    "worker_id", ARGV[3],
    "lease_token", ARGV[4],
    "state", "leased",
    "started_at_ms", now
)
redis.call("ZADD", KEYS[4], lease_until, ARGV[2])

local request_interval = tonumber(redis.call("HGET", KEYS[7], "request_interval_ms") or "0")
redis.call(
    "HSET",
    KEYS[7],
    "inflight", inflight + 1,
    "next_allowed_ms", now + request_interval,
    "last_claimed_at_ms", now
)
schedule_origin(now + 1)
return {1, attempt, lease_until}
"""


HEARTBEAT_RUN = r"""
local state = redis.call("HGET", KEYS[1], "state")
if state ~= "leased" and state ~= "running" then
    return {0}
end
if redis.call("HGET", KEYS[1], "lease_owner") ~= ARGV[2]
    or redis.call("HGET", KEYS[1], "lease_token") ~= ARGV[3] then
    return {0}
end

local clock = redis.call("TIME")
local now = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
local lease_until = now + tonumber(ARGV[4])
redis.call("HSET", KEYS[1], "state", "running", "lease_until_ms", lease_until)
redis.call("HSET", KEYS[3], "state", "running")
redis.call("ZADD", KEYS[2], lease_until, ARGV[1])
return {1, lease_until}
"""


COMPLETE_RUN = r"""
local function release_origin(now)
    if ARGV[5] == "" then
        return
    end
    local inflight = tonumber(redis.call("HGET", KEYS[6], "inflight") or "0")
    if inflight > 0 then
        inflight = inflight - 1
    end
    redis.call("HSET", KEYS[6], "inflight", inflight, "last_released_at_ms", now)
    local next_item = redis.call("ZRANGE", KEYS[5], 0, 0, "WITHSCORES")
    if #next_item == 0 then
        redis.call("ZREM", KEYS[4], ARGV[5])
        return
    end
    local next_allowed = tonumber(redis.call("HGET", KEYS[6], "next_allowed_ms") or "0")
    local backoff_until = tonumber(redis.call("HGET", KEYS[6], "backoff_until_ms") or "0")
    redis.call(
        "ZADD",
        KEYS[4],
        math.max(tonumber(next_item[2]), next_allowed, backoff_until),
        ARGV[5]
    )
end

local state = redis.call("HGET", KEYS[2], "state")
if (state ~= "leased" and state ~= "running")
    or redis.call("HGET", KEYS[2], "lease_owner") ~= ARGV[2]
    or redis.call("HGET", KEYS[2], "lease_token") ~= ARGV[3] then
    return {0}
end

local clock = redis.call("TIME")
local now = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
redis.call("ZREM", KEYS[1], ARGV[1])
redis.call(
    "HSET",
    KEYS[2],
    "state", "succeeded",
    "finished_at_ms", now,
    "output_json", ARGV[4]
)
redis.call(
    "HDEL",
    KEYS[2],
    "lease_owner",
    "lease_token",
    "lease_until_ms",
    "error_class",
    "error_code",
    "error_message"
)
redis.call("HSET", KEYS[3], "state", "succeeded", "finished_at_ms", now)
release_origin(now)
return {1, now}
"""


FAIL_RUN = r"""
local function schedule_origin(now)
    if ARGV[10] == "" then
        return
    end
    local inflight = tonumber(redis.call("HGET", KEYS[9], "inflight") or "0")
    if inflight > 0 then
        inflight = inflight - 1
    end
    local backoff_until = tonumber(redis.call("HGET", KEYS[9], "backoff_until_ms") or "0")
    if tonumber(ARGV[11]) > 0 then
        backoff_until = math.max(backoff_until, now + tonumber(ARGV[11]))
    end
    redis.call(
        "HSET",
        KEYS[9],
        "inflight", inflight,
        "backoff_until_ms", backoff_until,
        "last_released_at_ms", now
    )
    local next_item = redis.call("ZRANGE", KEYS[8], 0, 0, "WITHSCORES")
    if #next_item == 0 then
        redis.call("ZREM", KEYS[7], ARGV[10])
        return
    end
    local next_allowed = tonumber(redis.call("HGET", KEYS[9], "next_allowed_ms") or "0")
    redis.call(
        "ZADD",
        KEYS[7],
        math.max(tonumber(next_item[2]), next_allowed, backoff_until),
        ARGV[10]
    )
end

local state = redis.call("HGET", KEYS[2], "state")
if (state ~= "leased" and state ~= "running")
    or redis.call("HGET", KEYS[2], "lease_owner") ~= ARGV[2]
    or redis.call("HGET", KEYS[2], "lease_token") ~= ARGV[3] then
    return {0}
end

local clock = redis.call("TIME")
local now = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
redis.call("ZREM", KEYS[1], ARGV[1])
redis.call(
    "HSET",
    KEYS[3],
    "state", ARGV[4],
    "finished_at_ms", now,
    "error_class", ARGV[4],
    "error_code", ARGV[5],
    "error_message", ARGV[6]
)
redis.call("HDEL", KEYS[2], "lease_owner", "lease_token", "lease_until_ms")
schedule_origin(now)

local attempt_count = tonumber(redis.call("HGET", KEYS[2], "attempt_count") or "0")
local max_attempts = tonumber(redis.call("HGET", KEYS[2], "max_attempts") or "1")
if ARGV[4] == "retryable" and attempt_count < max_attempts then
    local retry_at = now + tonumber(ARGV[7])
    redis.call(
        "HSET",
        KEYS[2],
        "state", "retry_wait",
        "due_at_ms", retry_at,
        "error_class", ARGV[4],
        "error_code", ARGV[5],
        "error_message", ARGV[6]
    )
    redis.call("ZADD", KEYS[4], retry_at, ARGV[1])
    if ARGV[10] ~= "" then
        redis.call("ZADD", KEYS[8], retry_at, ARGV[1])
        local next_allowed = tonumber(redis.call("HGET", KEYS[9], "next_allowed_ms") or "0")
        local backoff_until = tonumber(redis.call("HGET", KEYS[9], "backoff_until_ms") or "0")
        redis.call(
            "ZADD",
            KEYS[7],
            math.max(retry_at, next_allowed, backoff_until),
            ARGV[10]
        )
    end
    return {1, "retry_wait", retry_at}
end

local terminal_state = "failed"
local terminal_key = KEYS[5]
if ARGV[4] == "blocked" then
    terminal_state = "blocked"
    terminal_key = KEYS[6]
end
redis.call(
    "HSET",
    KEYS[2],
    "state", terminal_state,
    "finished_at_ms", now,
    "error_class", ARGV[4],
    "error_code", ARGV[5],
    "error_message", ARGV[6]
)
redis.call("ZADD", terminal_key, now, ARGV[1])
return {1, terminal_state, now}
"""


RECLAIM_RUN = r"""
local function schedule_origin(now)
    if ARGV[7] == "" then
        return
    end
    local inflight = tonumber(redis.call("HGET", KEYS[9], "inflight") or "0")
    if inflight > 0 then
        inflight = inflight - 1
    end
    redis.call("HSET", KEYS[9], "inflight", inflight, "last_released_at_ms", now)
end

local state = redis.call("HGET", KEYS[2], "state")
if state ~= "leased" and state ~= "running" then
    redis.call("ZREM", KEYS[1], ARGV[1])
    return {0}
end

local clock = redis.call("TIME")
local now = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
local lease_until = tonumber(redis.call("HGET", KEYS[2], "lease_until_ms") or "0")
if lease_until > now then
    return {0}
end

redis.call("ZREM", KEYS[1], ARGV[1])
redis.call(
    "HSET",
    KEYS[3],
    "state", "expired",
    "finished_at_ms", now,
    "error_class", "retryable",
    "error_code", "lease_expired",
    "error_message", "worker lease expired"
)
redis.call("HDEL", KEYS[2], "lease_owner", "lease_token", "lease_until_ms")
schedule_origin(now)

local attempt_count = tonumber(redis.call("HGET", KEYS[2], "attempt_count") or "0")
local max_attempts = tonumber(redis.call("HGET", KEYS[2], "max_attempts") or "1")
if attempt_count < max_attempts then
    local retry_at = now + tonumber(ARGV[4])
    redis.call(
        "HSET",
        KEYS[2],
        "state", "retry_wait",
        "due_at_ms", retry_at,
        "error_class", "retryable",
        "error_code", "lease_expired",
        "error_message", "worker lease expired"
    )
    redis.call("ZADD", KEYS[4], retry_at, ARGV[1])
    if ARGV[7] ~= "" then
        redis.call("ZADD", KEYS[8], retry_at, ARGV[1])
        local next_allowed = tonumber(redis.call("HGET", KEYS[9], "next_allowed_ms") or "0")
        local backoff_until = tonumber(redis.call("HGET", KEYS[9], "backoff_until_ms") or "0")
        redis.call(
            "ZADD",
            KEYS[7],
            math.max(retry_at, next_allowed, backoff_until),
            ARGV[7]
        )
    end
    return {1, "retry_wait", retry_at}
end

redis.call(
    "HSET",
    KEYS[2],
    "state", "failed",
    "finished_at_ms", now,
    "error_class", "retryable",
    "error_code", "lease_expired",
    "error_message", "worker lease expired"
)
redis.call("ZADD", KEYS[5], now, ARGV[1])
return {1, "failed", now}
"""


CONFIGURE_ORIGIN = r"""
local clock = redis.call("TIME")
local now = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
redis.call(
    "HSET",
    KEYS[3],
    "schema_version", ARGV[1],
    "origin_id", ARGV[2],
    "origin", ARGV[3],
    "max_inflight", ARGV[4],
    "request_interval_ms", ARGV[5]
)
if tonumber(ARGV[6]) >= 0 then
    redis.call("HSET", KEYS[3], "next_allowed_ms", ARGV[6])
end
if tonumber(ARGV[7]) >= 0 then
    redis.call("HSET", KEYS[3], "backoff_until_ms", ARGV[7])
end
redis.call("HSETNX", KEYS[3], "inflight", 0)
redis.call("HSETNX", KEYS[3], "next_allowed_ms", 0)
redis.call("HSETNX", KEYS[3], "backoff_until_ms", 0)

local next_item = redis.call("ZRANGE", KEYS[2], 0, 0, "WITHSCORES")
if #next_item == 0 then
    redis.call("ZREM", KEYS[1], ARGV[2])
    return {1, 0}
end
local next_allowed = tonumber(redis.call("HGET", KEYS[3], "next_allowed_ms") or "0")
local backoff_until = tonumber(redis.call("HGET", KEYS[3], "backoff_until_ms") or "0")
local eligible_at = math.max(tonumber(next_item[2]), next_allowed, backoff_until, now)
redis.call("ZADD", KEYS[1], eligible_at, ARGV[2])
return {1, eligible_at}
"""
