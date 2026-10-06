"""Consent-gated first-party telemetry ingestion and operator summaries."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.core.config import get_settings
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
from app.services.telemetry_retention import (
    lock_session,
    page_view_summary,
    revoke_session,
    session_was_revoked,
)
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


class TelemetryEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=40, max_length=200)
    event: str = Field(min_length=1, max_length=64)
    path: str = Field(min_length=1, max_length=512)
    session_id: str = Field(min_length=16, max_length=128)
    consent_analytics: bool


class TelemetryRevokeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=40, max_length=200)
    session_id: str = Field(min_length=16, max_length=128)


async def _site_from_public_token(db: AsyncSession, *, token: str, request: Request) -> Site:
    try:
        site_id = UUID(token.split(".", 1)[0])
    except (IndexError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid telemetry token") from exc
    site = (await db.execute(select(Site).where(Site.id == site_id))).scalar_one_or_none()
    if site is None or not verify_telemetry_token(token=token, site_id=site.id, domain=site.domain):
        raise HTTPException(status_code=404, detail="Telemetry site not found")
    origin = request.headers.get("origin")
    if not origin:
        raise HTTPException(status_code=403, detail="Telemetry requires a same-origin request")
    try:
        parsed = urlsplit(origin)
        valid_scheme = parsed.scheme == "https" or (
            get_settings().app_env.lower() != "production"
            and parsed.scheme == "http"
            and site.domain.lower() in {"localhost", "127.0.0.1"}
        )
        valid_origin = (
            valid_scheme
            and parsed.hostname is not None
            and parsed.hostname.lower() == site.domain.lower()
            and parsed.port in (None, 443 if parsed.scheme == "https" else 80)
            and not parsed.username
            and not parsed.password
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        valid_origin = False
    if not valid_origin:
        raise HTTPException(status_code=403, detail="Telemetry origin does not match the site")
    return site


@router.post("/collect", status_code=status.HTTP_202_ACCEPTED)
async def collect_telemetry(
    body: TelemetryEventIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Persist an allowlisted, consented event without IP or raw session identity."""
    await shared_lead_limiter.check(rate_limit_key("telemetry", client_ip(request)))
    if (
        not body.consent_analytics
        or request.headers.get("Sec-GPC") == "1"
        or request.headers.get("DNT") == "1"
    ):
        return {"ok": False, "reason": "no_consent"}
    if body.event not in _ALLOWED_EVENTS:
        raise HTTPException(status_code=400, detail="Unsupported telemetry event")
    path = normalize_telemetry_path(body.path)
    if path is None:
        raise HTTPException(status_code=400, detail="Telemetry path is invalid")
    site = await _site_from_public_token(db, token=body.token, request=request)
    digest = session_digest(body.session_id)
    if digest is None:
        raise HTTPException(status_code=400, detail="Invalid telemetry session")
    await lock_session(db, digest)
    if await session_was_revoked(
        db, tenant_id=site.tenant_id, site_id=site.id, digest=digest, now=datetime.now(UTC)
    ):
        await db.rollback()
        return {"ok": False, "reason": "consent_withdrawn"}
    db.add(
        AnalyticsEvent(
            tenant_id=site.tenant_id,
            site_id=site.id,
            event=body.event,
            path=path,
            payload={"session": digest},
            source="first_party",
        )
    )
    await db.commit()
    return {"ok": True}


@router.post("/revoke", status_code=status.HTTP_202_ACCEPTED)
async def revoke_telemetry(
    body: TelemetryRevokeIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Withdraw one browser session's raw events without identifying the visitor."""
    await shared_lead_limiter.check(rate_limit_key("telemetry_revoke", client_ip(request)))
    site = await _site_from_public_token(db, token=body.token, request=request)
    digest = session_digest(body.session_id)
    if digest is None:
        raise HTTPException(status_code=400, detail="Invalid telemetry session")
    await revoke_session(db, tenant_id=site.tenant_id, site_id=site.id, digest=digest)
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
    settings = get_settings()
    return {
        "site_id": str(site.id),
        "days": days,
        "privacy": {
            "consent_required": True,
            "ip_stored": False,
            "low_sample_threshold": 5,
            "raw_retention_days": settings.telemetry_raw_retention_days,
            "aggregate_retention_days": settings.telemetry_aggregate_retention_days,
            "session_metric": "sum_of_daily_distinct_sessions",
        },
        "pages": await page_view_summary(db, tenant_id=site.tenant_id, site_id=site.id, days=days),
    }
