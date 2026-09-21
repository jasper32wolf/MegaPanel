"""Wayback / drop-domain 301 mapper (TZ 7.5)."""

from __future__ import annotations

from urllib.parse import urlparse

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models import Site
from app.models.publish import Redirect
from app.services.audit import append_audit
from app.services.caddy_client import CaddyClient
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


class WaybackMapIn(BaseModel):
    site_id: str
    source_urls: list[HttpUrl] = Field(min_length=1, max_length=500)
    target_path_mode: str = Field(default="path", pattern=r"^(path|home)$")


@router.post("/wayback-301")
async def wayback_301(
    body: WaybackMapIn,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from uuid import UUID

    site = (
        await db.execute(select(Site).where(Site.id == UUID(body.site_id)))
    ).scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    if auth.role != "superadmin" and site.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    if not auth.tenant_id and auth.role != "superadmin":
        raise HTTPException(status_code=403, detail="Tenant required")

    tenant_id = auth.tenant_id or site.tenant_id
    created = 0
    caddy = CaddyClient()
    for raw in body.source_urls:
        parsed = urlparse(str(raw))
        from_path = parsed.path or "/"
        to_url = (
            f"https://{site.domain}/"
            if body.target_path_mode == "home"
            else f"https://{site.domain}{from_path}"
        )
        db.add(
            Redirect(
                tenant_id=tenant_id,
                site_id=site.id,
                from_path=from_path,
                to_url=to_url,
                code=301,
            )
        )
        await caddy.add_redirect(site.domain, from_path, to_url, 301)
        created += 1

    await append_audit(
        db,
        action="wayback.map301",
        payload={"site_id": str(site.id), "created": created},
        tenant_id=tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"created": created, "domain": site.domain}
