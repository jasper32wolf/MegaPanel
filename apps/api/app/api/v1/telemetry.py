"""Consent-gated first-party telemetry ingestion and operator summaries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.core.rate_limit import client_ip, rate_limit_key, shared_lead_limiter
from app.db.session import get_db
from app.models import Site
from app.models.leads import AnalyticsEvent
from app.services.telemetry import (
    _ALLOWED_EVENTS,
    normalize_telemetry_path,
    session_digest,
    verify_telemetry_token,
)
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


class TelemetryEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=40, max_length=200)
    event: str = Field(min_length=1, max_length=64)
    path: str = Field(min_length=1, max_length=512)
    session_id: str | None = Field(default=None, min_length=16, max_length=128)
    consent_analytics: bool


async def _site_from_public_token(
    db: AsyncSession, *, body: TelemetryEventIn, request: Request
) -> Site:
    try:
        site_id = UUID(body.token.split(".", 1)[0])
    except (IndexError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid telemetry token") from exc
    site = (await db.execute(select(Site).where(Site.id == site_id))).scalar_one_or_none()
    if site is None or not verify_telemetry_token(
        token=body.token, site_id=site.id, domain=site.domain
    ):
        raise HTTPException(status_code=404, detail="Telemetry site not found")
    origin = request.headers.get("origin")
    if not origin:
        raise HTTPException(status_code=403, detail="Telemetry requires a same-origin request")
    origin_host = urlsplit(origin).hostname
    if not origin_host or origin_host.lower() != site.domain.lower():
        raise HTTPException(status_code=403, detail="Telemetry origin does not match the site")
    return site


@router.post("/collect", status_code=status.HTTP_202_ACCEPTED)
async def collect_telemetry(
    body: TelemetryEventIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Persist an allowlisted, consented event without IP or personal data."""
    await shared_lead_limiter.check(rate_limit_key("telemetry", client_ip(request)))
    if not body.consent_analytics:
        return {"ok": False, "reason": "no_consent"}
    if body.event not in _ALLOWED_EVENTS:
        raise HTTPException(status_code=400, detail="Unsupported telemetry event")
    path = normalize_telemetry_path(body.path)
    if path is None:
        raise HTTPException(status_code=400, detail="Telemetry path is invalid")
    site = await _site_from_public_token(db, body=body, request=request)
    db.add(
        AnalyticsEvent(
            tenant_id=site.tenant_id,
            site_id=site.id,
            event=body.event,
            path=path,
            payload={"session": session_digest(body.session_id)} if body.session_id else {},
        )
    )
    await db.commit()
    return {"ok": True}


@router.get("/sites/{site_id}/summary")
async def telemetry_summary(
    site_id: UUID,
    days: int = 30,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not 1 <= days <= 90:
        raise HTTPException(status_code=400, detail="Days must be between 1 and 90")
    site = (await db.execute(select(Site).where(Site.id == site_id))).scalar_one_or_none()
    if not site or (auth.role != "superadmin" and site.tenant_id != auth.tenant_id):
        raise HTTPException(status_code=404, detail="Site not found")
    cutoff = datetime.now(UTC) - timedelta(days=days)
    session_value = AnalyticsEvent.payload["session"].astext
    rows = (
        await db.execute(
            select(
                AnalyticsEvent.path,
                func.count(AnalyticsEvent.id).label("page_views"),
                func.count(func.distinct(session_value)).label("consented_sessions"),
            )
            .where(
                AnalyticsEvent.tenant_id == site.tenant_id,
                AnalyticsEvent.site_id == site.id,
                AnalyticsEvent.event == "page_view",
                AnalyticsEvent.created_at >= cutoff,
            )
            .group_by(AnalyticsEvent.path)
            .order_by(func.count(AnalyticsEvent.id).desc(), AnalyticsEvent.path)
            .limit(500)
        )
    ).all()
    return {
        "site_id": str(site.id),
        "days": days,
        "privacy": {
            "consent_required": True,
            "ip_stored": False,
            "low_sample_threshold": 5,
        },
        "pages": [
            {
                "path": path or "/",
                "page_views": int(page_views),
                "consented_sessions": int(consented_sessions),
                "low_sample": int(consented_sessions) < 5,
                "traffic_state": (
                    "not_enough_data"
                    if int(consented_sessions) < 5
                    else "low_traffic"
                    if int(page_views) < 10
                    else "observed"
                ),
            }
            for path, page_views, consented_sessions in rows
        ],
    }
