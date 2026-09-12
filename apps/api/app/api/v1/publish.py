from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models import Site
from app.models.publish import SitePage
from app.services.audit import append_audit
from app.services.drip import promote_drip, queue_for_index
from app.services.indexnow import submit_indexnow

router = APIRouter()


class PublishBody(BaseModel):
    publish_state: str = Field(pattern=r"^(draft|published|archived)$")
    page_ids: list[UUID] | None = None  # None = all pages of site


class ForceIndexBody(BaseModel):
    page_ids: list[UUID]
    reason: str = Field(min_length=3, max_length=500)


class QueueIndexBody(BaseModel):
    page_ids: list[UUID]


@router.post("/sites/{site_id}/publish")
async def publish_site(
    site_id: UUID,
    body: PublishBody,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    site = (await db.execute(select(Site).where(Site.id == site_id))).scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    if auth.role != "superadmin" and site.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    site.publish_state = body.publish_state
    stmt = select(SitePage).where(SitePage.site_id == site_id)
    if body.page_ids:
        stmt = stmt.where(SitePage.id.in_(body.page_ids))
    pages = list((await db.execute(stmt)).scalars().all())
    for p in pages:
        p.publish_state = body.publish_state
        # Publishing does NOT auto-index (TZ 7.1 / 17.4)
        if body.publish_state == "published" and p.index_state == "noindex" and not p.thin:
            p.index_state = "queued"
        if body.publish_state == "archived":
            p.index_state = "noindex"

    await append_audit(
        db,
        action="site.publish",
        payload={"site_id": str(site_id), "state": body.publish_state, "pages": len(pages)},
        tenant_id=site.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"site_id": str(site_id), "publish_state": body.publish_state, "pages_updated": len(pages)}


@router.post("/drip/run")
async def run_drip(
    limit: int = 40,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    tenant_id = None if auth.role == "superadmin" else auth.tenant_id
    result = await promote_drip(db, tenant_id=tenant_id, limit=min(max(limit, 1), 50))
    await append_audit(
        db,
        action="drip.promote",
        payload=result,
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return result


@router.post("/force-index")
async def force_index(
    body: ForceIndexBody,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Requires admin.force_index privilege (TZ 7.1) — mapped to tenant_admin+."""
    pages = list(
        (await db.execute(select(SitePage).where(SitePage.id.in_(body.page_ids)))).scalars().all()
    )
    if not pages:
        raise HTTPException(status_code=404, detail="No pages")
    for p in pages:
        if auth.role != "superadmin" and p.tenant_id != auth.tenant_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        p.index_state = "indexed"
        p.promoted_at = datetime.now(UTC)
        p.thin = False
    await append_audit(
        db,
        action="admin.force_index",
        payload={"page_ids": [str(i) for i in body.page_ids], "reason": body.reason},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"indexed": len(pages)}


@router.post("/indexnow/{site_id}")
async def indexnow_site(
    site_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    site = (await db.execute(select(Site).where(Site.id == site_id))).scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    if auth.role != "superadmin" and site.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    if not site.indexnow_key:
        raise HTTPException(status_code=400, detail="No IndexNow key")
    pages = list(
        (
            await db.execute(
                select(SitePage).where(
                    SitePage.site_id == site_id, SitePage.index_state == "indexed"
                )
            )
        ).scalars().all()
    )
    urls = [
        f"https://{site.domain}/" if not p.slug.strip("/") else f"https://{site.domain}/{p.slug.strip('/')}/"
        for p in pages
    ]
    result = await submit_indexnow(
        host=site.domain,
        key=site.indexnow_key,
        key_location=f"https://{site.domain}/{site.indexnow_key}.txt",
        urls=urls,
    )
    await append_audit(
        db,
        action="indexnow.submit",
        payload={"site_id": str(site_id), "count": len(urls)},
        tenant_id=site.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return result


@router.get("/sites/{site_id}/pages")
async def list_pages(
    site_id: UUID,
    auth: AuthContext = Depends(require_roles(
        "superadmin", "tenant_admin", "manager", "editor", "viewer"
    )),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    site = (await db.execute(select(Site).where(Site.id == site_id))).scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    if auth.role != "superadmin" and site.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    pages = list((await db.execute(select(SitePage).where(SitePage.site_id == site_id))).scalars().all())
    return [
        {
            "id": str(p.id),
            "slug": p.slug,
            "publish_state": p.publish_state,
            "index_state": p.index_state,
            "thin": p.thin,
            "content_chars": p.content_chars,
        }
        for p in pages
    ]


@router.post("/queue-index")
async def queue_index(
    body: QueueIndexBody,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    n = await queue_for_index(db, body.page_ids)
    await db.commit()
    return {"queued": n}
