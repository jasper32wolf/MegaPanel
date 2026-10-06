"""Reviewed author profiles for public EEAT information."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.api.v1.media import _asset_availability, _stored_sha256, ensure_media_review_allows_use
from app.api.v1.projects import _project_or_404
from app.db.session import get_db
from app.models import AuthorProfileRevision, MediaAsset
from app.schemas.author import (
    AuthorProfileCreate,
    AuthorProfileDecision,
    AuthorProfileIn,
    AuthorProfileOut,
    author_profile_hash,
)
from app.services.audit import append_audit
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
_READ = require_roles("superadmin", "tenant_admin", "manager", "editor")
_WRITE = require_roles("superadmin", "tenant_admin", "manager", "editor")
_REVIEW = require_roles("superadmin", "tenant_admin", "manager")


def _profile_input(revision: AuthorProfileRevision) -> AuthorProfileIn:
    return AuthorProfileIn(
        slug=revision.slug,
        name=revision.name,
        role=revision.role,
        biography=revision.biography,
        expertise=list(revision.expertise or []),
        evidence=list(revision.evidence or []),
        portrait_asset_id=revision.portrait_asset_id,
    )


def _profile_out(revision: AuthorProfileRevision) -> AuthorProfileOut:
    return AuthorProfileOut(
        id=revision.id,
        project_id=revision.project_id,
        supersedes_id=revision.supersedes_id,
        profile=_profile_input(revision),
        profile_hash=revision.profile_hash,
        version=revision.version,
        state=revision.state,
        submitted_at=revision.submitted_at,
        reviewed_at=revision.reviewed_at,
        decision_reason=revision.decision_reason,
        created_at=revision.created_at,
    )


async def _revision_or_404(
    db: AsyncSession, *, project_id: UUID, tenant_id: UUID, revision_id: UUID
) -> AuthorProfileRevision:
    revision = (
        await db.execute(
            select(AuthorProfileRevision).where(
                AuthorProfileRevision.id == revision_id,
                AuthorProfileRevision.project_id == project_id,
                AuthorProfileRevision.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not revision:
        raise HTTPException(status_code=404, detail="Author profile revision not found")
    return revision


async def _approved_portrait_or_409(
    db: AsyncSession, *, tenant_id: UUID, asset_id: UUID
) -> MediaAsset:
    asset = (
        await db.execute(
            select(MediaAsset).where(MediaAsset.id == asset_id, MediaAsset.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if not asset or _asset_availability(asset) != "eligible":
        raise HTTPException(
            status_code=409,
            detail="Author portrait must be an eligible reviewed MediaAsset",
        )
    try:
        await ensure_media_review_allows_use(
            db,
            tenant_id=tenant_id,
            asset_id=asset.id,
            stored_sha256=_stored_sha256(asset),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return asset


@router.get("/{project_id}/authors", response_model=list[AuthorProfileOut])
async def list_author_profiles(
    project_id: UUID,
    auth: AuthContext = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> list[AuthorProfileOut]:
    project = await _project_or_404(db, project_id, auth)
    revisions = list(
        (
            await db.execute(
                select(AuthorProfileRevision)
                .where(
                    AuthorProfileRevision.project_id == project.id,
                    AuthorProfileRevision.tenant_id == project.tenant_id,
                )
                .order_by(
                    AuthorProfileRevision.slug,
                    AuthorProfileRevision.version.desc(),
                    AuthorProfileRevision.created_at.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    return [_profile_out(revision) for revision in revisions]


@router.post(
    "/{project_id}/authors",
    response_model=AuthorProfileOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_author_profile(
    project_id: UUID,
    body: AuthorProfileCreate,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> AuthorProfileOut:
    project = await _project_or_404(db, project_id, auth)
    await _approved_portrait_or_409(
        db,
        tenant_id=project.tenant_id,
        asset_id=body.profile.portrait_asset_id,
    )
    supersedes = None
    if body.supersedes_id:
        supersedes = await _revision_or_404(
            db,
            project_id=project.id,
            tenant_id=project.tenant_id,
            revision_id=body.supersedes_id,
        )
        if supersedes.slug != body.profile.slug:
            raise HTTPException(
                status_code=409,
                detail="Author successor must keep the same public slug",
            )
    latest = await db.scalar(
        select(func.max(AuthorProfileRevision.version)).where(
            AuthorProfileRevision.project_id == project.id,
            AuthorProfileRevision.tenant_id == project.tenant_id,
            AuthorProfileRevision.slug == body.profile.slug,
        )
    )
    revision = AuthorProfileRevision(
        tenant_id=project.tenant_id,
        project_id=project.id,
        supersedes_id=supersedes.id if supersedes else None,
        portrait_asset_id=body.profile.portrait_asset_id,
        slug=body.profile.slug,
        name=body.profile.name,
        role=body.profile.role,
        biography=body.profile.biography,
        expertise=body.profile.expertise,
        evidence=body.profile.evidence,
        profile_hash=author_profile_hash(body.profile),
        version=int(latest or 0) + 1,
        created_by=auth.user.id,
    )
    db.add(revision)
    await db.flush()
    await append_audit(
        db,
        action="author_profile.create",
        payload={
            "project_id": str(project.id),
            "author_profile_revision_id": str(revision.id),
            "profile_hash": revision.profile_hash,
            "portrait_asset_id": str(revision.portrait_asset_id),
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _profile_out(revision)


@router.post("/{project_id}/authors/{revision_id}/submit-review", response_model=AuthorProfileOut)
async def submit_author_profile(
    project_id: UUID,
    revision_id: UUID,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> AuthorProfileOut:
    project = await _project_or_404(db, project_id, auth)
    revision = await _revision_or_404(
        db,
        project_id=project.id,
        tenant_id=project.tenant_id,
        revision_id=revision_id,
    )
    if revision.state != "draft":
        raise HTTPException(status_code=409, detail="Only draft author profiles can be submitted")
    await _approved_portrait_or_409(
        db,
        tenant_id=project.tenant_id,
        asset_id=revision.portrait_asset_id,
    )
    revision.state = "review"
    revision.submitted_at = datetime.now(UTC)
    await append_audit(
        db,
        action="author_profile.submit_review",
        payload={"project_id": str(project.id), "author_profile_revision_id": str(revision.id)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _profile_out(revision)


@router.post("/{project_id}/authors/{revision_id}/approve", response_model=AuthorProfileOut)
async def approve_author_profile(
    project_id: UUID,
    revision_id: UUID,
    body: AuthorProfileDecision,
    auth: AuthContext = Depends(_REVIEW),
    db: AsyncSession = Depends(get_db),
) -> AuthorProfileOut:
    project = await _project_or_404(db, project_id, auth)
    revision = await _revision_or_404(
        db,
        project_id=project.id,
        tenant_id=project.tenant_id,
        revision_id=revision_id,
    )
    if revision.state != "review":
        raise HTTPException(
            status_code=409,
            detail="Only author profiles under review can be approved",
        )
    await _approved_portrait_or_409(
        db,
        tenant_id=project.tenant_id,
        asset_id=revision.portrait_asset_id,
    )
    revision.state = "approved"
    revision.reviewed_at = datetime.now(UTC)
    revision.reviewed_by = auth.user.id
    revision.decision_reason = body.reason.strip() if body.reason else None
    await append_audit(
        db,
        action="author_profile.approve",
        payload={
            "project_id": str(project.id),
            "author_profile_revision_id": str(revision.id),
            "profile_hash": revision.profile_hash,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _profile_out(revision)


@router.post("/{project_id}/authors/{revision_id}/reject", response_model=AuthorProfileOut)
async def reject_author_profile(
    project_id: UUID,
    revision_id: UUID,
    body: AuthorProfileDecision,
    auth: AuthContext = Depends(_REVIEW),
    db: AsyncSession = Depends(get_db),
) -> AuthorProfileOut:
    if not body.reason or not body.reason.strip():
        raise HTTPException(status_code=400, detail="Provide an author-profile rejection reason")
    project = await _project_or_404(db, project_id, auth)
    revision = await _revision_or_404(
        db,
        project_id=project.id,
        tenant_id=project.tenant_id,
        revision_id=revision_id,
    )
    if revision.state != "review":
        raise HTTPException(
            status_code=409,
            detail="Only author profiles under review can be rejected",
        )
    revision.state = "rejected"
    revision.reviewed_at = datetime.now(UTC)
    revision.reviewed_by = auth.user.id
    revision.decision_reason = body.reason.strip()
    await append_audit(
        db,
        action="author_profile.reject",
        payload={"project_id": str(project.id), "author_profile_revision_id": str(revision.id)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _profile_out(revision)
