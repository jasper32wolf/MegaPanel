from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.core.config import get_settings
from app.db.session import get_db
from app.models import Site
from app.models.publish import Domain, Redirect
from app.services.audit import append_audit
from app.services.caddy_client import CaddyClient
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
settings = get_settings()

_HOST_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


def normalize_hostname(raw: str) -> str:
    hostname = raw.strip().lower().rstrip(".")
    if len(hostname) > 253 or "." not in hostname:
        raise ValueError("Hostname must be a public domain")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("IP addresses are not valid hostnames")
    if any(not _HOST_LABEL.fullmatch(label) for label in hostname.split(".")):
        raise ValueError("Invalid hostname")
    return hostname


def validate_redirect(site: Site, from_path: str, to_url: str) -> tuple[str, str]:
    path = from_path.strip()
    target = to_url.strip()
    if not path.startswith("/") or path.startswith("//") or any(ord(char) < 32 for char in path):
        raise ValueError("Redirect source must be an absolute path")
    if any(ord(char) < 32 for char in target):
        raise ValueError("Redirect target is invalid")
    parsed = urlparse(target)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Redirect target is invalid") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != site.domain
        or parsed.username
        or parsed.password
        or port not in (None, 443)
    ):
        raise ValueError("Redirect target must use this site's HTTPS domain")
    return path, target


class DomainCreate(BaseModel):
    hostname: str = Field(min_length=3, max_length=255)
    site_id: UUID
    registrar: str | None = None
    expires_at: datetime | None = None


class RedirectCreate(BaseModel):
    site_id: UUID
    from_path: str = Field(max_length=1024)
    to_url: str = Field(max_length=2048)
    code: Literal[301, 302, 303, 307, 308] = 301


def require_site_owner(auth: AuthContext, site: Site) -> None:
    if auth.role != "superadmin" and site.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")


def require_domain_owner(auth: AuthContext, domain: Domain) -> None:
    if auth.role != "superadmin" and domain.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("", status_code=201)
async def create_domain(
    body: DomainCreate,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        hostname = normalize_hostname(body.hostname)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    site = (await db.execute(select(Site).where(Site.id == body.site_id))).scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    require_site_owner(auth, site)
    existing = await db.execute(select(Domain).where(Domain.hostname == hostname))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Hostname taken")
    primary_site = await db.execute(select(Site.id).where(Site.domain == hostname))
    if primary_site.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Hostname is already a primary site domain")

    domain = Domain(
        tenant_id=site.tenant_id,
        site_id=site.id,
        hostname=hostname,
        registrar=body.registrar,
        expires_at=body.expires_at,
        ssl_status="pending",
        dns_status="unknown",
    )
    db.add(domain)
    root = f"{settings.caddy_sites_root}/{site.id}/current"
    caddy = await CaddyClient().upsert_site_vhost(hostname, root)
    domain.ssl_status = "pending" if caddy.get("ok") else "error"
    domain.meta = {"caddy": caddy}
    await append_audit(
        db,
        action="domain.create",
        payload={"hostname": hostname, "site_id": str(site.id)},
        tenant_id=site.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(domain)
    return {
        "id": str(domain.id),
        "hostname": domain.hostname,
        "site_id": str(domain.site_id),
        "ssl_status": domain.ssl_status,
        "caddy": caddy,
    }


@router.get("")
async def list_domains(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "viewer")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    statement = select(Domain).order_by(Domain.created_at.desc())
    if auth.role != "superadmin":
        if not auth.tenant_id:
            raise HTTPException(status_code=403, detail="Operator scope required")
        statement = statement.where(Domain.tenant_id == auth.tenant_id)
    rows = list((await db.execute(statement)).scalars().all())
    return [
        {
            "id": str(domain.id),
            "hostname": domain.hostname,
            "site_id": str(domain.site_id) if domain.site_id else None,
            "ssl_status": domain.ssl_status,
            "dns_status": domain.dns_status,
            "expires_at": domain.expires_at.isoformat() if domain.expires_at else None,
            "registrar": domain.registrar,
        }
        for domain in rows
    ]


@router.delete("/{domain_id}")
async def delete_domain(
    domain_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    domain = (await db.execute(select(Domain).where(Domain.id == domain_id))).scalar_one_or_none()
    if not domain:
        raise HTTPException(status_code=404, detail="Not found")
    require_domain_owner(auth, domain)
    primary_site = await db.execute(select(Site.id).where(Site.domain == domain.hostname))
    if primary_site.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Primary site domains cannot be removed here")
    caddy = await CaddyClient().delete_site_vhost(domain.hostname)
    if not caddy["ok"]:
        raise HTTPException(status_code=503, detail="Caddy configuration could not be removed")
    await db.delete(domain)
    await append_audit(
        db,
        action="domain.delete",
        payload={
            "hostname": domain.hostname,
            "site_id": str(domain.site_id) if domain.site_id else None,
        },
        tenant_id=domain.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"id": str(domain_id), "deleted": True}


@router.get("/health/{domain_id}")
async def domain_health(
    domain_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.domain_health import domain_probe

    domain = (await db.execute(select(Domain).where(Domain.id == domain_id))).scalar_one_or_none()
    if not domain:
        raise HTTPException(status_code=404, detail="Not found")
    require_domain_owner(auth, domain)
    caddy_ok = await CaddyClient().health()
    probe = domain_probe(domain.hostname)
    domain.dns_status = probe["dns_status"]
    domain.ssl_status = probe["ssl_status"]
    await db.commit()
    return {
        "hostname": domain.hostname,
        "ssl_status": domain.ssl_status,
        "dns_status": domain.dns_status,
        "caddy_reachable": caddy_ok,
        "expiry_alerts": _expiry_window(domain.expires_at),
        "probe": probe,
    }


def _expiry_window(expires_at: datetime | None) -> list[int]:
    if not expires_at:
        return []
    from datetime import UTC

    days = (expires_at.replace(tzinfo=UTC) - datetime.now(UTC)).days
    return [window for window in (60, 30, 14, 7) if days <= window]


@router.get("/redirects")
async def list_redirects(
    site_id: UUID | None = None,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "viewer")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    statement = (
        select(Redirect, Site.domain)
        .join(Site, Site.id == Redirect.site_id)
        .order_by(Redirect.created_at.desc())
    )
    if auth.role != "superadmin":
        if not auth.tenant_id:
            raise HTTPException(status_code=403, detail="Operator scope required")
        statement = statement.where(Redirect.tenant_id == auth.tenant_id)
    if site_id:
        statement = statement.where(Redirect.site_id == site_id)
    rows = (await db.execute(statement)).all()
    return [
        {
            "id": str(redirect.id),
            "site_id": str(redirect.site_id),
            "site_domain": site_domain,
            "from_path": redirect.from_path,
            "to_url": redirect.to_url,
            "code": redirect.code,
            "created_at": redirect.created_at.isoformat() if redirect.created_at else None,
        }
        for redirect, site_domain in rows
    ]


@router.post("/redirects", status_code=201)
async def create_redirect(
    body: RedirectCreate,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    site = (await db.execute(select(Site).where(Site.id == body.site_id))).scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    require_site_owner(auth, site)
    try:
        from_path, to_url = validate_redirect(site, body.from_path, body.to_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    existing = await db.execute(
        select(Redirect.id).where(Redirect.site_id == site.id, Redirect.from_path == from_path)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Redirect source already exists")
    redirect = Redirect(
        tenant_id=site.tenant_id,
        site_id=site.id,
        from_path=from_path,
        to_url=to_url,
        code=body.code,
    )
    db.add(redirect)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Redirect source already exists") from exc
    caddy = await CaddyClient().add_redirect(
        site.domain, from_path, to_url, body.code, redirect_id=redirect.id
    )
    if not caddy["ok"]:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Caddy configuration failed")
    await append_audit(
        db,
        action="redirect.create",
        payload={
            "redirect_id": str(redirect.id),
            "from": from_path,
            "to": to_url,
            "site_id": str(site.id),
        },
        tenant_id=site.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(redirect)
    return {"id": str(redirect.id), "caddy": caddy}


@router.delete("/redirects/{redirect_id}")
async def delete_redirect(
    redirect_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = (
        await db.execute(
            select(Redirect, Site)
            .join(Site, Site.id == Redirect.site_id)
            .where(Redirect.id == redirect_id)
        )
    ).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    redirect, site = row
    require_site_owner(auth, site)
    caddy = await CaddyClient().delete_redirect(redirect.id)
    if not caddy["ok"]:
        raise HTTPException(status_code=503, detail="Caddy configuration could not be removed")
    await db.delete(redirect)
    await append_audit(
        db,
        action="redirect.delete",
        payload={
            "redirect_id": str(redirect.id),
            "from": redirect.from_path,
            "site_id": str(site.id),
        },
        tenant_id=redirect.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"id": str(redirect_id), "deleted": True}
