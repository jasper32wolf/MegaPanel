from __future__ import annotations

from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models import Site
from app.models.publish import SitePage
from app.services.audit import append_audit
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


class PublishBody(BaseModel):
    publish_state: str = Field(pattern=r"^(draft|published|archived)$")
    page_ids: list[UUID] | None = None


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
    for page in pages:
        page.publish_state = body.publish_state
        if body.publish_state == "published" and page.index_state == "noindex" and not page.thin:
            page.index_state = "queued"
        if body.publish_state == "archived":
            page.index_state = "noindex"

    await append_audit(
        db,
        action="site.publish",
        payload={"site_id": str(site_id), "state": body.publish_state, "pages": len(pages)},
        tenant_id=site.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {
        "site_id": str(site_id),
        "publish_state": body.publish_state,
        "pages_updated": len(pages),
    }


@router.get("/sites/{site_id}/pages")
async def list_pages(
    site_id: UUID,
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
    ),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    site = (await db.execute(select(Site).where(Site.id == site_id))).scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    if auth.role != "superadmin" and site.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    pages = list(
        (await db.execute(select(SitePage).where(SitePage.site_id == site_id))).scalars().all()
    )
    return [
        {
            "id": str(page.id),
            "slug": page.slug,
            "publish_state": page.publish_state,
            "index_state": page.index_state,
            "thin": page.thin,
            "content_chars": page.content_chars,
        }
        for page in pages
    ]
