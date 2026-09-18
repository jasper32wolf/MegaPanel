from __future__ import annotations

from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models import Lead, Site, WebhookDelivery
from app.models.publish import Domain
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("/builds/{site_id}")
async def build_history(
    site_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    from app.models.publish import SiteBuild

    site = (await db.execute(select(Site).where(Site.id == site_id))).scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    if auth.role != "superadmin" and site.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    rows = list(
        (
            await db.execute(
                select(SiteBuild)
                .where(SiteBuild.site_id == site_id)
                .order_by(SiteBuild.created_at.desc())
                .limit(30)
            )
        )
        .scalars()
        .all()
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


@router.get("/reports/summary")
async def report_summary(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "client")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.tenant_id and auth.role != "superadmin":
        raise HTTPException(status_code=403, detail="Tenant required")
    site_predicates = [Site.tenant_id == auth.tenant_id] if auth.tenant_id else []
    lead_predicates = [Lead.tenant_id == auth.tenant_id] if auth.tenant_id else []
    domain_predicates = [Domain.tenant_id == auth.tenant_id] if auth.tenant_id else []
    delivery_predicates = [WebhookDelivery.tenant_id == auth.tenant_id] if auth.tenant_id else []

    sites = list((await db.execute(select(Site).where(*site_predicates))).scalars().all())
    lead_counts = dict(
        (
            await db.execute(
                select(Lead.status, func.count()).where(*lead_predicates).group_by(Lead.status)
            )
        ).all()
    )
    delivery_counts = dict(
        (
            await db.execute(
                select(WebhookDelivery.status, func.count())
                .where(*delivery_predicates)
                .group_by(WebhookDelivery.status)
            )
        ).all()
    )
    domain_counts = dict(
        (
            await db.execute(
                select(Domain.ssl_status, func.count())
                .where(*domain_predicates)
                .group_by(Domain.ssl_status)
            )
        ).all()
    )
    active_leads = sum(lead_counts.get(status, 0) for status in ("new", "qualified"))
    delivery_pending = sum(
        delivery_counts.get(status, 0) for status in ("queued", "retrying", "processing")
    )
    return {
        "sites": len(sites),
        "pages_estimate": sum(len((site.manifest or {}).get("pages") or []) for site in sites),
        "leads": sum(lead_counts.values()),
        "active_leads": active_leads,
        "delivery_pending": delivery_pending,
        "delivery_dead_letter": delivery_counts.get("dead_letter", 0),
        "domains_pending_tls": domain_counts.get("pending", 0),
        "domains_tls_error": domain_counts.get("error", 0),
    }
