from __future__ import annotations

import hashlib
import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models import AuditLog, Lead, Site, Tenant
from app.models.panel import ApiKey, Notification, Plugin, SavedView, WebhookSubscription
from app.models.publish import Domain
from app.services.audit import append_audit

router = APIRouter()


class SavedViewIn(BaseModel):
    name: str
    entity: str
    filters: dict = Field(default_factory=dict)


class ApiKeyIn(BaseModel):
    name: str
    scopes: list[str] = Field(default_factory=lambda: ["read"])


class WebhookIn(BaseModel):
    url: HttpUrl
    events: list[str] = Field(default_factory=lambda: ["lead.created"])
    secret: str | None = None


class BrandingIn(BaseModel):
    logo_url: str | None = None
    primary_color: str | None = None
    panel_domain: str | None = None
    company_name: str | None = None


class NotifyIn(BaseModel):
    title: str
    body: str = ""
    priority: str = "normal"
    group_key: str | None = None


@router.get("/search")
async def command_palette(
    q: str = Query(min_length=1, max_length=100),
    auth: AuthContext = Depends(require_roles(
        "superadmin", "tenant_admin", "manager", "editor", "viewer"
    )),
    db: AsyncSession = Depends(get_db),
) -> dict:
    like = f"%{q}%"
    tenant_filter = auth.tenant_id if auth.role != "superadmin" else None
    sites_q = select(Site).where(Site.domain.ilike(like)).limit(10)
    leads_q = select(Lead).limit(10)
    domains_q = select(Domain).where(Domain.hostname.ilike(like)).limit(10)
    if tenant_filter:
        sites_q = sites_q.where(Site.tenant_id == tenant_filter)
        leads_q = leads_q.where(Lead.tenant_id == tenant_filter)
        domains_q = domains_q.where(Domain.tenant_id == tenant_filter)
    sites = list((await db.execute(sites_q)).scalars().all())
    domains = list((await db.execute(domains_q)).scalars().all())
    # leads: match blind or status/page
    leads = list((await db.execute(leads_q)).scalars().all())
    leads = [l for l in leads if q.lower() in (l.page_slug or "").lower() or q.lower() in l.status]
    return {
        "sites": [{"id": str(s.id), "domain": s.domain, "href": f"/sites"} for s in sites],
        "domains": [{"id": str(d.id), "hostname": d.hostname} for d in domains],
        "leads": [{"id": str(l.id), "status": l.status, "href": "/leads"} for l in leads[:5]],
        "breadcrumbs_hint": ["tenant", "site", "page", "lead"],
    }


@router.get("/notifications")
async def list_notifications(
    auth: AuthContext = Depends(require_roles(
        "superadmin", "tenant_admin", "manager", "editor", "viewer"
    )),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    if not auth.tenant_id and auth.role != "superadmin":
        raise HTTPException(status_code=403, detail="Tenant required")
    stmt = select(Notification).order_by(Notification.created_at.desc()).limit(50)
    if auth.tenant_id:
        stmt = stmt.where(Notification.tenant_id == auth.tenant_id)
    rows = list((await db.execute(stmt)).scalars().all())
    return [
        {
            "id": str(n.id),
            "title": n.title,
            "body": n.body,
            "priority": n.priority,
            "read": n.read,
            "group_key": n.group_key,
        }
        for n in rows
    ]


@router.post("/notifications", status_code=201)
async def create_notification(
    body: NotifyIn,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    n = Notification(
        tenant_id=auth.tenant_id,
        user_id=auth.user.id,
        title=body.title,
        body=body.body,
        priority=body.priority,
        group_key=body.group_key,
    )
    db.add(n)
    await db.commit()
    await db.refresh(n)
    return {"id": str(n.id)}


@router.get("/audit")
async def audit_trail(
    action: str | None = None,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(100)
    if auth.role != "superadmin" and auth.tenant_id:
        stmt = stmt.where(or_(AuditLog.tenant_id == auth.tenant_id, AuditLog.tenant_id.is_(None)))
    if action:
        stmt = stmt.where(AuditLog.action == action)
    rows = list((await db.execute(stmt)).scalars().all())
    return [
        {
            "id": r.id,
            "action": r.action,
            "payload": r.payload,
            "record_hash": r.record_hash,
            "prev_hash": r.prev_hash,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/builds/{site_id}")
async def build_history(
    site_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    from app.models.publish import SiteBuild

    rows = list(
        (
            await db.execute(
                select(SiteBuild).where(SiteBuild.site_id == site_id).order_by(SiteBuild.created_at.desc()).limit(30)
            )
        ).scalars().all()
    )
    return [
        {
            "id": str(b.id),
            "status": b.status,
            "build_hash": b.build_hash,
            "previous_build_hash": b.previous_build_hash,
            "pages_built": b.pages_built,
            "duration_ms": b.duration_ms,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        }
        for b in rows
    ]


@router.post("/views", status_code=201)
async def save_view(
    body: SavedViewIn,
    auth: AuthContext = Depends(require_roles(
        "superadmin", "tenant_admin", "manager", "editor", "viewer"
    )),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    view = SavedView(
        tenant_id=auth.tenant_id,
        user_id=auth.user.id,
        name=body.name,
        entity=body.entity,
        filters=body.filters,
    )
    db.add(view)
    await db.commit()
    await db.refresh(view)
    return {"id": str(view.id)}


@router.patch("/branding")
async def update_branding(
    body: BrandingIn,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    tenant = (await db.execute(select(Tenant).where(Tenant.id == auth.tenant_id))).scalar_one()
    branding = dict(tenant.branding or {})
    branding.update({k: v for k, v in body.model_dump().items() if v is not None})
    tenant.branding = branding
    await append_audit(
        db,
        action="tenant.branding",
        payload=branding,
        tenant_id=tenant.id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"branding": branding}


@router.post("/api-keys", status_code=201)
async def create_api_key(
    body: ApiKeyIn,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    raw = f"sp_{secrets.token_urlsafe(24)}"
    key = ApiKey(
        tenant_id=auth.tenant_id,
        name=body.name,
        key_prefix=raw[:10],
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        scopes=body.scopes,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return {"id": str(key.id), "key": raw, "prefix": key.key_prefix, "scopes": key.scopes}


@router.post("/webhooks", status_code=201)
async def create_webhook(
    body: WebhookIn,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    wh = WebhookSubscription(
        tenant_id=auth.tenant_id,
        url=str(body.url),
        events=body.events,
        secret=body.secret or secrets.token_urlsafe(16),
    )
    db.add(wh)
    await db.commit()
    await db.refresh(wh)
    return {"id": str(wh.id), "url": wh.url, "events": wh.events}


@router.get("/plugins")
async def list_plugins(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    rows = list((await db.execute(select(Plugin).where(Plugin.enabled.is_(True)))).scalars().all())
    if not rows:
        # Seed built-in hook points
        defaults = [
            Plugin(key="block.validator", name="Block Validator Hook", hooks=["block.pre_save"]),
            Plugin(key="lead.router", name="Lead Router Hook", hooks=["lead.created"]),
            Plugin(key="build.post", name="Post-Build Hook", hooks=["build.finished"]),
        ]
        for p in defaults:
            db.add(p)
        await db.commit()
        rows = defaults
    return [{"id": str(p.id), "key": p.key, "name": p.name, "hooks": p.hooks} for p in rows]


@router.get("/reports/summary")
async def report_summary(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "client")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.tenant_id and auth.role != "superadmin":
        raise HTTPException(status_code=403, detail="Tenant required")
    sites_q = select(Site)
    leads_q = select(Lead)
    if auth.tenant_id:
        sites_q = sites_q.where(Site.tenant_id == auth.tenant_id)
        leads_q = leads_q.where(Lead.tenant_id == auth.tenant_id)
    sites = list((await db.execute(sites_q)).scalars().all())
    leads = list((await db.execute(leads_q)).scalars().all())
    qualified = sum(1 for l in leads if l.status in {"qualified", "sent"})
    return {
        "sites": len(sites),
        "pages_estimate": sum(len((s.manifest or {}).get("pages") or []) for s in sites),
        "leads": len(leads),
        "qualified_leads": qualified,
        "conversion_hint": round(qualified / max(len(leads), 1), 3),
        "export": ["csv", "json"],
    }


@router.get("/reports/export.csv")
async def report_export_csv(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "client")),
    db: AsyncSession = Depends(get_db),
):
    import csv
    import io

    from fastapi.responses import StreamingResponse

    if not auth.tenant_id and auth.role != "superadmin":
        raise HTTPException(status_code=403, detail="Tenant required")
    sites_q = select(Site)
    if auth.tenant_id:
        sites_q = sites_q.where(Site.tenant_id == auth.tenant_id)
    sites = list((await db.execute(sites_q)).scalars().all())
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["site_id", "domain", "publish_state", "version", "build_hash", "pages"])
    for s in sites:
        writer.writerow(
            [
                str(s.id),
                s.domain,
                s.publish_state,
                s.version,
                s.build_hash or "",
                len((s.manifest or {}).get("pages") or []),
            ]
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=sites-report.csv"},
    )
