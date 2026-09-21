from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.core.rate_limit import client_ip, lead_limiter
from app.db.session import get_db
from app.models.leads import AnalyticsEvent, Consent
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


class ConsentIn(BaseModel):
    tenant_id: UUID
    site_id: UUID | None = None
    visitor_id: str = Field(min_length=8, max_length=64)
    purposes: dict = Field(default_factory=dict)  # analytics, marketing
    gpc: bool = False


class AnalyticsIn(BaseModel):
    tenant_id: UUID
    site_id: UUID | None = None
    event: str = Field(min_length=1, max_length=64)
    path: str | None = None
    payload: dict = Field(default_factory=dict)
    consent_analytics: bool = False


@router.post("/consent", status_code=201)
async def record_consent(
    body: ConsentIn,
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = Consent(
        tenant_id=body.tenant_id,
        site_id=body.site_id,
        visitor_id=body.visitor_id,
        purposes=body.purposes,
        gpc=body.gpc,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"id": str(row.id), "ok": True}


@router.post("/consent/revoke")
async def revoke_consent(
    visitor_id: str,
    tenant_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from sqlalchemy import select

    rows = list(
        (
            await db.execute(
                select(Consent).where(
                    Consent.visitor_id == visitor_id,
                    Consent.tenant_id == tenant_id,
                    Consent.revoked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    for r in rows:
        r.revoked_at = now
    await db.commit()
    return {"revoked": len(rows)}


@router.post("/collect")
async def collect_event(
    body: AnalyticsIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Privacy-first pixel ingestion — only after consent (TZ 11)."""
    lead_limiter.check(f"pixel:{client_ip(request)}")
    if not body.consent_analytics:
        return {"ok": False, "reason": "no_consent"}
    # No raw IP stored
    evt = AnalyticsEvent(
        tenant_id=body.tenant_id,
        site_id=body.site_id,
        event=body.event,
        path=body.path,
        payload={k: v for k, v in body.payload.items() if k not in {"ip", "email", "phone"}},
    )
    db.add(evt)
    await db.commit()
    return {"ok": True}


# Minimal vanilla pixel source for sites
PIXEL_JS = (
    "(function(){if(!window.__spConsent)return;var d=document,s=d.currentScript,"
    "t=s&&s.getAttribute('data-tenant'),i=s&&s.getAttribute('data-site');"
    "fetch('/api/v1/analytics/collect',{method:'POST',headers:{'Content-Type':'application/json'},"
    "body:JSON.stringify({tenant_id:t,site_id:i,event:'pageview',path:location.pathname,"
    "consent_analytics:!!window.__spConsent.analytics,payload:{}})});})();"
)


@router.get("/pixel.js")
async def pixel_js() -> dict:
    return {"js": PIXEL_JS, "bytes": len(PIXEL_JS)}


@router.get("/legal/templates")
async def legal_templates(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
) -> dict:
    return {
        "pages": ["/privacy/", "/terms/", "/cookie-policy/"],
        "jurisdictions": ["152-FZ", "GDPR", "CCPA"],
        "note": "Templates require lawyer review before production",
        "cookie_banner": "vanilla_light",
    }
