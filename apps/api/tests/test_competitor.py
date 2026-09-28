from __future__ import annotations

import asyncio

import pytest
from app.schemas.phase3 import ScanCreate
from app.services import competitor
from app.services.competitor import build_skeleton, extract_html, scan_competitors
from pydantic import ValidationError
from site_panel_security import SSRFBlockedError, SSRFGuard


def test_extract_html_basic():
    html = """
    <html><head><title>Ремонт в Москве</title>
    <meta name="description" content="Услуги ремонта">
    </head><body><h1>Ремонт стиральных машин</h1>
    <h2>Сколько стоит ремонт?</h2>
    </body></html>
    """
    data = extract_html(html)
    assert "Ремонт" in data["title"]
    assert data["meta_description"]
    assert data["h1"]
    assert data["faq_candidates"]


def test_scan_schema_requires_unique_manual_urls_and_acknowledgement():
    with pytest.raises(ValidationError, match="unique"):
        ScanCreate(
            urls=["https://example.test/a", "https://example.test/a"],
            terms_acknowledged=True,
        )
    assert ScanCreate(urls=["https://example.test/a"], terms_acknowledged=True).urls


def test_manual_scan_fetches_only_operator_supplied_urls(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    class Response:
        status_code = 200
        text = "<title>Конкурент</title><h1>Услуга</h1><h2>Цена?</h2>"

    class Guard:
        def __init__(self, **_kwargs):
            pass

        async def fetch(self, url: str) -> Response:
            calls.append(url)
            return Response()

    monkeypatch.setattr(competitor, "SSRFGuard", Guard)

    result = asyncio.run(
        scan_competitors(["https://one.example.test/page", "https://two.example.test/page"])
    )

    assert calls == ["https://one.example.test/page", "https://two.example.test/page"]
    assert result["urls"] == calls
    assert result["skeleton"]["coverage"] == "manual_urls_only"
    assert all("sitemap" not in url for url in calls)


def test_skeleton_merge():
    pages = [
        {"title": "A", "h1": ["H1"], "faq_candidates": ["Цена?"]},
        {"title": "B", "h1": ["H2"], "faq_candidates": []},
    ]
    skeleton = build_skeleton(pages)
    assert skeleton["coverage"] == "manual_urls_only"
    assert "silo" in skeleton
    assert len(skeleton["sample_titles"]) == 2


def test_ssrf_blocks_metadata_ip():
    guard = SSRFGuard()
    with pytest.raises(SSRFBlockedError):
        guard.resolve_safe("169.254.169.254")
