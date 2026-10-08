from __future__ import annotations

import hashlib
import ipaddress
import time
from collections import defaultdict, deque

from app.core.config import get_settings
from fastapi import HTTPException, Request
from redis.asyncio import from_url


class SlidingWindowLimiter:
    """In-process rate limiter; Redis-backed limiter lands with production deploy."""

    def __init__(self, max_hits: int, window_seconds: float) -> None:
        self.max_hits = max_hits
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.max_hits:
            raise HTTPException(status_code=429, detail="Too many requests")
        q.append(now)


class RedisWindowLimiter:
    def __init__(self, max_hits: int, window_seconds: int) -> None:
        self.max_hits = max_hits
        self.window_seconds = window_seconds

    async def check(self, key: str) -> None:
        redis = from_url(get_settings().redis_url)
        try:
            hits = await redis.eval(
                "local count = redis.call('INCR', KEYS[1]) "
                "if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end "
                "return count",
                1,
                key,
                self.window_seconds,
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail="Rate limit unavailable") from exc
        finally:
            await redis.aclose()
        if int(hits) > self.max_hits:
            raise HTTPException(status_code=429, detail="Too many requests")


auth_limiter = SlidingWindowLimiter(max_hits=10, window_seconds=60)
lead_limiter = SlidingWindowLimiter(max_hits=30, window_seconds=60)
shared_lead_limiter = RedisWindowLimiter(max_hits=30, window_seconds=60)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.rsplit(",", maxsplit=1)[-1].strip()
    if request.client:
        return request.client.host
    return "unknown"


def session_client_ip(request: Request) -> str | None:
    peer = _parse_ip(request.client.host) if request.client else None
    trusted_networks = _trusted_proxy_networks()
    if peer and any(peer in network for network in trusted_networks):
        forwarded = request.headers.get("X-Forwarded-For", "")
        candidate = _parse_ip(forwarded.rsplit(",", maxsplit=1)[-1].strip())
        if candidate and candidate.is_global:
            return str(candidate)
    if peer and peer.is_global:
        return str(peer)
    return None


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _trusted_proxy_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    networks = []
    for value in get_settings().trusted_proxy_cidrs.split(","):
        try:
            if value.strip():
                networks.append(ipaddress.ip_network(value.strip(), strict=False))
        except ValueError:
            continue
    return tuple(networks)


def rate_limit_key(prefix: str, value: str) -> str:
    return f"{prefix}:{hashlib.sha256(value.encode()).hexdigest()}"
