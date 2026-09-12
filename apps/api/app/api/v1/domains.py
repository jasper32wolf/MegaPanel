from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_roles
from app.core.config import get_settings
from app.db.session import get_db
from app.models import Site
from app.models.publish import Domain, Redirect
from app.services.audit import append_audit
from app.services.caddy_client import CaddyClient

router = APIRouter()
settings = get_settings()


class DomainCreate(BaseModel):
    hostname: str = Field(min_length=3, max_length=255)
    site_id: UUID | None = None
    registrar: str | None = None
    expires_at: datetime | None = None


class RedirectCreate(BaseModel):
    site_id: UUID
    from_path: str
    to_url: str
    code: int = Field(default=301, ge=301, le=308)


@router.post("", status_code=201)
async def create_domain(
    body: DomainCreate,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    host = body.hostname.lower().strip()
    existing = await db.execute(select(Domain).where(Domain.hostname == host))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Hostname taken")
    domain = Domain(
        tenant_id=auth.tenant_id,
        site_id=body.site_id,
        hostname=host,
        registrar=body.registrar,
        expires_at=body.expires_at,
        ssl_status="pending",
        dns_status="unknown",
    )
    db.add(domain)
    root = f"{settings.sites_root}/{body.site_id}/current" if body.site_id else settings.sites_root
    caddy = await CaddyClient().upsert_site_vhost(host, root)
    domain.ssl_status = "pending" if caddy.get("ok") else "error"
    domain.meta = {"caddy": caddy}
    await append_audit(
        db,
        action="domain.create",
        payload={"hostname": host},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(domain)
    return {
        "id": str(domain.id),
        "hostname": domain.hostname,
        "ssl_status": domain.ssl_status,
        "caddy": caddy,
    }


@router.get("")
async def list_domains(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "viewer")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    stmt = select(Domain).order_by(Domain.created_at.desc())
    if auth.role != "superadmin":
        if not auth.tenant_id:
            raise HTTPException(status_code=403, detail="Tenant required")
        stmt = stmt.where(Domain.tenant_id == auth.tenant_id)
    rows = list((await db.execute(stmt)).scalars().all())
    return [
        {
            "id": str(d.id),
            "hostname": d.hostname,
            "site_id": str(d.site_id) if d.site_id else None,
            "ssl_status": d.ssl_status,
            "dns_status": d.dns_status,
            "expires_at": d.expires_at.isoformat() if d.expires_at else None,
            "registrar": d.registrar,
        }
        for d in rows
    ]


@router.get("/health/{domain_id}")
async def domain_health(
    domain_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.domain_health import domain_probe

    d = (await db.execute(select(Domain).where(Domain.id == domain_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(status_code=404, detail="Not found")
    if auth.role != "superadmin" and d.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    caddy_ok = await CaddyClient().health()
    probe = domain_probe(d.hostname)
    d.dns_status = probe["dns_status"]
    d.ssl_status = probe["ssl_status"]
    await db.commit()
    return {
        "hostname": d.hostname,
        "ssl_status": d.ssl_status,
        "dns_status": d.dns_status,
        "caddy_reachable": caddy_ok,
        "expiry_alerts": _expiry_window(d.expires_at),
        "probe": probe,
    }


def _expiry_window(expires_at: datetime | None) -> list[int]:
    if not expires_at:
        return []
    from datetime import UTC

    days = (expires_at.replace(tzinfo=UTC) - datetime.now(UTC)).days
    return [w for w in (60, 30, 14, 7) if days <= w]


@router.post("/redirects", status_code=201)
async def create_redirect(
    body: RedirectCreate,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    site = (await db.execute(select(Site).where(Site.id == body.site_id))).scalar_one_or_none()
    if not site or (auth.role != "superadmin" and site.tenant_id != auth.tenant_id):
        raise HTTPException(status_code=404, detail="Site not found")
    redir = Redirect(
        tenant_id=auth.tenant_id,
        site_id=body.site_id,
        from_path=body.from_path,
        to_url=body.to_url,
        code=body.code,
    )
    db.add(redir)
    caddy = await CaddyClient().add_redirect(site.domain, body.from_path, body.to_url, body.code)
    await append_audit(
        db,
        action="redirect.create",
        payload={"from": body.from_path, "to": body.to_url},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(redir)
    return {"id": str(redir.id), "caddy": caddy}
