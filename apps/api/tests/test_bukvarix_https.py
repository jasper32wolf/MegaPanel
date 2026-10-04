from __future__ import annotations

import inspect
from pathlib import Path

import httpx
import pytest
from app.services import bukvarix_https
from app.services.bukvarix_https import (
    BUKVARIX_KEYWORDS_URL,
    MAX_RESPONSE_BYTES,
    PUBLIC_FREE_KEY,
    BukvarixHTTPSFailure,
    fetch_public_free_keywords,
    parse_keyword_payload,
)


def test_fixed_public_free_provider_boundary_has_no_configurable_endpoint_or_secret():
    assert BUKVARIX_KEYWORDS_URL == "https://api.bukvarix.com/v1/keywords/"
    assert PUBLIC_FREE_KEY == "free"
    source = inspect.getsource(fetch_public_free_keywords)
    assert "follow_redirects=False" in source
    assert '"api_key": PUBLIC_FREE_KEY' in source
    assert "endpoint" not in inspect.signature(fetch_public_free_keywords).parameters
    assert "api_key" not in inspect.signature(fetch_public_free_keywords).parameters


def test_keyword_payload_is_strictly_bounded_and_does_not_treat_booleans_as_metrics():
    assert parse_keyword_payload({"data": [["ремонт окон", 10, 1.5]]}) == [
        ("ремонт окон", [10, 1.5])
    ]
    for payload in (
        {},
        {"data": "not-a-list"},
        {"data": [["", 1]]},
        {"data": [["ремонт", True]]},
        {"data": [["ремонт", "100"]]},
        {"data": [["ремонт", *range(9)]]},
    ):
        with pytest.raises(BukvarixHTTPSFailure, match="malformed_response"):
            parse_keyword_payload(payload)


@pytest.mark.asyncio
async def test_fetch_uses_only_fixed_https_params_and_keeps_response_bounded(monkeypatch):
    monkeypatch.setattr(
        bukvarix_https.EgressGuard, "assert_allowed", lambda self, url: url == BUKVARIX_KEYWORDS_URL
    )

    async def public_dns(self, url: str) -> None:
        assert url == BUKVARIX_KEYWORDS_URL

    monkeypatch.setattr(bukvarix_https.EgressGuard, "assert_public_dns", public_dns)
    seen: dict[str, object] = {}

    def responder(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url.copy_with(query=None))
        seen["params"] = dict(request.url.params)
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, json={"data": [["ремонт окон", 42, 3]]})

    result = await fetch_public_free_keywords(
        " ремонт окон ", transport=httpx.MockTransport(responder)
    )

    assert result == [("ремонт окон", [42, 3])]
    assert seen["url"] == BUKVARIX_KEYWORDS_URL
    assert seen["params"] == {"q": "ремонт окон", "format": "json", "api_key": "free"}
    assert seen["cookie"] is None


@pytest.mark.asyncio
async def test_fetch_rejects_oversized_response_without_persisting_provider_body(monkeypatch):
    monkeypatch.setattr(bukvarix_https.EgressGuard, "assert_allowed", lambda self, url: None)

    async def public_dns(self, url: str) -> None:
        return None

    monkeypatch.setattr(bukvarix_https.EgressGuard, "assert_public_dns", public_dns)

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))

    with pytest.raises(BukvarixHTTPSFailure, match="response_too_large"):
        await fetch_public_free_keywords("ремонт окон", transport=httpx.MockTransport(responder))


def test_queue_and_worker_receive_only_durable_run_ids():
    queue_source = Path(__file__).parents[1] / "app" / "services" / "bukvarix_queue.py"
    worker_source = Path(__file__).parents[1] / "app" / "worker.py"
    queue = queue_source.read_text(encoding="utf-8")
    worker = worker_source.read_text(encoding="utf-8")

    assert 'enqueue_job("bukvarix_keyword_task", str(run_id))' in queue
    assert "fetch_public_free_keywords(seed[\"phrase\"])" in queue
    assert "bukvarix_keyword_sweep_task" in worker
    assert "bukvarix_keyword_task" in worker
    assert "candidate_build_task" not in queue
    assert "indexnow" not in queue.lower()
