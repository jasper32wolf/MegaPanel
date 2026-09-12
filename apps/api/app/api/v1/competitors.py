from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models import CompetitorScan, KnowledgeDoc
from app.schemas.phase3 import KnowledgeOut, ScanCreate, ScanOut
from app.services.audit import append_audit
from app.services.competitor import scan_competitor
from site_panel_security import SSRFBlockedError

router = APIRouter()


@router.post("/scan", response_model=ScanOut, status_code=201)
async def create_scan(
    body: ScanCreate,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> CompetitorScan:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    scan = CompetitorScan(
        tenant_id=auth.tenant_id,
        seed_url=str(body.seed_url),
        status="running",
    )
    db.add(scan)
    await db.flush()
    try:
        result = await scan_competitor(str(body.seed_url), max_pages=body.max_pages)
        scan.urls = result["urls"]
        scan.extracted = result["extracted"]
        scan.skeleton = result["skeleton"]
        scan.status = "done"
        doc = KnowledgeDoc(
            tenant_id=auth.tenant_id,
            title=f"Skeleton from {body.seed_url}",
            kind="skeleton",
            content=result["skeleton"],
            source_scan_id=scan.id,
        )
        db.add(doc)
    except SSRFBlockedError as exc:
        scan.status = "blocked"
        scan.error = str(exc)
    except Exception as exc:  # noqa: BLE001
        scan.status = "failed"
        scan.error = str(exc)

    await append_audit(
        db,
        action="competitor.scan",
        payload={"seed_url": str(body.seed_url), "status": scan.status},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(scan)
    return scan


@router.get("/scans", response_model=list[ScanOut])
async def list_scans(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> list[CompetitorScan]:
    if not auth.tenant_id and auth.role != "superadmin":
        raise HTTPException(status_code=403, detail="Tenant required")
    stmt = select(CompetitorScan).order_by(CompetitorScan.created_at.desc())
    if auth.role != "superadmin":
        stmt = stmt.where(CompetitorScan.tenant_id == auth.tenant_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/knowledge", response_model=list[KnowledgeOut])
async def list_knowledge(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> list[KnowledgeDoc]:
    if not auth.tenant_id and auth.role != "superadmin":
        raise HTTPException(status_code=403, detail="Tenant required")
    stmt = select(KnowledgeDoc).order_by(KnowledgeDoc.created_at.desc())
    if auth.role != "superadmin":
        stmt = stmt.where(KnowledgeDoc.tenant_id == auth.tenant_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/scans/{scan_id}", response_model=ScanOut)
async def get_scan(
    scan_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> CompetitorScan:
    result = await db.execute(select(CompetitorScan).where(CompetitorScan.id == scan_id))
    scan = result.scalar_one_or_none()
    if not scan:
        raise HTTPException(status_code=404, detail="Not found")
    if auth.role != "superadmin" and scan.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return scan
