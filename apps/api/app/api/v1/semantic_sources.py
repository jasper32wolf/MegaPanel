from __future__ import annotations

from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.api.v1.projects import _project_or_404
from app.db.session import get_db
from app.models import (
    ProjectKeyword,
    ProjectSemanticSourceRun,
    ProjectSemanticSourceRunKeyword,
)
from app.schemas.research import (
    BukvarixProviderStatus,
    SemanticSourceRunCreate,
    SemanticSourceRunOut,
)
from app.services.audit import append_audit
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
            "Букварикс отключён: документированный transport использует HTTP и API key "
            "в URL. Добавьте credential-safe HTTPS contract поставщика, прежде чем включать импорт."
        ),
        supported_modes=["domain", "compare", "multi_domain"],
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
