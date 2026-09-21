"""IndexNow + sitemap ping helpers (TZ 7.4)."""

from __future__ import annotations

import secrets
from typing import Any

import httpx

INDEXNOW_ENDPOINTS = [
    "https://api.indexnow.org/indexnow",
    "https://yandex.com/indexnow",
    "https://www.bing.com/indexnow",
]


def new_indexnow_key() -> str:
    return secrets.token_hex(16)


async def submit_indexnow(
    *,
    host: str,
    key: str,
    key_location: str,
    urls: list[str],
) -> dict[str, Any]:
    if not urls:
        return {"submitted": 0, "results": []}
    payload = {
        "host": host,
        "key": key,
        "keyLocation": key_location,
        "urlList": urls[:10000],
    }
    results = []
    async with httpx.AsyncClient(timeout=20.0) as client:
        for endpoint in INDEXNOW_ENDPOINTS:
            try:
                resp = await client.post(endpoint, json=payload)
                results.append({"endpoint": endpoint, "status": resp.status_code})
            except Exception as exc:  # noqa: BLE001
                results.append({"endpoint": endpoint, "error": str(exc)})
    return {"submitted": len(urls), "results": results}
