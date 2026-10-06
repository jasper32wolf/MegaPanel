from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as element_tree
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import UUID

from app.models import CompetitorCrawlPage, CompetitorCrawlRun, SchedulerJob
from app.services.audit import append_audit
from site_panel_security import SSRFBlockedError, SSRFGuard
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

MAX_CRAWL_PAGES = 500
MAX_CRAWL_DEPTH = 12
MAX_SITEMAP_URLS = 5_000
MAX_SITEMAPS = 25
DEFAULT_MAX_PAGES = 100
DEFAULT_MAX_DEPTH = 8


@dataclass(frozen=True)
class CrawlScope:
    root_url: str
    origin: str
    host: str


@dataclass(frozen=True)
class DiscoveredURL:
    url: str
    parent_url: str | None
    depth: int
    source: str


def normalize_root_url(value: str) -> CrawlScope:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Competitor research requires a public HTTPS root URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Root URL cannot contain credentials, query, or fragment")
    if parsed.port not in {None, 443}:
        raise ValueError("Competitor research only allows HTTPS port 443")
    host = parsed.hostname.lower().rstrip(".")
    path = parsed.path or "/"
    root_url = urlunsplit(("https", host, path, "", ""))
    return CrawlScope(root_url=root_url, origin=f"https://{host}", host=host)


def normalize_same_origin_url(value: str, scope: CrawlScope) -> str | None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    if parsed.username or parsed.password or parsed.port not in {None, 443}:
        return None
    if parsed.hostname.lower().rstrip(".") != scope.host:
        return None
    if parsed.query:
        return None
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return urlunsplit(("https", scope.host, path, "", ""))


def _robots_directives(text: str, scope: CrawlScope) -> tuple[list[str], list[str]]:
    applies = False
    disallowed: list[str] = []
    sitemap_urls: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (item.strip() for item in line.split(":", 1))
        normalized_key = key.lower()
        if normalized_key == "user-agent":
            applies = value.lower() in {"*", "sitepanelbot"}
        elif normalized_key == "disallow" and applies and value:
            disallowed.append(value)
        elif normalized_key == "sitemap":
            sitemap = normalize_same_origin_url(value, scope)
            if sitemap:
                sitemap_urls.append(sitemap)
    return disallowed, list(dict.fromkeys(sitemap_urls))[:MAX_SITEMAPS]


def _is_allowed_by_robots(url: str, disallowed: list[str]) -> bool:
    path = urlsplit(url).path or "/"
    return not any(path.startswith(rule) for rule in disallowed if rule)


def _sitemap_urls(content: bytes, scope: CrawlScope) -> list[str]:
    try:
        root = element_tree.fromstring(content)
    except element_tree.ParseError:
        return []
    urls: list[str] = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "loc" or not node.text:
            continue
        normalized = normalize_same_origin_url(node.text.strip(), scope)
        if normalized:
            urls.append(normalized)
        if len(urls) >= MAX_SITEMAP_URLS:
            break
    return list(dict.fromkeys(urls))


class _SignalExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.canonical_url: str | None = None
        self.robots = ""
        self.headings: list[dict[str, str]] = []
        self.links: list[str] = []
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._json_ld = False
        self._json_ld_buffer: list[str] = []
        self.json_ld: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        if tag == "title" or tag in {f"h{level}" for level in range(1, 7)}:
            self._capture = tag
            self._buffer = []
        elif tag == "meta" and attributes.get("name", "").lower() == "description":
            self.description = attributes.get("content", "")
        elif tag == "meta" and attributes.get("name", "").lower() == "robots":
            self.robots = attributes.get("content", "")
        elif tag == "link" and attributes.get("rel", "").lower() == "canonical":
            self.canonical_url = attributes.get("href") or None
        elif tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"])
        elif tag == "script" and attributes.get("type", "").lower() == "application/ld+json":
            self._json_ld = True
            self._json_ld_buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag == self._capture:
            text = re.sub(r"\s+", " ", "".join(self._buffer)).strip()
            if tag == "title":
                self.title = text
            elif text:
                self.headings.append({"level": tag, "text": text})
            self._capture = None
            self._buffer = []
        if tag == "script" and self._json_ld:
            self.json_ld.append("".join(self._json_ld_buffer))
            self._json_ld = False
            self._json_ld_buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)
        if self._json_ld:
            self._json_ld_buffer.append(data)


def _bounded(value: object, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()[:limit]


def _json_items(value: object) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        values = value.get("@graph", [value])
        return values if isinstance(values, list) else []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _json_ld_signals(raw_blocks: list[str]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    faqs: list[dict[str, str]] = []
    prices: list[dict[str, str]] = []
    for raw in raw_blocks[:20]:
        try:
            items = _json_items(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
        for item in items:
            item_type = item.get("@type")
            types = {item_type} if isinstance(item_type, str) else set(item_type or [])
            if "FAQPage" in types:
                for question in item.get("mainEntity", [])[:20]:
                    if not isinstance(question, dict):
                        continue
                    answer = question.get("acceptedAnswer", {})
                    answer = answer if isinstance(answer, dict) else {}
                    name = _bounded(question.get("name", ""), 255)
                    if name:
                        faqs.append(
                            {
                                "question": name,
                                "answer": _bounded(answer.get("text", ""), 500),
                                "source": "json_ld",
                            }
                        )
            offers = item.get("offers")
            for offer in offers if isinstance(offers, list) else [offers]:
                if not isinstance(offer, dict):
                    continue
                value = offer.get("price") or offer.get("lowPrice")
                if value is not None:
                    prices.append(
                        {
                            "value": _bounded(value, 64),
                            "currency": _bounded(offer.get("priceCurrency", ""), 8),
                            "type": "range" if offer.get("lowPrice") is not None else "fixed",
                            "source": "json_ld",
                        }
                    )
    return faqs[:30], prices[:30]


def extract_page_signals(
    html: str, page_url: str, scope: CrawlScope
) -> tuple[dict[str, Any], list[str]]:
    parser = _SignalExtractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001
        pass
    faqs, prices = _json_ld_signals(parser.json_ld)
    for heading in parser.headings:
        if "?" in heading["text"] and len(faqs) < 30:
            faqs.append({"question": heading["text"], "answer": "", "source": "heading"})
    price_pattern = re.compile(r"(?:от\s*)?(\d[\d\s]{0,12})(?:\s*)(₽|руб\.?|р\.)", re.IGNORECASE)
    for match in price_pattern.finditer(html):
        if len(prices) == 30:
            break
        prices.append(
            {
                "value": re.sub(r"\s+", "", match.group(1)),
                "currency": "RUB",
                "type": "from" if match.group(0).lower().lstrip().startswith("от") else "fixed",
                "source": "text_pattern",
            }
        )
    links: list[str] = []
    for href in parser.links[:5_000]:
        normalized = normalize_same_origin_url(urljoin(page_url, href), scope)
        if normalized and normalized not in links:
            links.append(normalized)
    canonical = (
        normalize_same_origin_url(urljoin(page_url, parser.canonical_url), scope)
        if parser.canonical_url
        else None
    )
    return (
        {
            "title": _bounded(parser.title, 255),
            "meta_description": _bounded(parser.description, 500),
            "headings": parser.headings[:100],
            "canonical_url": canonical,
            "robots": _bounded(parser.robots, 200),
            "faq": faqs,
            "prices": prices,
            "price_present": bool(prices),
        },
        links,
    )


def crawler_guard() -> SSRFGuard:
    return SSRFGuard(
        timeout=15.0,
        max_response_bytes=2_000_000,
        require_global_ips=True,
        allowed_schemes=("https",),
        allowed_ports=(443,),
    )


async def recover_legacy_competitor_crawls(db: AsyncSession) -> int:
    """Surface orphaned pre-scheduler crawls without replaying partial page writes."""
    cutoff = datetime.now(UTC) - timedelta(minutes=10)
    managed = select(SchedulerJob.id).where(
        SchedulerJob.work_type == "competitor_crawl",
        SchedulerJob.source_id == CompetitorCrawlRun.id,
    )
    runs = list(
        (
            await db.execute(
                select(CompetitorCrawlRun)
                .where(
                    CompetitorCrawlRun.status == "running",
                    CompetitorCrawlRun.started_at < cutoff,
                    ~managed.exists(),
                )
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for run in runs:
        run.status = "failed"
        run.error_code = "worker_timeout"
        run.error_message = "Competitor research worker timed out"
        run.finished_at = datetime.now(UTC)
        await append_audit(
            db,
            action="competitor.crawl.worker_timeout",
            payload={"project_id": str(run.project_id), "crawl_id": str(run.id)},
            tenant_id=run.tenant_id,
            actor_id=None,
        )
    if runs:
        await db.commit()
    return len(runs)


async def run_competitor_crawl(db: AsyncSession, crawl_id: UUID) -> dict[str, Any]:
    run = (
        await db.execute(
            select(CompetitorCrawlRun).where(CompetitorCrawlRun.id == crawl_id).with_for_update()
        )
    ).scalar_one_or_none()
    if run is None:
        return {"status": "missing"}
    if run.status != "queued":
        return {"status": run.status, "duplicate": True}

    scope = normalize_root_url(run.root_url)
    config = dict(run.configuration or {})
    max_pages = min(int(config.get("max_pages", DEFAULT_MAX_PAGES)), MAX_CRAWL_PAGES)
    max_depth = min(int(config.get("max_depth", DEFAULT_MAX_DEPTH)), MAX_CRAWL_DEPTH)
    run.status = "running"
    run.started_at = datetime.now(UTC)
    run.progress = {
        "discovered": 0,
        "fetched": 0,
        "skipped": 0,
        "failed": 0,
        "max_pages": max_pages,
    }
    await db.commit()

    guard = crawler_guard()
    queue: deque[DiscoveredURL] = deque()
    seen: set[str] = set()
    disallowed: list[str] = []
    sitemap_urls: list[str] = []
    sitemap_count = 0

    def enqueue(item: DiscoveredURL) -> None:
        if (
            item.url not in seen
            and len(seen) < MAX_SITEMAP_URLS
            and item.depth <= max_depth
            and _is_allowed_by_robots(item.url, disallowed)
        ):
            seen.add(item.url)
            queue.append(item)

    try:
        robots_url = f"{scope.origin}/robots.txt"
        try:
            robots_status, _, robots_body = await guard.fetch_text(robots_url)
            if robots_status == 200:
                disallowed, sitemap_urls = _robots_directives(
                    robots_body.decode("utf-8", errors="replace"), scope
                )
            elif robots_status not in {404, 410}:
                raise RuntimeError(f"robots_http_{robots_status}")
        except SSRFBlockedError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("robots_unavailable") from exc

        sitemap_queue = deque(sitemap_urls + [f"{scope.origin}/sitemap.xml"])
        seen_sitemaps: set[str] = set()
        while sitemap_queue and sitemap_count < MAX_SITEMAPS:
            sitemap = sitemap_queue.popleft()
            if sitemap in seen_sitemaps:
                continue
            seen_sitemaps.add(sitemap)
            sitemap_count += 1
            try:
                status, _, body = await guard.fetch_text(
                    sitemap,
                    accepted_content_types=("application/xml", "text/xml", "text/plain"),
                )
            except SSRFBlockedError:
                raise
            except Exception:  # noqa: BLE001
                continue
            if status != 200:
                continue
            for discovered in _sitemap_urls(body, scope):
                if discovered.endswith(".xml") and len(sitemap_queue) < MAX_SITEMAPS:
                    sitemap_queue.append(discovered)
                else:
                    enqueue(DiscoveredURL(discovered, None, 0, "sitemap"))

        enqueue(DiscoveredURL(scope.root_url, None, 0, "seed"))
        while queue and int(run.progress.get("fetched", 0)) < max_pages:
            current = queue.popleft()
            fresh_run = await db.get(CompetitorCrawlRun, run.id, populate_existing=True)
            if fresh_run is None or fresh_run.status != "running":
                break
            page = CompetitorCrawlPage(
                crawl_run_id=run.id,
                tenant_id=run.tenant_id,
                project_id=run.project_id,
                url=current.url,
                parent_url=current.parent_url,
                discovery_source=current.source,
                depth=current.depth,
            )
            db.add(page)
            await db.flush()
            try:
                status, _, body = await guard.fetch_text(
                    current.url,
                    accepted_content_types=("text/html", "application/xhtml+xml"),
                )
                page.http_status = status
                if status >= 400:
                    page.status = "skipped"
                    page.error_code = f"http_{status}"
                    run.progress["skipped"] = int(run.progress.get("skipped", 0)) + 1
                else:
                    html = body.decode("utf-8", errors="replace")
                    signals, links = extract_page_signals(html, current.url, scope)
                    page.canonical_url = signals.pop("canonical_url") or current.url
                    page.content_hash = hashlib.sha256(body).hexdigest()
                    page.signals = signals
                    page.status = "fetched"
                    page.fetched_at = datetime.now(UTC)
                    run.progress["fetched"] = int(run.progress.get("fetched", 0)) + 1
                    for link in links:
                        enqueue(DiscoveredURL(link, current.url, current.depth + 1, "link"))
            except SSRFBlockedError:
                page.status = "skipped"
                page.error_code = "ssrf_blocked"
                run.progress["skipped"] = int(run.progress.get("skipped", 0)) + 1
            except Exception:  # noqa: BLE001
                page.status = "failed"
                page.error_code = "fetch_failed"
                run.progress["failed"] = int(run.progress.get("failed", 0)) + 1
            run.progress["discovered"] = len(seen)
            await db.commit()

        run = await db.get(CompetitorCrawlRun, run.id, populate_existing=True)
        assert run is not None
        if run.status == "running":
            run.coverage = {
                "origin": scope.origin,
                "robots_respected": True,
                "sitemaps_checked": sitemap_count,
                "page_limit_reached": bool(queue),
                "coverage": "partial" if queue else "bounded_complete",
            }
            run.status = "partial" if queue else "done"
            run.finished_at = datetime.now(UTC)
        await db.commit()
        return {"status": run.status, "crawl_id": str(run.id)}
    except SSRFBlockedError:
        run = await db.get(CompetitorCrawlRun, crawl_id, populate_existing=True)
        assert run is not None
        if run.status == "running":
            run.status = "blocked"
            run.error_code = "ssrf_blocked"
            run.error_message = "The competitor domain resolved to a non-public address"
            run.finished_at = datetime.now(UTC)
        await db.commit()
        return {"status": run.status, "crawl_id": str(run.id)}
    except Exception:  # noqa: BLE001
        run = await db.get(CompetitorCrawlRun, crawl_id, populate_existing=True)
        assert run is not None
        if run.status == "running":
            run.status = "failed"
            run.error_code = "crawler_failed"
            run.error_message = "Competitor research could not be completed"
            run.finished_at = datetime.now(UTC)
        await db.commit()
        return {"status": run.status, "crawl_id": str(run.id)}
