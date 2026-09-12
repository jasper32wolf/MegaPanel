from app.services.competitor import build_skeleton, extract_html, parse_sitemap_xml
from site_panel_security import SSRFBlockedError, SSRFGuard
import pytest


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


def test_parse_sitemap():
    xml = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/a</loc></url>
      <url><loc>https://example.com/b</loc></url>
    </urlset>
    """
    urls = parse_sitemap_xml(xml, "https://example.com")
    assert len(urls) == 2


def test_skeleton_merge():
    pages = [
        {"title": "A", "h1": ["H1"], "faq_candidates": ["Цена?"]},
        {"title": "B", "h1": ["H2"], "faq_candidates": []},
    ]
    sk = build_skeleton(pages)
    assert sk["coverage"] == "intent_full"
    assert "silo" in sk
    assert len(sk["sample_titles"]) == 2


def test_ssrf_blocks_metadata_ip():
    guard = SSRFGuard()
    with pytest.raises(SSRFBlockedError):
        guard.resolve_safe("169.254.169.254")
