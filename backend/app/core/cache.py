"""Redis client.

Used for the resolved access context, business-rule resolution results and
rate-limit counters. Every caller must treat a cache failure as a cache miss:
the system is allowed to get slower when Redis is down, never more permissive.
"""

from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("cache")

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """The shared connection pool. Created on first use."""
    global _client
    if _client is None:
        _client = aioredis.from_url(
            str(settings.redis_url),
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def ping() -> bool:
    # Blind catch is deliberate: this is a probe, and any failure at all means
    # "not reachable".
    try:
        return bool(await get_redis().ping())
    except Exception:  # noqa: BLE001
        return False


async def safe_get(key: str) -> str | None:
    """Read, returning None on any failure."""
    try:
        value: Any = await get_redis().get(key)
        return None if value is None else str(value)
    except Exception:
        log.warning("cache.get_failed", key=key, exc_info=True)
        return None


async def safe_set(key: str, value: str, ttl_seconds: int) -> None:
    try:
        await get_redis().set(key, value, ex=ttl_seconds)
    except Exception:
        log.warning("cache.set_failed", key=key, exc_info=True)


async def safe_delete(*keys: str) -> None:
    if not keys:
        return
    try:
        await get_redis().delete(*keys)
    except Exception:
        log.warning("cache.delete_failed", exc_info=True)
