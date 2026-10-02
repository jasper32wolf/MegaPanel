from __future__ import annotations

from copy import deepcopy
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.api.v1.domains import normalize_hostname
from app.api.v1.projects import _confirmed_facts, _facts_hash, _project_or_404, _serialize_project
from app.db.session import get_db
from app.models import (
    GeoPlace,
    PagePlan,
    Project,
    ProjectFactRevision,
    ProjectFamilyMember,
    ProjectGeoPlace,
    ProjectKeyword,
    Site,
    SiteStructureRevision,
)
from app.schemas.project_family import (
    CityProjectReadinessOut,
    ProjectCityCloneCreate,
    ProjectFamilyMemberOut,
)
from app.services.audit import append_audit
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
_READ = require_roles("superadmin", "tenant_admin", "manager", "editor")
_WRITE = require_roles("superadmin", "tenant_admin", "manager")


def _member_out(
    member: ProjectFamilyMember, child: Project, draft: ProjectFactRevision | None
) -> ProjectFamilyMemberOut:
    return ProjectFamilyMemberOut(
        id=member.id,
        master_project_id=member.master_project_id,
        child_project_id=member.child_project_id,
        geo_id=member.geo_id,
        hostname=member.hostname,
        source_structure_revision_id=member.source_structure_revision_id,
        child_project=_serialize_project(child),
        draft_fact_revision_id=draft.id if draft else None,
    )


async def _member_rows(
    db: AsyncSession, *, master_project_id: UUID, tenant_id: UUID
) -> list[tuple[ProjectFamilyMember, Project, ProjectFactRevision | None]]:
    rows = (
        await db.execute(
            select(ProjectFamilyMember, Project, ProjectFactRevision)
            .join(Project, Project.id == ProjectFamilyMember.child_project_id)
            .outerjoin(
                ProjectFactRevision,
                and_(
                    ProjectFactRevision.project_id == Project.id,
                    ProjectFactRevision.state == "draft",
                ),
            )
            .where(
                ProjectFamilyMember.master_project_id == master_project_id,
                ProjectFamilyMember.tenant_id == tenant_id,
            )
            .order_by(ProjectFamilyMember.created_at.desc())
        )
    ).all()
    return list(rows)


@router.get("/{project_id}/city-projects", response_model=list[ProjectFamilyMemberOut])
async def list_city_projects(
    project_id: UUID,
    auth: AuthContext = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> list[ProjectFamilyMemberOut]:
    master = await _project_or_404(db, project_id, auth)
    return [
        _member_out(*row)
        for row in await _member_rows(db, master_project_id=master.id, tenant_id=master.tenant_id)
    ]


def _public_fact_diff(master_facts: dict, child_facts: dict) -> dict[str, list[str]]:
    master_keys = set(master_facts)
    child_keys = set(child_facts)
    return {
        "changed": sorted(
            key for key in master_keys & child_keys if master_facts.get(key) != child_facts.get(key)
        ),
        "missing": sorted(key for key in master_keys if not child_facts.get(key)),
        "additional": sorted(key for key in child_keys - master_keys),
    }


def _city_next_action(
    *, facts_state: str | None, keyword_count: int, plan_counts: dict[str, int], site_exists: bool
) -> str:
    if facts_state != "confirmed":
        return "review_city_facts"
    if keyword_count == 0:
        return "select_keywords"
    if plan_counts.get("draft", 0):
        return "submit_plan_for_review"
    if plan_counts.get("review", 0):
        return "approve_plan"
    if plan_counts.get("approved", 0):
        return "generate_draft"
    if not site_exists:
        return "prepare_page_plan"
    return "create_candidate"


@router.get("/{project_id}/city-projects/readiness", response_model=list[CityProjectReadinessOut])
async def list_city_project_readiness(
    project_id: UUID,
    auth: AuthContext = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> list[CityProjectReadinessOut]:
    master = await _project_or_404(db, project_id, auth)
    rows = await _member_rows(db, master_project_id=master.id, tenant_id=master.tenant_id)
    if not rows:
        return []
    child_ids = [child.id for _, child, _ in rows]
    master_facts = (
        await db.execute(
            select(ProjectFactRevision)
            .where(
                ProjectFactRevision.project_id == master.id,
                ProjectFactRevision.tenant_id == master.tenant_id,
                ProjectFactRevision.state == "confirmed",
            )
            .order_by(ProjectFactRevision.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    child_facts = list(
        (
            await db.execute(
                select(ProjectFactRevision)
                .where(
                    ProjectFactRevision.project_id.in_(child_ids),
                    ProjectFactRevision.tenant_id == master.tenant_id,
                )
                .order_by(ProjectFactRevision.project_id, ProjectFactRevision.version.desc())
            )
        )
        .scalars()
        .all()
    )
    latest_facts: dict[UUID, ProjectFactRevision] = {}
    for revision in child_facts:
        latest_facts.setdefault(revision.project_id, revision)
    keyword_counts = dict(
        (
            await db.execute(
                select(ProjectKeyword.project_id, func.count(ProjectKeyword.id))
                .where(
                    ProjectKeyword.project_id.in_(child_ids),
                    ProjectKeyword.tenant_id == master.tenant_id,
                )
                .group_by(ProjectKeyword.project_id)
            )
        ).all()
    )
    plans = list(
        (
            await db.execute(
                select(PagePlan).where(
                    PagePlan.project_id.in_(child_ids),
                    PagePlan.tenant_id == master.tenant_id,
                )
            )
        )
        .scalars()
        .all()
    )
    plan_counts: dict[UUID, dict[str, int]] = {child_id: {} for child_id in child_ids}
    for plan in plans:
        counts = plan_counts[plan.project_id]
        counts[plan.state] = counts.get(plan.state, 0) + 1
    primary_geo = {
        project_id
        for project_id, geo_id in (
            await db.execute(
                select(ProjectGeoPlace.project_id, ProjectGeoPlace.geo_id).where(
                    ProjectGeoPlace.project_id.in_(child_ids),
                    ProjectGeoPlace.tenant_id == master.tenant_id,
                    ProjectGeoPlace.role == "primary",
                )
            )
        ).all()
    }
    site_project_ids = set(
        (
            await db.execute(
                select(Site.project_id).where(
                    Site.project_id.in_(child_ids),
                    Site.tenant_id == master.tenant_id,
                )
            )
        )
        .scalars()
        .all()
    )
    master_public_facts = master_facts.facts if master_facts else {}
    result: list[CityProjectReadinessOut] = []
    for member, child, _draft in rows:
        facts = latest_facts.get(child.id)
        counts = plan_counts[child.id]
        result.append(
            CityProjectReadinessOut(
                project_family_member_id=member.id,
                child_project_id=child.id,
                child_project_name=child.name,
                child_project_slug=child.slug,
                hostname=member.hostname,
                geo_id=member.geo_id,
                source_structure_revision_id=member.source_structure_revision_id,
                facts_state=facts.state if facts else None,
                facts_version=facts.version if facts else None,
                public_fact_diff=_public_fact_diff(
                    master_public_facts,
                    facts.facts if facts else {},
                ),
                private_recipient_configured=bool(facts and facts.private_lead_email_enc),
                keyword_count=keyword_counts.get(child.id, 0),
                primary_geo_ready=child.id in primary_geo,
                page_plans_by_state=counts,
                site_exists=child.id in site_project_ids,
                next_action=_city_next_action(
                    facts_state=facts.state if facts else None,
                    keyword_count=keyword_counts.get(child.id, 0),
                    plan_counts=counts,
                    site_exists=child.id in site_project_ids,
                ),
            )
        )
    return result


@router.post(
    "/{project_id}/city-projects",
    response_model=ProjectFamilyMemberOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_city_project(
    project_id: UUID,
    body: ProjectCityCloneCreate,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> ProjectFamilyMemberOut:
    master = await _project_or_404(db, project_id, auth)
    facts = await _confirmed_facts(db, master)
    city = (
        await db.execute(
            select(GeoPlace).where(
                GeoPlace.id == body.geo_id,
                GeoPlace.kind == "city",
                GeoPlace.is_validated.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not city:
        raise HTTPException(status_code=409, detail="Select a validated city")
    try:
        hostname = normalize_hostname(body.hostname)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    existing = (
        await db.execute(
            select(Project).where(Project.tenant_id == master.tenant_id, Project.slug == body.slug)
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Child project slug already exists")
    if body.source_structure_revision_id:
        structure = await db.get(SiteStructureRevision, body.source_structure_revision_id)
        if (
            not structure
            or structure.project_id != master.id
            or structure.tenant_id != master.tenant_id
            or structure.state != "approved"
        ):
            raise HTTPException(status_code=409, detail="Select an approved master site structure")
    child = Project(
        tenant_id=master.tenant_id,
        name=body.name.strip(),
        slug=body.slug,
        domain=hostname,
        locale=master.locale,
        niche=master.niche,
    )
    db.add(child)
    await db.flush()
    db.add(
        ProjectGeoPlace(
            project_id=child.id,
            geo_id=city.id,
            tenant_id=master.tenant_id,
            role="primary",
            position=0,
            morph_overrides={},
        )
    )
    copied_facts = deepcopy(facts.facts or {})
    draft = ProjectFactRevision(
        project_id=child.id,
        tenant_id=child.tenant_id,
        version=1,
        state="draft",
        facts=copied_facts,
        facts_hash=_facts_hash(copied_facts),
        source_notes=(
            f"Draft copied from master project {master.id}; confirm and replace city-specific "
            "address, phone, contacts, and commercial information."
        ),
        created_by=auth.user.id,
    )
    db.add(draft)
    await db.flush()
    member = ProjectFamilyMember(
        tenant_id=master.tenant_id,
        master_project_id=master.id,
        child_project_id=child.id,
        geo_id=city.id,
        hostname=hostname,
        source_structure_revision_id=body.source_structure_revision_id,
    )
    db.add(member)
    await db.flush()
    await append_audit(
        db,
        action="project_family.city_project.create",
        payload={
            "master_project_id": str(master.id),
            "child_project_id": str(child.id),
            "geo_id": str(city.id),
            "hostname": hostname,
            "draft_fact_revision_id": str(draft.id),
        },
        tenant_id=master.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _member_out(member, child, draft)
