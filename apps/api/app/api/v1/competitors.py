from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.api.v1.projects import _project_or_404
from app.db.session import get_db
from app.models import (
    CompetitorCrawlPage,
    CompetitorCrawlRun,
    CompetitorScan,
    KnowledgeDoc,
    SchedulerJob,
)
from app.schemas.phase3 import KnowledgeOut, ScanCreate, ScanOut
from app.schemas.research import (
    CompetitorCrawlCancel,
    CompetitorCrawlCreate,
    CompetitorCrawlOut,
    CompetitorCrawlPageOut,
)
from app.services.audit import append_audit
from app.services.competitor import (
    approved_crawl_evidence_content,
    approved_evidence_content,
    scan_competitors,
)
from app.services.competitor_crawl import normalize_root_url
from app.services.scheduler import cancel_job, create_competitor_crawl_job
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


@router.post(
    "/projects/{project_id}/domain-crawls",
    response_model=CompetitorCrawlOut,
    status_code=202,
)
async def create_domain_crawl(
    project_id: UUID,
    body: CompetitorCrawlCreate,
    auth: AuthContext = Depends(_SCAN_WRITE_ROLES),
    db: AsyncSession = Depends(get_db),
) -> CompetitorCrawlRun:
    project = await _project_or_404(db, project_id, auth)
    try:
        scope = normalize_root_url(str(body.root_url))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    active = (
        await db.execute(
            select(CompetitorCrawlRun).where(
                CompetitorCrawlRun.project_id == project.id,
                CompetitorCrawlRun.origin == scope.origin,
                CompetitorCrawlRun.status.in_(("queued", "running")),
            )
        )
    ).scalar_one_or_none()
    if active:
        raise HTTPException(status_code=409, detail="A crawl for this domain is already active")
    crawl = CompetitorCrawlRun(
        tenant_id=project.tenant_id,
        project_id=project.id,
        root_url=scope.root_url,
        origin=scope.origin,
        configuration={"max_pages": body.max_pages, "max_depth": body.max_depth},
        progress={"discovered": 0, "fetched": 0, "skipped": 0, "failed": 0},
    )
    db.add(crawl)
    await db.flush()
    await create_competitor_crawl_job(db, crawl=crawl, requested_by=auth.user.id)
    await append_audit(
        db,
        action="competitor.crawl.queued",
        payload={
            "project_id": str(project.id),
            "crawl_id": str(crawl.id),
            "origin": scope.origin,
            "max_pages": body.max_pages,
            "max_depth": body.max_depth,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(crawl)
    return crawl


@router.get("/projects/{project_id}/domain-crawls", response_model=list[CompetitorCrawlOut])
async def list_domain_crawls(
    project_id: UUID,
    auth: AuthContext = Depends(_SCAN_READ_ROLES),
    db: AsyncSession = Depends(get_db),
) -> list[CompetitorCrawlRun]:
    project = await _project_or_404(db, project_id, auth)
    result = await db.execute(
        select(CompetitorCrawlRun)
        .where(
            CompetitorCrawlRun.project_id == project.id,
            CompetitorCrawlRun.tenant_id == project.tenant_id,
        )
        .order_by(CompetitorCrawlRun.created_at.desc())
    )
    return list(result.scalars().all())


async def _crawl_or_404(
    db: AsyncSession,
    *,
    project_id: UUID,
    crawl_id: UUID,
    auth: AuthContext,
    lock: bool = False,
) -> CompetitorCrawlRun:
    statement = select(CompetitorCrawlRun).where(
        CompetitorCrawlRun.id == crawl_id,
        CompetitorCrawlRun.project_id == project_id,
        CompetitorCrawlRun.tenant_id == auth.tenant_id,
    )
    crawl = (
        await db.execute(statement.with_for_update() if lock else statement)
    ).scalar_one_or_none()
    if not crawl:
        raise HTTPException(status_code=404, detail="Competitor crawl not found")
    return crawl


@router.get("/projects/{project_id}/domain-crawls/{crawl_id}", response_model=CompetitorCrawlOut)
async def get_domain_crawl(
    project_id: UUID,
    crawl_id: UUID,
    auth: AuthContext = Depends(_SCAN_READ_ROLES),
    db: AsyncSession = Depends(get_db),
) -> CompetitorCrawlRun:
    await _project_or_404(db, project_id, auth)
    return await _crawl_or_404(db, project_id=project_id, crawl_id=crawl_id, auth=auth)


@router.get(
    "/projects/{project_id}/domain-crawls/{crawl_id}/pages",
    response_model=list[CompetitorCrawlPageOut],
)
async def list_domain_crawl_pages(
    project_id: UUID,
    crawl_id: UUID,
    offset: int = 0,
    limit: int = 100,
    auth: AuthContext = Depends(_SCAN_READ_ROLES),
    db: AsyncSession = Depends(get_db),
) -> list[CompetitorCrawlPage]:
    if offset < 0 or not 1 <= limit <= 100:
        raise HTTPException(status_code=400, detail="Invalid pagination")
    await _project_or_404(db, project_id, auth)
    await _crawl_or_404(db, project_id=project_id, crawl_id=crawl_id, auth=auth)
    result = await db.execute(
        select(CompetitorCrawlPage)
        .where(
            CompetitorCrawlPage.crawl_run_id == crawl_id,
            CompetitorCrawlPage.tenant_id == auth.tenant_id,
            CompetitorCrawlPage.project_id == project_id,
        )
        .order_by(CompetitorCrawlPage.depth, CompetitorCrawlPage.url)
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


@router.post(
    "/projects/{project_id}/domain-crawls/{crawl_id}/cancel", response_model=CompetitorCrawlOut
)
async def cancel_domain_crawl(
    project_id: UUID,
    crawl_id: UUID,
    body: CompetitorCrawlCancel,
    auth: AuthContext = Depends(_SCAN_WRITE_ROLES),
    db: AsyncSession = Depends(get_db),
) -> CompetitorCrawlRun:
    project = await _project_or_404(db, project_id, auth)
    crawl = await _crawl_or_404(db, project_id=project_id, crawl_id=crawl_id, auth=auth)
    job = await db.scalar(
        select(SchedulerJob)
        .where(
            SchedulerJob.work_type == "competitor_crawl",
            SchedulerJob.source_id == crawl.id,
            SchedulerJob.project_id == project.id,
            SchedulerJob.tenant_id == project.tenant_id,
        )
        .with_for_update()
    )
    if job:
        try:
            await cancel_job(db, job=job)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    else:
        crawl = await _crawl_or_404(
            db, project_id=project_id, crawl_id=crawl_id, auth=auth, lock=True
        )
        if crawl.status not in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="Only active crawls can be cancelled")
        crawl.status = "cancelled"
        crawl.cancelled_at = datetime.now(UTC)
    crawl.error_code = "cancelled_by_operator"
    crawl.error_message = body.reason.strip() if body.reason else None
    await append_audit(
        db,
        action="competitor.crawl.cancelled",
        payload={"project_id": str(project.id), "crawl_id": str(crawl.id)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(crawl)
    return crawl


@router.post(
    "/projects/{project_id}/domain-crawls/{crawl_id}/approve-evidence",
    response_model=KnowledgeOut,
    status_code=201,
)
async def approve_domain_crawl_evidence(
    project_id: UUID,
    crawl_id: UUID,
    auth: AuthContext = Depends(_SCAN_WRITE_ROLES),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeDoc:
    project = await _project_or_404(db, project_id, auth)
    crawl = await _crawl_or_404(db, project_id=project_id, crawl_id=crawl_id, auth=auth)
    if crawl.status not in {"done", "partial"}:
        raise HTTPException(status_code=409, detail="Only completed crawls can become evidence")
    existing = (
        await db.execute(
            select(KnowledgeDoc).where(
                KnowledgeDoc.source_crawl_id == crawl.id,
                KnowledgeDoc.kind == "competitor_crawl_evidence",
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Crawl evidence has already been approved")
    pages = list(
        (
            await db.execute(
                select(CompetitorCrawlPage.signals)
                .where(
                    CompetitorCrawlPage.crawl_run_id == crawl.id,
                    CompetitorCrawlPage.tenant_id == project.tenant_id,
                    CompetitorCrawlPage.project_id == project.id,
                    CompetitorCrawlPage.status == "fetched",
                )
                .order_by(CompetitorCrawlPage.depth, CompetitorCrawlPage.url)
                .limit(500)
            )
        ).scalars()
    )
    evidence = KnowledgeDoc(
        tenant_id=project.tenant_id,
        project_id=project.id,
        title="Approved competitor domain research",
        kind="competitor_crawl_evidence",
        content=approved_crawl_evidence_content(str(crawl.id), crawl.coverage, pages),
        source_crawl_id=crawl.id,
        state="approved",
        approved_by=auth.user.id,
        approved_at=datetime.now(UTC),
    )
    db.add(evidence)
    await append_audit(
        db,
        action="competitor.crawl.evidence.approve",
        payload={
            "project_id": str(project.id),
            "crawl_id": str(crawl.id),
            "evidence_id": str(evidence.id),
            "fetched_page_count": len(pages),
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(evidence)
    return evidence


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
            KnowledgeDoc.kind.in_(("competitor_evidence", "competitor_crawl_evidence")),
            KnowledgeDoc.state == "approved",
        )
        .order_by(KnowledgeDoc.approved_at.desc())
    )
    return list(result.scalars().all())
