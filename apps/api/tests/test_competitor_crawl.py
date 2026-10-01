from __future__ import annotations

import pytest
from app.main import app
from app.schemas.research import CompetitorCrawlCreate
from app.services.competitor_crawl import (
    CrawlScope,
    _is_allowed_by_robots,
    _robots_directives,
    _sitemap_urls,
    crawler_guard,
    extract_page_signals,
    normalize_root_url,
    normalize_same_origin_url,
)
from pydantic import ValidationError
from site_panel_security import SSRFBlockedError


def test_root_domain_crawl_requires_https_root_without_credentials_or_query():
    scope = normalize_root_url("https://Example.Test/services/")

    assert scope == CrawlScope(
        root_url="https://example.test/services/",
        origin="https://example.test",
        host="example.test",
    )
    with pytest.raises(ValueError, match="HTTPS"):
        normalize_root_url("http://example.test")
    with pytest.raises(ValueError, match="credentials"):
        normalize_root_url("https://user:pass@example.test")
    with pytest.raises(ValueError, match="query"):
        normalize_root_url("https://example.test/?page=2")


def test_crawl_schema_rejects_insecure_and_non_root_inputs():
    assert (
        CompetitorCrawlCreate(root_url="https://example.test", terms_acknowledged=True).max_pages
        == 100
    )
    with pytest.raises(ValidationError, match="HTTPS"):
        CompetitorCrawlCreate(root_url="http://example.test", terms_acknowledged=True)
    with pytest.raises(ValidationError, match="Confirm responsibility"):
        CompetitorCrawlCreate(root_url="https://example.test", terms_acknowledged=False)


def test_crawler_only_accepts_same_origin_https_paths_without_query_or_fragment():
    scope = normalize_root_url("https://example.test")

    assert (
        normalize_same_origin_url("https://example.test//repair#top", scope)
        == "https://example.test/repair"
    )
    assert normalize_same_origin_url("https://sub.example.test/repair", scope) is None
    assert normalize_same_origin_url("https://example.test/repair?utm=ad", scope) is None
    assert normalize_same_origin_url("http://example.test/repair", scope) is None
    assert normalize_same_origin_url("https://example.test:8443/repair", scope) is None


def test_robots_and_sitemap_are_bounded_to_same_origin():
    scope = normalize_root_url("https://example.test")
    disallowed, sitemaps = _robots_directives(
        """
        User-agent: *
        Disallow: /private/
        Sitemap: https://example.test/sitemap-main.xml
        Sitemap: https://outside.test/sitemap.xml
        """,
        scope,
    )

    assert disallowed == ["/private/"]
    assert sitemaps == ["https://example.test/sitemap-main.xml"]
    assert _is_allowed_by_robots("https://example.test/open/", disallowed)
    assert not _is_allowed_by_robots("https://example.test/private/page", disallowed)
    urls = _sitemap_urls(
        b"""<?xml version='1.0'?><urlset><url><loc>https://example.test/a</loc></url>
        <url><loc>https://outside.test/b</loc></url></urlset>""",
        scope,
    )
    assert urls == ["https://example.test/a"]


def test_page_signal_extraction_keeps_bounded_structural_faq_and_price_signals():
    scope = normalize_root_url("https://example.test")
    html = """
    <html><head>
      <title>Ремонт телевизоров</title>
      <meta name="description" content="Срочный ремонт">
      <link rel="canonical" href="/repair/">
      <script type="application/ld+json">{
        "@context":"https://schema.org", "@type":"FAQPage", "mainEntity":[{
          "@type":"Question", "name":"Сколько стоит ремонт?",
          "acceptedAnswer":{"@type":"Answer", "text":"От 1200 рублей"}
        }]
      }</script>
    </head><body>
      <h1>Ремонт телевизоров</h1><h2>Цены</h2><p>от 1 200 ₽</p>
      <a href="/prices/">Прайс</a><a href="https://outside.test/">Внешняя</a>
    </body></html>
    """

    signals, links = extract_page_signals(html, "https://example.test/", scope)

    assert signals["title"] == "Ремонт телевизоров"
    assert signals["meta_description"] == "Срочный ремонт"
    assert signals["headings"] == [
        {"level": "h1", "text": "Ремонт телевизоров"},
        {"level": "h2", "text": "Цены"},
    ]
    assert signals["faq"] == [
        {"question": "Сколько стоит ремонт?", "answer": "От 1200 рублей", "source": "json_ld"}
    ]
    assert signals["price_present"] is True
    assert {item["source"] for item in signals["prices"]} == {"text_pattern"}
    assert links == ["https://example.test/prices/"]


def test_domain_crawl_api_exposes_only_authenticated_project_routes():
    paths = app.openapi()["paths"]

    root = "/api/v1/competitors/projects/{project_id}/domain-crawls"
    assert {"get", "post"}.issubset(paths[root])
    assert "get" in paths[f"{root}/{{crawl_id}}"]
    assert "get" in paths[f"{root}/{{crawl_id}}/pages"]
    assert "post" in paths[f"{root}/{{crawl_id}}/cancel"]


def test_crawler_guard_uses_https_public_ips_and_standard_port_only():
    guard = crawler_guard()

    assert guard.require_global_ips is True
    assert guard.allowed_schemes == ("https",)
    assert guard.allowed_ports == (443,)
    with pytest.raises(SSRFBlockedError, match="scheme"):
        guard.validate_url("http://example.test")
    with pytest.raises(SSRFBlockedError, match="port"):
        guard.validate_url("https://example.test:8443")
