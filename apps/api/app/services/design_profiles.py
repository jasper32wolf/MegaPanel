"""Resolution of reviewed, bounded visual policy for project generation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from app.core.security import sha256_hex
from app.models import DesignProfileAssignment, DesignProfileRevision, ProjectFamilyMember
from app.schemas.design import DesignProfileIn
from site_panel_blocks.schema import ThemeProfile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ResolvedDesignProfile:
    project_id: UUID
    inherited_from_project_id: UUID | None
    family_profile: DesignProfileRevision | None
    project_profile: DesignProfileRevision | None
    effective_profile: DesignProfileIn | None
    effective_profile_hash: str | None


def design_profile_hash(profile: DesignProfileIn | dict) -> str:
    value = profile.model_dump(mode="json") if isinstance(profile, DesignProfileIn) else profile
    return sha256_hex(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def theme_from_profile(profile: DesignProfileIn) -> ThemeProfile:
    tokens = profile.tokens
    return ThemeProfile(
        radius=tokens.radius,
        primary=tokens.primary,
        secondary=tokens.secondary,
        bg=tokens.background,
        surface=tokens.surface,
        text=tokens.text,
        muted=tokens.muted,
        gradient=tokens.gradient,
        density=tokens.density,
        font_pair=tokens.font_pair,
    )


def design_snapshot(resolved: ResolvedDesignProfile) -> dict | None:
    selected = resolved.project_profile or resolved.family_profile
    if not selected or not resolved.effective_profile:
        return None
    return {
        "profile_revision_id": str(selected.id),
        "profile_hash": selected.profile_hash,
        "profile_scope": selected.scope,
        "layout_variant": None,
        "tokens": {
            key: str(value)
            for key, value in resolved.effective_profile.tokens.model_dump(mode="json").items()
        },
        "art_direction": {
            "site_family": resolved.effective_profile.site_family,
            "imagery_guidance": resolved.effective_profile.imagery_guidance,
            "voice_traits": resolved.effective_profile.voice_traits,
            "differentiation_rationale": resolved.effective_profile.differentiation_rationale,
        },
    }


async def _assignment_revision(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    scope: str,
) -> DesignProfileRevision | None:
    return (
        await db.execute(
            select(DesignProfileRevision)
            .join(
                DesignProfileAssignment,
                DesignProfileAssignment.profile_revision_id == DesignProfileRevision.id,
            )
            .where(
                DesignProfileAssignment.tenant_id == tenant_id,
                DesignProfileAssignment.project_id == project_id,
                DesignProfileAssignment.scope == scope,
                DesignProfileRevision.tenant_id == tenant_id,
                DesignProfileRevision.project_id == project_id,
                DesignProfileRevision.scope == scope,
                DesignProfileRevision.state == "approved",
            )
        )
    ).scalar_one_or_none()


async def resolve_design_profile(
    db: AsyncSession, *, tenant_id: UUID, project_id: UUID
) -> ResolvedDesignProfile:
    """Resolve family then project policy; never reads mutable unapproved profile revisions."""
    member = (
        await db.execute(
            select(ProjectFamilyMember).where(
                ProjectFamilyMember.tenant_id == tenant_id,
                ProjectFamilyMember.child_project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    master_project_id = member.master_project_id if member else None
    family_profile = (
        await _assignment_revision(
            db,
            tenant_id=tenant_id,
            project_id=master_project_id,
            scope="family",
        )
        if master_project_id
        else await _assignment_revision(
            db,
            tenant_id=tenant_id,
            project_id=project_id,
            scope="family",
        )
    )
    project_profile = await _assignment_revision(
        db,
        tenant_id=tenant_id,
        project_id=project_id,
        scope="project",
    )
    selected = project_profile or family_profile
    profile = DesignProfileIn.model_validate(selected.profile) if selected else None
    return ResolvedDesignProfile(
        project_id=project_id,
        inherited_from_project_id=master_project_id,
        family_profile=family_profile,
        project_profile=project_profile,
        effective_profile=profile,
        effective_profile_hash=selected.profile_hash if selected else None,
    )


__all__ = [
    "ResolvedDesignProfile",
    "design_profile_hash",
    "design_snapshot",
    "resolve_design_profile",
    "theme_from_profile",
]
