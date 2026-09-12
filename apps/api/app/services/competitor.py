from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

from site_panel_security import SSRFBlockedError, SSRFGuard


class _MetaExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.meta_description = ""
        self.headings: dict[str, list[str]] = {f"h{i}": [] for i in range(1, 7)}
        self._capture: str | None = None
        self._buf: list[str] = []
        self.faqs: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = {k: v or "" for k, v in attrs}
        if tag == "meta" and ad.get("name", "").lower() == "description":
            self.meta_description = ad.get("content", "")
        if tag in self.headings:
            self._capture = tag
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == self._capture:
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if text:
                self.headings[tag].append(text)
            self._capture = None

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buf.append(data)
        if self._capture is None and "title" == getattr(self, "_in_title", None):
            self.title += data

    def handle_starttag_title(self, tag: str) -> None:
        pass

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


class _TitleAwareExtractor(_MetaExtractor):
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        super().handle_starttag(tag, attrs)
        if tag == "title":
            self._capture_title = True
            self._title_buf: list[str] = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and getattr(self, "_capture_title", False):
            self.title = re.sub(r"\s+", " ", "".join(self._title_buf)).strip()
            self._capture_title = False
        super().handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if getattr(self, "_capture_title", False):
            self._title_buf.append(data)
        super().handle_data(data)


def extract_html(html: str) -> dict[str, Any]:
    parser = _TitleAwareExtractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001
        pass
    return {
        "title": parser.title,
        "meta_description": parser.meta_description,
        "headings": parser.headings,
        "h1": parser.headings.get("h1", []),
        "faq_candidates": [
            h for h in parser.headings.get("h2", []) + parser.headings.get("h3", []) if "?" in h
        ],
    }


def parse_sitemap_xml(xml_text: str, base_url: str, limit: int = 200) -> list[str]:
    urls: list[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return urls
    # Handle namespaces
    for loc in root.iter():
        if loc.tag.endswith("loc") and loc.text:
            urls.append(urljoin(base_url, loc.text.strip()))
            if len(urls) >= limit:
                break
    return urls


def build_skeleton(pages: list[dict[str, Any]]) -> dict[str, Any]:
    """Master Skeleton Builder — merge competitor structures into silo tree."""
    titles = [p.get("title") for p in pages if p.get("title")]
    h1s = [h for p in pages for h in p.get("h1", [])]
    faqs = [q for p in pages for q in p.get("faq_candidates", [])]
    sections = []
    for label in ("hero", "services", "pricing", "faq", "contacts"):
        sections.append({"type": label, "required": True})
    return {
        "silo": {
            "home": {"intent": "brand+geo", "blocks": ["hero", "services", "lead_form"]},
            "service": {"intent": "commercial", "blocks": ["hero", "pricing", "faq", "lead_form"]},
            "geo": {"intent": "local", "blocks": ["hero", "services", "contacts"]},
        },
        "sections": sections,
        "sample_titles": titles[:20],
        "sample_h1": h1s[:20],
        "sample_faq": faqs[:20],
        "coverage": "intent_full",
    }


async def scan_competitor(seed_url: str, *, max_pages: int = 5) -> dict[str, Any]:
    guard = SSRFGuard(timeout=12.0, max_response_bytes=2_000_000)
    parsed = urlparse(seed_url)
    if parsed.scheme not in {"http", "https"}:
        raise SSRFBlockedError("Invalid scheme")

    urls: list[str] = [seed_url]
    sitemap_url = urljoin(seed_url, "/sitemap.xml")
    try:
        sm = await guard.fetch(sitemap_url)
        if sm.status_code == 200:
            urls = parse_sitemap_xml(sm.text, seed_url) or urls
    except SSRFBlockedError:
        raise
    except Exception:  # noqa: BLE001
        pass

    urls = urls[:max_pages]
    extracted_pages: list[dict[str, Any]] = []
    for u in urls:
        try:
            resp = await guard.fetch(u)
            if resp.status_code >= 400:
                continue
            data = extract_html(resp.text)
            data["url"] = u
            extracted_pages.append(data)
        except SSRFBlockedError:
            raise
        except Exception:  # noqa: BLE001
            continue

    skeleton = build_skeleton(extracted_pages)
    return {
        "urls": urls,
        "extracted": {"pages": extracted_pages},
        "skeleton": skeleton,
    }
