from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_roles
from app.core.config import get_settings
from app.db.session import get_db
from app.models import Site
from app.models.ops import ContentDecayEvent, FootprintAudit, SerpCheck, StagingApproval
from app.models.publish import SitePage
from app.services.audit import append_audit
from app.services.ops import (
    auto_correct_from_serp,
    detect_decay,
    evergreen_refresh_text,
    mock_serp_position,
    record_serp_check,
    run_footprint_audit,
)

router = APIRouter()
settings = get_settings()


class SerpCheckIn(BaseModel):
    site_id: UUID
    query: str
    page_id: UUID | None = None
    engine: str = "yandex"
    days_live: int = Field(default=50, ge=0)
    position: int | None = None  # if None — mock provider


class DecayIn(BaseModel):
    site_id: UUID
    page_id: UUID | None = None
    metric: str = "traffic"
    baseline: float
    current: float


class StagingIn(BaseModel):
    site_id: UUID
    build_hash: str | None = None


class StagingDecide(BaseModel):
    status: str = Field(pattern=r"^(approved|rejected)$")
    notes: str | None = None


@router.post("/serp/check")
async def serp_check(
    body: SerpCheckIn,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    site = (await db.execute(select(Site).where(Site.id == body.site_id))).scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    if auth.role != "superadmin" and site.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    position = body.position if body.position is not None else mock_serp_position(body.query, site.domain)
    row = await record_serp_check(
        db,
        tenant_id=site.tenant_id,
        site_id=site.id,
        query=body.query,
        position=position,
        engine=body.engine,
        page_id=body.page_id,
    )
    correction = None
    if body.page_id:
        page = (await db.execute(select(SitePage).where(SitePage.id == body.page_id))).scalar_one_or_none()
        if page:
            correction = await auto_correct_from_serp(
                db, site=site, page=page, position=position, days_live=body.days_live
            )
    await append_audit(
        db,
        action="serp.check",
        payload={"query": body.query, "position": position, "corrected": bool(correction)},
        tenant_id=site.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {
        "id": str(row.id),
        "position": position,
        "needs_correction": correction is not None,
        "correction": correction,
    }


@router.post("/decay")
async def content_decay(
    body: DecayIn,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    site = (await db.execute(select(Site).where(Site.id == body.site_id))).scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    if auth.role != "superadmin" and site.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    decayed = detect_decay(body.baseline, body.current)
    action = "evergreen" if decayed else "none"
    if decayed and body.page_id:
        # Apply evergreen refresh to on-disk HTML if present
        page = (await db.execute(select(SitePage).where(SitePage.id == body.page_id))).scalar_one_or_none()
        if page:
            rel = page.slug.strip("/") or "index"
            path = Path(settings.sites_root) / str(site.id) / "current" / rel / "index.html"
            if path.exists():
                path.write_text(evergreen_refresh_text(path.read_text(encoding="utf-8")), encoding="utf-8")
            action = "evergreen_applied"

    evt = ContentDecayEvent(
        tenant_id=site.tenant_id,
        site_id=site.id,
        page_id=body.page_id,
        metric=body.metric,
        baseline=body.baseline,
        current=body.current,
        action=action,
    )
    db.add(evt)
    await db.commit()
    await db.refresh(evt)
    return {"id": str(evt.id), "decayed": decayed, "action": action}


@router.post("/footprint/{site_id}")
async def footprint(
    site_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    site = (await db.execute(select(Site).where(Site.id == site_id))).scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    if auth.role != "superadmin" and site.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    html = ""
    index_path = Path(settings.sites_root) / str(site.id) / "current" / "index" / "index.html"
    if index_path.exists():
        html = index_path.read_text(encoding="utf-8")
    else:
        # Fallback sample from manifest blocks
        html = str(site.manifest)

    audit = await run_footprint_audit(db, site, html)
    await append_audit(
        db,
        action="footprint.audit",
        payload={"site_id": str(site_id), "risk": audit.risk_score},
        tenant_id=site.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"id": str(audit.id), "risk_score": audit.risk_score, "findings": audit.findings}


@router.post("/staging", status_code=201)
async def request_staging(
    body: StagingIn,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    site = (await db.execute(select(Site).where(Site.id == body.site_id))).scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    if auth.role != "superadmin" and site.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    build_hash = body.build_hash or site.build_hash
    if not build_hash:
        raise HTTPException(status_code=400, detail="No build_hash — build site first")
    row = StagingApproval(
        tenant_id=site.tenant_id,
        site_id=site.id,
        build_hash=build_hash,
        status="pending",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"id": str(row.id), "status": row.status, "build_hash": row.build_hash}


@router.post("/staging/{approval_id}/decide")
async def decide_staging(
    approval_id: UUID,
    body: StagingDecide,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "client")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = (await db.execute(select(StagingApproval).where(StagingApproval.id == approval_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    if auth.role != "superadmin" and row.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    row.status = body.status
    row.notes = body.notes
    row.reviewer_id = auth.user.id
    row.decided_at = datetime.now(UTC)
    if body.status == "approved":
        site = (await db.execute(select(Site).where(Site.id == row.site_id))).scalar_one()
        site.publish_state = "published"
    await db.commit()
    return {"id": str(row.id), "status": row.status}


@router.get("/serp/{site_id}")
async def list_serp(
    site_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "viewer")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    rows = list(
        (await db.execute(select(SerpCheck).where(SerpCheck.site_id == site_id).order_by(SerpCheck.checked_at.desc()).limit(50))).scalars().all()
    )
    return [
        {
            "id": str(r.id),
            "query": r.query,
            "position": r.position,
            "engine": r.engine,
            "checked_at": r.checked_at.isoformat() if r.checked_at else None,
        }
        for r in rows
    ]
