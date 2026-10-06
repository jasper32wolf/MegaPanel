from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.api.v1.projects import _project_or_404
from app.core.security import sha256_hex
from app.db.session import get_db
from app.models import (
    Keyword,
    ProjectBukvarixKeywordResult,
    ProjectBukvarixKeywordRun,
    ProjectKeyword,
    ProjectSemanticSourceRun,
    ProjectSemanticSourceRunKeyword,
)
from app.schemas.research import (
    BukvarixKeywordResultOut,
    BukvarixKeywordRunCommit,
    BukvarixKeywordRunCommitOut,
    BukvarixKeywordRunCreate,
    BukvarixKeywordRunOut,
    BukvarixProviderStatus,
    SemanticSourceRunCreate,
    SemanticSourceRunOut,
)
from app.services.audit import append_audit
from app.services.bukvarix_https import MAX_RESULTS_PER_RUN, MAX_SEED_QUERIES
from app.services.scheduler import create_bukvarix_keyword_job
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
_READ = require_roles("superadmin", "tenant_admin", "manager", "editor")
_WRITE = require_roles("superadmin", "tenant_admin", "manager", "editor")


def _source_run_out(
    run: ProjectSemanticSourceRun, selected_keyword_count: int
) -> SemanticSourceRunOut:
    return SemanticSourceRunOut(
        id=run.id,
        project_id=run.project_id,
        provider=run.provider,
        acquisition=run.acquisition,
        mode=run.mode,
        source_label=run.source_label,
        observed_at=run.observed_at,
        notes=run.notes,
        selected_keyword_count=selected_keyword_count,
        created_at=run.created_at,
    )


@router.get("/{project_id}/semantic-sources/bukvarix/status", response_model=BukvarixProviderStatus)
async def bukvarix_status(
    project_id: UUID,
    auth: AuthContext = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> BukvarixProviderStatus:
    await _project_or_404(db, project_id, auth)
    return BukvarixProviderStatus(
        message=(
            "Автоматический Букварикс включён только в фиксированном HTTPS public free-mode. "
            "Панель не принимает personal API key, URL или endpoint и не импортирует ключи "
            "без preview и явного commit."
        ),
        max_seed_keywords=MAX_SEED_QUERIES,
        max_results_per_run=MAX_RESULTS_PER_RUN,
    )


def _bukvarix_result_out(result: ProjectBukvarixKeywordResult) -> BukvarixKeywordResultOut:
    return BukvarixKeywordResultOut(
        id=result.id,
        source_project_keyword_id=result.source_project_keyword_id,
        phrase=result.phrase,
        metrics=result.metrics or [],
    )


async def _bukvarix_run_out(
    db: AsyncSession, run: ProjectBukvarixKeywordRun, *, include_results: bool = True
) -> BukvarixKeywordRunOut:
    results = []
    if include_results and run.status == "completed":
        rows = list(
            (
                await db.execute(
                    select(ProjectBukvarixKeywordResult)
                    .where(
                        ProjectBukvarixKeywordResult.run_id == run.id,
                        ProjectBukvarixKeywordResult.tenant_id == run.tenant_id,
                        ProjectBukvarixKeywordResult.project_id == run.project_id,
                    )
                    .order_by(ProjectBukvarixKeywordResult.normalized)
                    .limit(MAX_RESULTS_PER_RUN)
                )
            )
            .scalars()
            .all()
        )
        results = [_bukvarix_result_out(row) for row in rows]
    return BukvarixKeywordRunOut(
        id=run.id,
        project_id=run.project_id,
        status=run.status,
        provider_mode=run.provider_mode,
        query_count=run.query_count,
        result_count=run.result_count,
        failure_code=run.failure_code,
        committed_source_run_id=run.committed_source_run_id,
        queued_at=run.queued_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
        results=results,
    )


@router.post(
    "/{project_id}/bukvarix-keyword-runs",
    response_model=BukvarixKeywordRunOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_bukvarix_keyword_run(
    project_id: UUID,
    body: BukvarixKeywordRunCreate,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> BukvarixKeywordRunOut:
    project = await _project_or_404(db, project_id, auth)
    seeds = list(
        (
            await db.execute(
                select(ProjectKeyword, Keyword)
                .join(Keyword, Keyword.id == ProjectKeyword.keyword_id)
                .where(
                    ProjectKeyword.id.in_(body.seed_project_keyword_ids),
                    ProjectKeyword.project_id == project.id,
                    ProjectKeyword.tenant_id == project.tenant_id,
                    Keyword.tenant_id == project.tenant_id,
                )
            )
        ).all()
    )
    if len(seeds) != len(body.seed_project_keyword_ids):
        raise HTTPException(
            status_code=409,
            detail="Select only existing project keywords as Bukvarix seeds",
        )
    snapshot = [
        {"project_keyword_id": str(project_keyword.id), "phrase": keyword.phrase}
        for project_keyword, keyword in sorted(seeds, key=lambda row: str(row[0].id))
    ]
    snapshot_hash = sha256_hex(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    run = ProjectBukvarixKeywordRun(
        tenant_id=project.tenant_id,
        project_id=project.id,
        seed_snapshot=snapshot,
        seed_snapshot_hash=snapshot_hash,
        requested_by=auth.user.id,
    )
    db.add(run)
    await db.flush()
    if hasattr(db, "scalar"):
        await create_bukvarix_keyword_job(db, run=run)
    await append_audit(
        db,
        action="bukvarix.https_public_free.queued",
        payload={
            "project_id": str(project.id),
            "run_id": str(run.id),
            "seed_count": len(snapshot),
            "seed_snapshot_hash": snapshot_hash,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return await _bukvarix_run_out(db, run, include_results=False)


@router.get("/{project_id}/bukvarix-keyword-runs", response_model=list[BukvarixKeywordRunOut])
async def list_bukvarix_keyword_runs(
    project_id: UUID,
    auth: AuthContext = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> list[BukvarixKeywordRunOut]:
    project = await _project_or_404(db, project_id, auth)
    runs = list(
        (
            await db.execute(
                select(ProjectBukvarixKeywordRun)
                .where(
                    ProjectBukvarixKeywordRun.project_id == project.id,
                    ProjectBukvarixKeywordRun.tenant_id == project.tenant_id,
                )
                .order_by(ProjectBukvarixKeywordRun.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    return [await _bukvarix_run_out(db, run) for run in runs]


@router.post(
    "/{project_id}/bukvarix-keyword-runs/{run_id}/commit",
    response_model=BukvarixKeywordRunCommitOut,
)
async def commit_bukvarix_keyword_run(
    project_id: UUID,
    run_id: UUID,
    body: BukvarixKeywordRunCommit,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> BukvarixKeywordRunCommitOut:
    project = await _project_or_404(db, project_id, auth)
    run = (
        await db.execute(
            select(ProjectBukvarixKeywordRun)
            .where(
                ProjectBukvarixKeywordRun.id == run_id,
                ProjectBukvarixKeywordRun.project_id == project.id,
                ProjectBukvarixKeywordRun.tenant_id == project.tenant_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not run or run.status != "completed":
        raise HTTPException(
            status_code=409,
            detail="Select a completed Bukvarix HTTPS public-free run",
        )
    if run.committed_source_run_id:
        return BukvarixKeywordRunCommitOut(
            source_run_id=run.committed_source_run_id,
            created_keywords=0,
            existing_keywords=0,
            linked_project_keywords=0,
        )
    results = list(
        (
            await db.execute(
                select(ProjectBukvarixKeywordResult).where(
                    ProjectBukvarixKeywordResult.id.in_(body.selected_result_ids),
                    ProjectBukvarixKeywordResult.run_id == run.id,
                    ProjectBukvarixKeywordResult.tenant_id == project.tenant_id,
                    ProjectBukvarixKeywordResult.project_id == project.id,
                )
            )
        )
        .scalars()
        .all()
    )
    if len(results) != len(body.selected_result_ids):
        raise HTTPException(status_code=409, detail="Select only results of this Bukvarix run")
    existing_keywords = {
        keyword.normalized: keyword
        for keyword in (
            await db.execute(select(Keyword).where(Keyword.tenant_id == project.tenant_id))
        )
        .scalars()
        .all()
    }
    created_keywords = 0
    for result in results:
        if result.normalized not in existing_keywords:
            keyword = Keyword(
                tenant_id=project.tenant_id,
                phrase=result.phrase,
                normalized=result.normalized,
                meta={"bukvarix_public_metrics": result.metrics or []},
            )
            db.add(keyword)
            existing_keywords[keyword.normalized] = keyword
            created_keywords += 1
    await db.flush()
    existing_links = {
        item.keyword_id: item
        for item in (
            await db.execute(
                select(ProjectKeyword).where(
                    ProjectKeyword.project_id == project.id,
                    ProjectKeyword.tenant_id == project.tenant_id,
                )
            )
        )
        .scalars()
        .all()
    }
    linked: list[ProjectKeyword] = []
    for result in results:
        keyword = existing_keywords[result.normalized]
        link = existing_links.get(keyword.id)
        if not link:
            link = ProjectKeyword(
                project_id=project.id,
                keyword_id=keyword.id,
                tenant_id=project.tenant_id,
            )
            db.add(link)
            existing_links[keyword.id] = link
        linked.append(link)
    await db.flush()
    source_run = ProjectSemanticSourceRun(
        tenant_id=project.tenant_id,
        project_id=project.id,
        provider="bukvarix",
        acquisition="https_public_free",
        mode="domain",
        source_label=f"Bukvarix HTTPS public free · {run.id.hex[:8]}",
        observed_at=run.completed_at or datetime.now(UTC),
        created_by=auth.user.id,
    )
    db.add(source_run)
    await db.flush()
    db.add_all(
        [
            ProjectSemanticSourceRunKeyword(
                source_run_id=source_run.id,
                project_keyword_id=link.id,
                tenant_id=project.tenant_id,
                project_id=project.id,
            )
            for link in {link.id: link for link in linked}.values()
        ]
    )
    run.committed_source_run_id = source_run.id
    await append_audit(
        db,
        action="bukvarix.https_public_free.commit",
        payload={
            "project_id": str(project.id),
            "run_id": str(run.id),
            "source_run_id": str(source_run.id),
            "selected_result_count": len(results),
            "created_keyword_count": created_keywords,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return BukvarixKeywordRunCommitOut(
        source_run_id=source_run.id,
        created_keywords=created_keywords,
        existing_keywords=len(results) - created_keywords,
        linked_project_keywords=len({link.id for link in linked}),
    )


@router.get("/{project_id}/semantic-source-runs", response_model=list[SemanticSourceRunOut])
async def list_semantic_source_runs(
    project_id: UUID,
    auth: AuthContext = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> list[SemanticSourceRunOut]:
    project = await _project_or_404(db, project_id, auth)
    runs = list(
        (
            await db.execute(
                select(ProjectSemanticSourceRun)
                .where(
                    ProjectSemanticSourceRun.project_id == project.id,
                    ProjectSemanticSourceRun.tenant_id == project.tenant_id,
                )
                .order_by(ProjectSemanticSourceRun.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if not runs:
        return []
    counts = dict(
        (
            await db.execute(
                select(
                    ProjectSemanticSourceRunKeyword.source_run_id,
                    func.count(ProjectSemanticSourceRunKeyword.id),
                )
                .where(
                    ProjectSemanticSourceRunKeyword.source_run_id.in_([run.id for run in runs]),
                    ProjectSemanticSourceRunKeyword.project_id == project.id,
                    ProjectSemanticSourceRunKeyword.tenant_id == project.tenant_id,
                )
                .group_by(ProjectSemanticSourceRunKeyword.source_run_id)
            )
        ).all()
    )
    return [_source_run_out(run, counts.get(run.id, 0)) for run in runs]


@router.post(
    "/{project_id}/semantic-source-runs",
    response_model=SemanticSourceRunOut,
    status_code=status.HTTP_201_CREATED,
)
async def record_manual_semantic_source_run(
    project_id: UUID,
    body: SemanticSourceRunCreate,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> SemanticSourceRunOut:
    project = await _project_or_404(db, project_id, auth)
    selected_keywords = list(
        (
            await db.execute(
                select(ProjectKeyword).where(
                    ProjectKeyword.id.in_(body.project_keyword_ids),
                    ProjectKeyword.project_id == project.id,
                    ProjectKeyword.tenant_id == project.tenant_id,
                )
            )
        )
        .scalars()
        .all()
    )
    if len(selected_keywords) != len(body.project_keyword_ids):
        raise HTTPException(
            status_code=409,
            detail="Select only existing keywords of this project for manual provenance",
        )
    run = ProjectSemanticSourceRun(
        tenant_id=project.tenant_id,
        project_id=project.id,
        provider=body.provider,
        acquisition=body.acquisition,
        mode=body.mode,
        source_label=body.source_label,
        observed_at=body.observed_at,
        notes=body.notes.strip() if body.notes else None,
        created_by=auth.user.id,
    )
    db.add(run)
    await db.flush()
    db.add_all(
        [
            ProjectSemanticSourceRunKeyword(
                source_run_id=run.id,
                project_keyword_id=keyword.id,
                tenant_id=project.tenant_id,
                project_id=project.id,
            )
            for keyword in selected_keywords
        ]
    )
    await append_audit(
        db,
        action="semantic_source.run.record_manual_export",
        payload={
            "project_id": str(project.id),
            "source_run_id": str(run.id),
            "provider": body.provider,
            "acquisition": body.acquisition,
            "mode": body.mode,
            "selected_keyword_count": len(selected_keywords),
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _source_run_out(run, len(selected_keywords))
