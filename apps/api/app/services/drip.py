"""Drip-publishing: promote noindex → indexed (TZ 7.1)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Site
from app.models.publish import SitePage
from app.services.indexnow import submit_indexnow


async def promote_drip(
    session: AsyncSession,
    *,
    tenant_id: UUID | None = None,
    limit: int = 40,
) -> dict:
    """Promote 30–50 queued/noindex published pages to indexed daily."""
    stmt = (
        select(SitePage)
        .where(
            SitePage.publish_state == "published",
            SitePage.index_state.in_(["noindex", "queued"]),
            SitePage.thin.is_(False),
        )
        .order_by(SitePage.created_at.asc())
        .limit(limit)
    )
    if tenant_id:
        stmt = stmt.where(SitePage.tenant_id == tenant_id)
    result = await session.execute(stmt)
    pages = list(result.scalars().all())
    if not pages:
        return {"promoted": 0, "urls": []}

    now = datetime.now(UTC)
    by_site: dict[UUID, list[SitePage]] = {}
    for p in pages:
        p.index_state = "indexed"
        p.promoted_at = now
        by_site.setdefault(p.site_id, []).append(p)

    submitted = []
    for site_id, site_pages in by_site.items():
        site = (await session.execute(select(Site).where(Site.id == site_id))).scalar_one_or_none()
        if not site or not site.indexnow_key:
            continue
        urls = [f"https://{site.domain}/{p.slug.strip('/')}/" if p.slug.strip("/") else f"https://{site.domain}/" for p in site_pages]
        key_loc = f"https://{site.domain}/{site.indexnow_key}.txt"
        res = await submit_indexnow(host=site.domain, key=site.indexnow_key, key_location=key_loc, urls=urls)
        submitted.append({"site_id": str(site_id), **res})

    await session.flush()
    return {
        "promoted": len(pages),
        "urls": [p.slug for p in pages],
        "indexnow": submitted,
    }


async def queue_for_index(session: AsyncSession, page_ids: list[UUID]) -> int:
    result = await session.execute(
        update(SitePage)
        .where(SitePage.id.in_(page_ids), SitePage.thin.is_(False))
        .values(index_state="queued")
    )
    return result.rowcount or 0
