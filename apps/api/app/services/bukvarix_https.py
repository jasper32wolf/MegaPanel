"""Fixed HTTPS-only public Bukvarix free-mode client."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any

import httpx

from app.services.hardening import EgressGuard

BUKVARIX_KEYWORDS_URL = "https://api.bukvarix.com/v1/keywords/"
PUBLIC_FREE_KEY = "free"
MAX_SEED_QUERIES = 10
MAX_ROWS_PER_QUERY = 100
MAX_RESULTS_PER_RUN = 1_000
MAX_RESPONSE_BYTES = 512 * 1024
MAX_PHRASE_LENGTH = 512

_WS = re.compile(r"\s+")


class BukvarixHTTPSFailure(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def normalize_phrase(value: str) -> str:
    return _WS.sub(" ", value.strip().lower())


def output_hash(rows: Iterable[dict]) -> str:
    return hashlib.sha256(
        json.dumps(list(rows), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def parse_keyword_payload(payload: object) -> list[tuple[str, list[int | float]]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise BukvarixHTTPSFailure("malformed_response")
    rows: list[tuple[str, list[int | float]]] = []
    for item in payload["data"][:MAX_ROWS_PER_QUERY]:
        if not isinstance(item, list) or not item or not isinstance(item[0], str):
            raise BukvarixHTTPSFailure("malformed_response")
        phrase = item[0].strip()
        if not phrase or len(phrase) > MAX_PHRASE_LENGTH:
            raise BukvarixHTTPSFailure("malformed_response")
        metrics = item[1:]
        if len(metrics) > 8 or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) for value in metrics
        ):
            raise BukvarixHTTPSFailure("malformed_response")
        rows.append((phrase, metrics))
    return rows


async def fetch_public_free_keywords(
    query: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[tuple[str, list[int | float]]]:
    """Fetch one bounded public-free query; never accepts an external endpoint or credential."""
    if not query.strip() or len(query) > MAX_PHRASE_LENGTH:
        raise BukvarixHTTPSFailure("invalid_seed")
    guard = EgressGuard()
    guard.assert_allowed(BUKVARIX_KEYWORDS_URL)
    await guard.assert_public_dns(BUKVARIX_KEYWORDS_URL)
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": "SitePanel/0.1 HTTPS public-free"},
            transport=transport,
        ) as client:
            async with client.stream(
                "GET",
                BUKVARIX_KEYWORDS_URL,
                params={"q": query.strip(), "format": "json", "api_key": PUBLIC_FREE_KEY},
            ) as response:
                if response.status_code == 429:
                    raise BukvarixHTTPSFailure("rate_limited")
                if response.status_code >= 500:
                    raise BukvarixHTTPSFailure("provider_unavailable")
                if response.status_code != 200:
                    raise BukvarixHTTPSFailure("provider_rejected")
                chunks: list[bytes] = []
                received = 0
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > MAX_RESPONSE_BYTES:
                        raise BukvarixHTTPSFailure("response_too_large")
                    chunks.append(chunk)
                raw = b"".join(chunks)
    except BukvarixHTTPSFailure:
        raise
    except httpx.TimeoutException as exc:
        raise BukvarixHTTPSFailure("transport_timeout") from exc
    except httpx.HTTPError as exc:
        raise BukvarixHTTPSFailure("transport_failed") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise BukvarixHTTPSFailure("response_too_large")
    try:
        payload: Any = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise BukvarixHTTPSFailure("malformed_response") from exc
    return parse_keyword_payload(payload)


__all__ = [
    "BUKVARIX_KEYWORDS_URL",
    "BukvarixHTTPSFailure",
    "MAX_RESULTS_PER_RUN",
    "MAX_SEED_QUERIES",
    "fetch_public_free_keywords",
    "normalize_phrase",
    "output_hash",
]
