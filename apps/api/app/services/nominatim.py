"""Nominatim/OSM importer stub — rate-limited, cache-first (TZ 3.1)."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import quote

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
        url = f"https://nominatim.openstreetmap.org/search?q={quote(query)}&format=json&limit=5"
        resp = await self._guard.fetch(url)
        resp.raise_for_status()
        data = resp.json()
        self._cache[query] = data
        return data
