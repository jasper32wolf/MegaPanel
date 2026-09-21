"""SERP self-correction, decay detector, footprint scanner, evergreen (TZ 7.5 / 3.3 / 8)."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.models import Site
from app.models.ops import FootprintAudit, SerpCheck
from app.models.publish import SitePage
from app.services.indexnow import submit_indexnow
from sqlalchemy.ext.asyncio import AsyncSession


async def record_serp_check(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    site_id: UUID,
    query: str,
    position: int | None,
    engine: str = "yandex",
    page_id: UUID | None = None,
    url_found: str | None = None,
) -> SerpCheck:
    row = SerpCheck(
        tenant_id=tenant_id,
        site_id=site_id,
        page_id=page_id,
        query=query,
        engine=engine,
        position=position,
        url_found=url_found,
    )
    session.add(row)
    await session.flush()
    return row


def needs_serp_correction(
    position: int | None, days_live: int, *, top_n: int = 30, after_days: int = 45
) -> bool:
    """If not in TOP-30 after 45 days → regenerate title/h1/FAQ (TZ 7.5)."""
    if days_live < after_days:
        return False
    if position is None:
        return True
    return position > top_n


def regenerate_meta_local(page_title: str, h1: str, city_prep: str, service: str) -> dict[str, str]:
    """Zero/low-token SERP correction variants."""
    return {
        "title": f"{service} в {city_prep} — цены, сроки, гарантия | {page_title[:40]}",
        "h1": f"{service} в {city_prep}: выезд сегодня",
        "faq_q": f"Сколько стоит {service} в {city_prep}?",
        "faq_a": (
            f"Стоимость {service} в {city_prep} зависит от объёма. Даём смету до начала работ."
        ),
    }


def detect_decay(baseline: float, current: float, *, drop_ratio: float = 0.35) -> bool:
    if baseline <= 0:
        return False
    return (baseline - current) / baseline >= drop_ratio


def evergreen_refresh_text(html: str, year: int | None = None) -> str:
    """Zero-token Evergreen Refresher — update years/dates placeholders."""
    y = year or datetime.now(UTC).year
    html = re.sub(r"\b20[1-2]\d\b", str(y), html)
    html = re.sub(r"\{year\}", str(y), html)
    html = re.sub(r"\{price_from\}", "от 990 ₽", html)
    return html


def footprint_scan_html(html: str, css_vars: dict[str, str], seed: int) -> dict[str, Any]:
    """
    Internal Self-Audit Footprint Scanner (TZ 3.3).
    Risk report for operator only — not a cloaking tool.
    """
    findings: list[dict[str, Any]] = []
    risk = 0

    # Shared CSS variable fingerprints
    if css_vars.get("primary-color") in {"#1a5f4a", "#7c3aed", "#6366f1"}:
        findings.append(
            {"code": "shared_css_var", "severity": "common", "detail": "primary-color cluster"}
        )
        risk += 15

    # CMS path leftovers
    if re.search(r"/(wp-content|bitrix|catalog/product)/", html, re.I):
        findings.append({"code": "cms_path", "severity": "high", "detail": "CMS-like path residue"})
        risk += 40

    # Identical phone patterns across sites often use same mask
    phones = re.findall(r"\+7\s*\(\d{3}\)\s*\d{3}-\d{2}-\d{2}", html)
    if phones:
        findings.append(
            {"code": "phone_format", "severity": "low", "detail": f"count={len(phones)}"}
        )
        risk += 5

    # Block order entropy from seed
    blocks = re.findall(r'data-block="([^"]+)"', html)
    if blocks and seed % 3 == 0 and blocks == sorted(blocks):
        findings.append(
            {
                "code": "sorted_blocks",
                "severity": "medium",
                "detail": "deterministic ascending block order",
            }
        )
        risk += 20

    # Generic class names without hash
    if re.search(r'class="(hero|container|wrapper)"', html):
        findings.append(
            {"code": "generic_class", "severity": "medium", "detail": "non-hashed class names"}
        )
        risk += 15

    return {"risk_score": min(risk, 100), "findings": findings}


async def run_footprint_audit(session: AsyncSession, site: Site, html: str) -> FootprintAudit:
    manifest = site.manifest or {}
    result = footprint_scan_html(html, manifest.get("css_vars") or {}, seed=42)
    audit = FootprintAudit(
        tenant_id=site.tenant_id,
        site_id=site.id,
        risk_score=result["risk_score"],
        findings=result["findings"],
    )
    session.add(audit)
    await session.flush()
    return audit


async def auto_correct_from_serp(
    session: AsyncSession,
    *,
    site: Site,
    page: SitePage,
    position: int | None,
    days_live: int,
) -> dict[str, Any] | None:
    if not needs_serp_correction(position, days_live):
        return None
    service = (page.manifest or {}).get("service") or site.niche or "Услуги"
    city_prep = (page.manifest or {}).get("city_prep") or "городе"
    meta = regenerate_meta_local(page.title or service, service, city_prep, service)
    page.manifest = {
        **(page.manifest or {}),
        "serp_correction": meta,
        "corrected_at": datetime.now(UTC).isoformat(),
    }
    if site.indexnow_key:
        url = (
            f"https://{site.domain}/{page.slug.strip('/')}/"
            if page.slug.strip("/")
            else f"https://{site.domain}/"
        )
        await submit_indexnow(
            host=site.domain,
            key=site.indexnow_key,
            key_location=f"https://{site.domain}/{site.indexnow_key}.txt",
            urls=[url],
        )
    return meta


def mock_serp_position(query: str, domain: str) -> int | None:
    """Deterministic mock for local/dev without paid SERP APIs."""
    h = int(hashlib.md5(f"{query}:{domain}".encode()).hexdigest(), 16)
    pos = (h % 80) + 1
    return pos if pos <= 60 else None
