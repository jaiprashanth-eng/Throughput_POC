import os
import time
from datetime import datetime, timezone as dt_timezone
from typing import Optional

import redis
import redis.asyncio as aioredis

_redis_pool: "redis.ConnectionPool | None" = None


def _get_pool() -> "redis.ConnectionPool":
    global _redis_pool
    if _redis_pool is None:
        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _redis_pool = redis.ConnectionPool.from_url(url, decode_responses=True, max_connections=50)
    return _redis_pool

_JOB_KEY_PREFIX = "job:"

_MARK_ITEM_DONE_SCRIPT = """
local key = KEYS[1]
local success = ARGV[1] == '1'
local now_ms = tonumber(ARGV[2])

if success then
    redis.call('HINCRBY', key, 'completed', 1)
else
    redis.call('HINCRBY', key, 'failed_count', 1)
end

local total = tonumber(redis.call('HGET', key, 'total') or '0')
local completed = tonumber(redis.call('HGET', key, 'completed') or '0')
local failed_count = tonumber(redis.call('HGET', key, 'failed_count') or '0')

if (completed + failed_count) >= total and total > 0 then
    local enqueued_at_ms = tonumber(redis.call('HGET', key, 'enqueued_at_ms') or '0')
    redis.call('HSET', key,
        'status', 'done',
        'finished_at_ms', now_ms,
        'wall_time_ms', tostring(now_ms - enqueued_at_ms)
    )
end

return redis.call('HGETALL', key)
"""


def _get_client() -> redis.Redis:
    return redis.Redis(connection_pool=_get_pool())


def _job_key(job_id: str) -> str:
    return f"{_JOB_KEY_PREFIX}{job_id}"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _hash_to_dict(data: dict) -> dict:
    return {
        "job_id": data.get("job_id"),
        "system": data.get("system"),
        "total": int(data.get("total", 0)),
        "completed": int(data.get("completed", 0)),
        "failed_count": int(data.get("failed_count", 0)),
        "status": data.get("status", "pending"),
        "enqueued_at": _ms_to_iso(data.get("enqueued_at_ms")),
        "started_at": _ms_to_iso(data.get("started_at_ms")),
        "finished_at": _ms_to_iso(data.get("finished_at_ms")),
        "wall_time_ms": float(data["wall_time_ms"]) if data.get("wall_time_ms") else None,
    }


def _ms_to_iso(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    dt = datetime.fromtimestamp(int(value) / 1000.0, tz=dt_timezone.utc)
    return dt.isoformat()


def create_job(job_id: str, system: str, total: int) -> None:
    client = _get_client()
    now_ms = _now_ms()
    client.hset(
        _job_key(job_id),
        mapping={
            "job_id": job_id,
            "system": system,
            "total": total,
            "completed": 0,
            "failed_count": 0,
            "status": "pending",
            "enqueued_at_ms": now_ms,
        },
    )


def mark_item_done(job_id: str, success: bool) -> None:
    client = _get_client()
    client.eval(
        _MARK_ITEM_DONE_SCRIPT,
        1,
        _job_key(job_id),
        "1" if success else "0",
        _now_ms(),
    )


def get_job_status(job_id: str) -> dict:
    client = _get_client()
    data = client.hgetall(_job_key(job_id))
    if not data:
        raise KeyError(f"Job {job_id} not found")
    return _hash_to_dict(data)


# ── async Redis client ────────────────────────────────────────────────────────
# Mirrors the sync pool pattern above; uses redis.asyncio (bundled with redis-py).

_async_redis_pool: "aioredis.ConnectionPool | None" = None


def _get_async_pool() -> "aioredis.ConnectionPool":
    global _async_redis_pool
    if _async_redis_pool is None:
        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _async_redis_pool = aioredis.ConnectionPool.from_url(url, decode_responses=True, max_connections=50)
    return _async_redis_pool


def _get_async_client() -> "aioredis.Redis":
    return aioredis.Redis(connection_pool=_get_async_pool())


async def create_job_async(job_id: str, system: str, total: int) -> None:
    client = _get_async_client()
    now_ms = _now_ms()
    await client.hset(
        _job_key(job_id),
        mapping={
            "job_id": job_id,
            "system": system,
            "total": total,
            "completed": 0,
            "failed_count": 0,
            "status": "pending",
            "enqueued_at_ms": now_ms,
        },
    )


async def mark_item_done_async(job_id: str, success: bool) -> None:
    client = _get_async_client()
    await client.eval(
        _MARK_ITEM_DONE_SCRIPT,
        1,
        _job_key(job_id),
        "1" if success else "0",
        _now_ms(),
    )


async def get_job_status_async(job_id: str) -> dict:
    client = _get_async_client()
    data = await client.hgetall(_job_key(job_id))
    if not data:
        raise KeyError(f"Job {job_id} not found")
    return _hash_to_dict(data)
