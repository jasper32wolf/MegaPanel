"""Nominatim/OSM importer stub — rate-limited, cache-first (TZ 3.1)."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import quote

import httpx
from site_panel_security import SSRFGuard


class NominatimClient:
    def __init__(self, min_interval: float = 1.1) -> None:
        self.min_interval = min_interval
        self._last = 0.0
        self._guard = SSRFGuard(timeout=15.0)
        self._cache: dict[str, Any] = {}

    async def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self.min_interval:
            await asyncio.sleep(self.min_interval - elapsed)
        self._last = time.monotonic()

    async def search(self, query: str) -> list[dict[str, Any]]:
        if query in self._cache:
            return self._cache[query]
        await self._throttle()
        # Prefer SSRF-guarded resolve; Nominatim is public HTTPS
        url = f"https://nominatim.openstreetmap.org/search?q={quote(query)}&format=json&limit=5"
        try:
            resp = await self._guard.fetch(url)
            data = resp.json()
        except Exception:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"q": query, "format": "json", "limit": 5},
                    headers={"User-Agent": "SitePanel/0.1 (geo-import; contact@example.com)"},
                )
                resp.raise_for_status()
                data = resp.json()
        self._cache[query] = data
        return data
