from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.api.v1.projects import _project_or_404
from app.db.session import get_db
from app.models import DesignProfileAssignment, DesignProfileRevision, ProjectFamilyMember
from app.schemas.design import (
    DesignProfileCreate,
    DesignProfileDecision,
    DesignProfileIn,
    DesignProfileOut,
    EffectiveDesignProfileOut,
)
from app.services.audit import append_audit
from app.services.design_profiles import design_profile_hash, resolve_design_profile
from fastapi import APIRouter, Depends, HTTPException, status
from site_panel_blocks import list_kits
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
_READ = require_roles("superadmin", "tenant_admin", "manager", "editor")
_WRITE = require_roles("superadmin", "tenant_admin", "manager", "editor")
_REVIEW = require_roles("superadmin", "tenant_admin", "manager")


def _profile_out(revision: DesignProfileRevision) -> DesignProfileOut:
    return DesignProfileOut(
        id=revision.id,
        project_id=revision.project_id,
        scope=revision.scope,
        name=revision.name,
        version=revision.version,
        state=revision.state,
        profile=DesignProfileIn.model_validate(revision.profile),
        profile_hash=revision.profile_hash,
        supersedes_id=revision.supersedes_id,
        submitted_at=revision.submitted_at,
        reviewed_at=revision.reviewed_at,
        decision_reason=revision.decision_reason,
        created_at=revision.created_at,
    )


async def _revision_or_404(
    db: AsyncSession, *, project_id: UUID, tenant_id: UUID, revision_id: UUID
) -> DesignProfileRevision:
    revision = (
        await db.execute(
            select(DesignProfileRevision).where(
                DesignProfileRevision.id == revision_id,
                DesignProfileRevision.project_id == project_id,
                DesignProfileRevision.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not revision:
        raise HTTPException(status_code=404, detail="Design profile revision not found")
    return revision


def _validate_profile_catalog(profile: DesignProfileIn) -> None:
    catalog = {item["key"]: set(item["blocks"]) for item in list_kits()}
    if any(kit_key not in catalog for kit_key in profile.layout.allowed_kits):
        raise HTTPException(
            status_code=422,
            detail="Design profile selected an unknown curated kit",
        )
    available_blocks = set().union(*(catalog[kit_key] for kit_key in profile.layout.allowed_kits))
    if any(block_id not in available_blocks for block_id in profile.layout.required_blocks):
        raise HTTPException(
            status_code=422,
            detail="Design profile requires an unknown curated block",
        )


async def _require_family_master(db: AsyncSession, *, project_id: UUID, tenant_id: UUID) -> None:
    member = (
        await db.execute(
            select(ProjectFamilyMember.id).where(
                ProjectFamilyMember.child_project_id == project_id,
                ProjectFamilyMember.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if member:
        raise HTTPException(
            status_code=409,
            detail=(
                "Create a project override in a city child; "
                "family profiles belong to its master project"
            ),
        )


@router.get("/{project_id}/design-profiles", response_model=list[DesignProfileOut])
async def list_design_profiles(
    project_id: UUID,
    auth: AuthContext = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> list[DesignProfileOut]:
    project = await _project_or_404(db, project_id, auth)
    revisions = list(
        (
            await db.execute(
                select(DesignProfileRevision)
                .where(
                    DesignProfileRevision.project_id == project.id,
                    DesignProfileRevision.tenant_id == project.tenant_id,
                )
                .order_by(
                    DesignProfileRevision.scope,
                    DesignProfileRevision.version.desc(),
                    DesignProfileRevision.created_at.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    return [_profile_out(revision) for revision in revisions]


@router.get("/{project_id}/design-profile", response_model=EffectiveDesignProfileOut)
async def get_effective_design_profile(
    project_id: UUID,
    auth: AuthContext = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> EffectiveDesignProfileOut:
    project = await _project_or_404(db, project_id, auth)
    resolved = await resolve_design_profile(db, tenant_id=project.tenant_id, project_id=project.id)
    return EffectiveDesignProfileOut(
        project_id=project.id,
        inherited_from_project_id=resolved.inherited_from_project_id,
        family_profile=_profile_out(resolved.family_profile) if resolved.family_profile else None,
        project_profile=(
            _profile_out(resolved.project_profile) if resolved.project_profile else None
        ),
        effective_profile=resolved.effective_profile,
        effective_profile_hash=resolved.effective_profile_hash,
    )


@router.post(
    "/{project_id}/design-profiles",
    response_model=DesignProfileOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_design_profile(
    project_id: UUID,
    body: DesignProfileCreate,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> DesignProfileOut:
    project = await _project_or_404(db, project_id, auth)
    _validate_profile_catalog(body.profile)
    if body.scope == "family":
        await _require_family_master(db, project_id=project.id, tenant_id=project.tenant_id)
    supersedes = None
    if body.supersedes_id:
        supersedes = await _revision_or_404(
            db,
            project_id=project.id,
            tenant_id=project.tenant_id,
            revision_id=body.supersedes_id,
        )
        if supersedes.scope != body.scope:
            raise HTTPException(
                status_code=409,
                detail="Successor profile scope must match its predecessor",
            )
    latest_version = await db.scalar(
        select(func.max(DesignProfileRevision.version)).where(
            DesignProfileRevision.project_id == project.id,
            DesignProfileRevision.tenant_id == project.tenant_id,
            DesignProfileRevision.scope == body.scope,
        )
    )
    profile = body.profile.model_dump(mode="json")
    revision = DesignProfileRevision(
        tenant_id=project.tenant_id,
        project_id=project.id,
        supersedes_id=supersedes.id if supersedes else None,
        scope=body.scope,
        name=body.name.strip(),
        version=int(latest_version or 0) + 1,
        profile=profile,
        profile_hash=design_profile_hash(profile),
        created_by=auth.user.id,
    )
    db.add(revision)
    await db.flush()
    await append_audit(
        db,
        action="design_profile.create",
        payload={
            "project_id": str(project.id),
            "design_profile_revision_id": str(revision.id),
            "scope": revision.scope,
            "version": revision.version,
            "profile_hash": revision.profile_hash,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _profile_out(revision)


@router.post(
    "/{project_id}/design-profiles/{revision_id}/submit-review",
    response_model=DesignProfileOut,
)
async def submit_design_profile(
    project_id: UUID,
    revision_id: UUID,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> DesignProfileOut:
    project = await _project_or_404(db, project_id, auth)
    revision = await _revision_or_404(
        db, project_id=project.id, tenant_id=project.tenant_id, revision_id=revision_id
    )
    if revision.state != "draft":
        raise HTTPException(status_code=409, detail="Only draft design profiles can be submitted")
    revision.state = "review"
    revision.submitted_at = datetime.now(UTC)
    await append_audit(
        db,
        action="design_profile.submit_review",
        payload={"project_id": str(project.id), "design_profile_revision_id": str(revision.id)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _profile_out(revision)


@router.post("/{project_id}/design-profiles/{revision_id}/approve", response_model=DesignProfileOut)
async def approve_design_profile(
    project_id: UUID,
    revision_id: UUID,
    body: DesignProfileDecision,
    auth: AuthContext = Depends(_REVIEW),
    db: AsyncSession = Depends(get_db),
) -> DesignProfileOut:
    project = await _project_or_404(db, project_id, auth)
    revision = await _revision_or_404(
        db, project_id=project.id, tenant_id=project.tenant_id, revision_id=revision_id
    )
    if revision.state != "review":
        raise HTTPException(status_code=409, detail="Only reviewed design profiles can be approved")
    if revision.scope == "family":
        await _require_family_master(db, project_id=project.id, tenant_id=project.tenant_id)
    revision.state = "approved"
    revision.reviewed_at = datetime.now(UTC)
    revision.reviewed_by = auth.user.id
    revision.decision_reason = body.reason.strip() if body.reason else None
    await db.execute(
        delete(DesignProfileAssignment).where(
            DesignProfileAssignment.project_id == project.id,
            DesignProfileAssignment.tenant_id == project.tenant_id,
            DesignProfileAssignment.scope == revision.scope,
        )
    )
    db.add(
        DesignProfileAssignment(
            tenant_id=project.tenant_id,
            project_id=project.id,
            scope=revision.scope,
            profile_revision_id=revision.id,
            assigned_by=auth.user.id,
        )
    )
    await append_audit(
        db,
        action="design_profile.approve",
        payload={
            "project_id": str(project.id),
            "design_profile_revision_id": str(revision.id),
            "scope": revision.scope,
            "profile_hash": revision.profile_hash,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _profile_out(revision)


@router.post("/{project_id}/design-profiles/{revision_id}/reject", response_model=DesignProfileOut)
async def reject_design_profile(
    project_id: UUID,
    revision_id: UUID,
    body: DesignProfileDecision,
    auth: AuthContext = Depends(_REVIEW),
    db: AsyncSession = Depends(get_db),
) -> DesignProfileOut:
    if not body.reason or not body.reason.strip():
        raise HTTPException(status_code=400, detail="Provide a rejection reason")
    project = await _project_or_404(db, project_id, auth)
    revision = await _revision_or_404(
        db, project_id=project.id, tenant_id=project.tenant_id, revision_id=revision_id
    )
    if revision.state != "review":
        raise HTTPException(status_code=409, detail="Only reviewed design profiles can be rejected")
    revision.state = "rejected"
    revision.reviewed_at = datetime.now(UTC)
    revision.reviewed_by = auth.user.id
    revision.decision_reason = body.reason.strip()
    await append_audit(
        db,
        action="design_profile.reject",
        payload={"project_id": str(project.id), "design_profile_revision_id": str(revision.id)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _profile_out(revision)
