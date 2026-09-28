from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

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
        attributes = {key: value or "" for key, value in attrs}
        if tag == "meta" and attributes.get("name", "").lower() == "description":
            self.meta_description = attributes.get("content", "")
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
            heading
            for heading in parser.headings.get("h2", []) + parser.headings.get("h3", [])
            if "?" in heading
        ],
    }


def build_skeleton(pages: list[dict[str, Any]]) -> dict[str, Any]:
    titles = [page.get("title") for page in pages if page.get("title")]
    h1s = [heading for page in pages for heading in page.get("h1", [])]
    faqs = [question for page in pages for question in page.get("faq_candidates", [])]
    return {
        "silo": {
            "home": {"intent": "brand+geo", "blocks": ["hero", "services", "lead_form"]},
            "service": {"intent": "commercial", "blocks": ["hero", "pricing", "faq", "lead_form"]},
            "geo": {"intent": "local", "blocks": ["hero", "services", "contacts"]},
        },
        "sections": [
            {"type": label, "required": True}
            for label in ("hero", "services", "pricing", "faq", "contacts")
        ],
        "sample_titles": titles[:20],
        "sample_h1": h1s[:20],
        "sample_faq": faqs[:20],
        "coverage": "manual_urls_only",
    }


def _bounded_text(value: object, *, limit: int = 180) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"\s+", " ", value).strip()
    if not normalized:
        return None
    return normalized[:limit]


def _bounded_items(value: object, *, maximum: int = 10) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        normalized = _bounded_text(item)
        if normalized and normalized not in result:
            result.append(normalized)
        if len(result) == maximum:
            break
    return result


_STRUCTURAL_SECTION_TYPES = {"hero", "services", "pricing", "faq", "contacts"}


def approved_evidence_content(scan_id: str, urls: object, skeleton: object) -> dict[str, Any]:
    source_urls = [item for item in urls if isinstance(item, str)] if isinstance(urls, list) else []
    source = skeleton if isinstance(skeleton, dict) else {}
    sections = source.get("sections", [])
    structural_sections = []
    if isinstance(sections, list):
        for item in sections:
            label = item.get("type") if isinstance(item, dict) else None
            if isinstance(label, str) and label in _STRUCTURAL_SECTION_TYPES:
                structural_sections.append(label)
    return {
        "reference_class": "competitor_evidence",
        "scope": "reference_only",
        "source_scan_id": scan_id,
        "coverage": "manual_urls_only",
        "source_url_count": len(source_urls),
        "signals": {
            "sample_titles": _bounded_items(source.get("sample_titles")),
            "sample_h1": _bounded_items(source.get("sample_h1")),
            "sample_faq": _bounded_items(source.get("sample_faq")),
            "structural_sections": list(dict.fromkeys(structural_sections))[:8],
        },
    }


def evidence_provider_rows(content: object) -> dict[str, Any]:
    source = content if isinstance(content, dict) else {}
    signals = source.get("signals") if isinstance(source.get("signals"), dict) else {}
    structural_sections = signals.get("structural_sections")
    source_url_count = source.get("source_url_count")
    return {
        "reference_class": "competitor_evidence",
        "scope": "reference_only",
        "source_scan_id": _bounded_text(source.get("source_scan_id"), limit=36) or "",
        "coverage": "manual_urls_only",
        "source_url_count": source_url_count if isinstance(source_url_count, int) else 0,
        "signals": {
            "sample_titles": _bounded_items(signals.get("sample_titles")),
            "sample_h1": _bounded_items(signals.get("sample_h1")),
            "sample_faq": _bounded_items(signals.get("sample_faq")),
            "structural_sections": [
                item
                for item in structural_sections
                if isinstance(item, str) and item in _STRUCTURAL_SECTION_TYPES
            ][:8]
            if isinstance(structural_sections, list)
            else [],
        },
    }


def _validate_manual_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SSRFBlockedError("Invalid public URL")
    return value


async def scan_competitors(urls: list[str]) -> dict[str, Any]:
    """Fetch only operator-supplied public URLs; never discover or crawl pages."""
    guard = SSRFGuard(timeout=12.0, max_response_bytes=2_000_000)
    manual_urls = [_validate_manual_url(url) for url in urls]
    if len(manual_urls) != len(set(manual_urls)):
        raise ValueError("Competitor URLs must be unique")
    if not 1 <= len(manual_urls) <= 10:
        raise ValueError("Provide from 1 to 10 competitor URLs")

    extracted_pages: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for url in manual_urls:
        try:
            response = await guard.fetch(url)
            if response.status_code >= 400:
                errors.append({"url": url, "error": f"HTTP {response.status_code}"})
                continue
            data = extract_html(response.text)
            data["url"] = url
            extracted_pages.append(data)
        except SSRFBlockedError:
            raise
        except Exception:  # noqa: BLE001
            errors.append({"url": url, "error": "Fetch failed"})

    return {
        "urls": manual_urls,
        "extracted": {"pages": extracted_pages, "errors": errors},
        "skeleton": build_skeleton(extracted_pages),
    }
