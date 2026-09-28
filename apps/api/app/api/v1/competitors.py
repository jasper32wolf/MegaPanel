from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.api.v1.projects import _project_or_404
from app.db.session import get_db
from app.models import CompetitorScan, KnowledgeDoc
from app.schemas.phase3 import KnowledgeOut, ScanCreate, ScanOut
from app.services.audit import append_audit
from app.services.competitor import approved_evidence_content, scan_competitors
from fastapi import APIRouter, Depends, HTTPException
from site_panel_security import SSRFBlockedError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()

_SCAN_READ_ROLES = require_roles("superadmin", "tenant_admin", "manager", "editor")
_SCAN_WRITE_ROLES = require_roles("superadmin", "tenant_admin", "manager")


async def _scan_or_404(
    db: AsyncSession,
    *,
    project_id: UUID,
    scan_id: UUID,
    auth: AuthContext,
) -> CompetitorScan:
    scan = (
        await db.execute(
            select(CompetitorScan).where(
                CompetitorScan.id == scan_id,
                CompetitorScan.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if not scan:
        raise HTTPException(status_code=404, detail="Competitor scan not found")
    if scan.tenant_id != auth.tenant_id and auth.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")
    return scan


@router.post("/projects/{project_id}/scans", response_model=ScanOut, status_code=201)
async def create_scan(
    project_id: UUID,
    body: ScanCreate,
    auth: AuthContext = Depends(_SCAN_WRITE_ROLES),
    db: AsyncSession = Depends(get_db),
) -> CompetitorScan:
    project = await _project_or_404(db, project_id, auth)
    manual_urls = [str(url) for url in body.urls]
    scan = CompetitorScan(
        tenant_id=project.tenant_id,
        project_id=project.id,
        seed_url=manual_urls[0],
        urls=manual_urls,
        status="running",
    )
    db.add(scan)
    await db.flush()
    try:
        result = await scan_competitors(manual_urls)
        scan.urls = result["urls"]
        scan.extracted = result["extracted"]
        scan.skeleton = result["skeleton"]
        scan.status = "done"
    except SSRFBlockedError as exc:
        scan.status = "blocked"
        scan.error = str(exc)
    except Exception:  # noqa: BLE001
        scan.status = "failed"
        scan.error = "Competitor research failed"

    await append_audit(
        db,
        action="competitor.scan",
        payload={
            "project_id": str(project.id),
            "url_count": len(manual_urls),
            "status": scan.status,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(scan)
    return scan


@router.get("/projects/{project_id}/scans", response_model=list[ScanOut])
async def list_scans(
    project_id: UUID,
    auth: AuthContext = Depends(_SCAN_READ_ROLES),
    db: AsyncSession = Depends(get_db),
) -> list[CompetitorScan]:
    project = await _project_or_404(db, project_id, auth)
    result = await db.execute(
        select(CompetitorScan)
        .where(
            CompetitorScan.project_id == project.id,
            CompetitorScan.tenant_id == project.tenant_id,
        )
        .order_by(CompetitorScan.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("/projects/{project_id}/scans/{scan_id}", response_model=ScanOut)
async def get_scan(
    project_id: UUID,
    scan_id: UUID,
    auth: AuthContext = Depends(_SCAN_READ_ROLES),
    db: AsyncSession = Depends(get_db),
) -> CompetitorScan:
    await _project_or_404(db, project_id, auth)
    return await _scan_or_404(db, project_id=project_id, scan_id=scan_id, auth=auth)


@router.post(
    "/projects/{project_id}/scans/{scan_id}/approve-evidence",
    response_model=KnowledgeOut,
    status_code=201,
)
async def approve_scan_evidence(
    project_id: UUID,
    scan_id: UUID,
    auth: AuthContext = Depends(_SCAN_WRITE_ROLES),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeDoc:
    project = await _project_or_404(db, project_id, auth)
    scan = await _scan_or_404(db, project_id=project_id, scan_id=scan_id, auth=auth)
    if scan.status != "done":
        raise HTTPException(status_code=409, detail="Only completed scans can become evidence")
    existing = (
        await db.execute(
            select(KnowledgeDoc).where(
                KnowledgeDoc.source_scan_id == scan.id,
                KnowledgeDoc.kind == "competitor_evidence",
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Competitor evidence has already been approved")
    evidence = KnowledgeDoc(
        tenant_id=project.tenant_id,
        project_id=project.id,
        title=f"Approved competitor evidence ({len(scan.urls)} manual URLs)",
        kind="competitor_evidence",
        content=approved_evidence_content(str(scan.id), scan.urls, scan.skeleton),
        source_scan_id=scan.id,
        state="approved",
        approved_by=auth.user.id,
        approved_at=datetime.now(UTC),
    )
    db.add(evidence)
    await append_audit(
        db,
        action="competitor.evidence.approve",
        payload={
            "project_id": str(project.id),
            "scan_id": str(scan.id),
            "evidence_id": str(evidence.id),
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(evidence)
    return evidence


@router.get("/projects/{project_id}/evidence", response_model=list[KnowledgeOut])
async def list_project_evidence(
    project_id: UUID,
    auth: AuthContext = Depends(_SCAN_READ_ROLES),
    db: AsyncSession = Depends(get_db),
) -> list[KnowledgeDoc]:
    project = await _project_or_404(db, project_id, auth)
    result = await db.execute(
        select(KnowledgeDoc)
        .where(
            KnowledgeDoc.project_id == project.id,
            KnowledgeDoc.tenant_id == project.tenant_id,
            KnowledgeDoc.kind == "competitor_evidence",
            KnowledgeDoc.state == "approved",
        )
        .order_by(KnowledgeDoc.approved_at.desc())
    )
    return list(result.scalars().all())
