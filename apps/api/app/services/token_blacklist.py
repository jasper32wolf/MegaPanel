from __future__ import annotations

from app.core.config import get_settings

settings = get_settings()
_memory_blacklist: set[str] = set()
_redis = None


async def _get_redis():
    global _redis
    if _redis is not None:
        return _redis
    try:
        from redis.asyncio import Redis

        _redis = Redis.from_url(settings.redis_url, decode_responses=True)
        await _redis.ping()
        return _redis
    except Exception:  # noqa: BLE001
        return None


async def blacklist_jti(jti: str, ttl_seconds: int) -> None:
    client = await _get_redis()
    if client:
        await client.setex(f"jwt:bl:{jti}", ttl_seconds, "1")
    else:
        _memory_blacklist.add(jti)


async def is_blacklisted(jti: str) -> bool:
    client = await _get_redis()
    if client:
        return bool(await client.exists(f"jwt:bl:{jti}"))
    return jti in _memory_blacklist
