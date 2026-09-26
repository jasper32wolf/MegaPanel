from __future__ import annotations

import json
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from app.api.deps import AuthContext, require_roles
from app.api.v1.domains import normalize_hostname
from app.core.config import get_settings
from app.core.security import sha256_hex
from app.db.session import get_db
from app.models import (
    GeoPlace,
    Keyword,
    PageDraft,
    PagePlan,
    Project,
    ProjectFactRevision,
    ProjectGeoPlace,
    ProjectKeyword,
    Site,
    SiteBuild,
    SitePage,
)
from app.schemas.workflow import (
    PROTECTED_CONTACT_FIELDS,
    BuildPublishRequest,
    BuildRollbackRequest,
    FactRevisionCreate,
    PageDraftDecision,
    PageDraftRequest,
    PagePlanCreate,
    PagePlanDecision,
    PagePlanUpdate,
    ProjectCreate,
    ProjectGeoUpdate,
    ProjectKeywordsUpdate,
    ProjectUpdate,
    QaOverrideIn,
)
from app.services.audit import append_audit
from app.services.block_library import instantiate_kit_for_site
from app.services.caddy_client import CaddyClient
from app.services.domain_health import domain_probe
from app.services.generation import create_page_draft
from app.services.indexnow import new_indexnow_key
from app.services.qa import run_page_qa
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from site_panel_blocks import list_kits
from site_panel_shared.manifests import PageManifest, SiteManifest
from site_panel_ssg import SiteBuilder
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
settings = get_settings()


def _require_project_access(auth: AuthContext, project: Project) -> None:
    if auth.role != "superadmin" and auth.tenant_id != project.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")


async def _project_or_404(db: AsyncSession, project_id: UUID, auth: AuthContext) -> Project:
    project = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    _require_project_access(auth, project)
    return project


def _serialize_project(project: Project) -> dict:
    return {
        "id": str(project.id),
        "name": project.name,
        "slug": project.slug,
        "domain": project.domain,
        "locale": project.locale,
        "niche": project.niche,
        "status": project.status,
        "version": project.version,
        "site_id": str(project.site_id) if project.site_id else None,
        "current_fact_revision_id": str(project.current_fact_revision_id)
        if project.current_fact_revision_id
        else None,
        "domain_check_meta": project.domain_check_meta or {},
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }


def _facts_hash(facts: dict) -> str:
    return sha256_hex(json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _public_fact_values(facts: dict) -> dict:
    values = dict(facts or {})
    contacts = values.get("contacts")
    if isinstance(contacts, dict):
        values["contacts"] = {
            key: value for key, value in contacts.items() if key not in PROTECTED_CONTACT_FIELDS
        }
    return values


def _serialize_fact(revision: ProjectFactRevision) -> dict:
    return {
        "id": str(revision.id),
        "version": revision.version,
        "state": revision.state,
        "facts": _public_fact_values(revision.facts or {}),
        "source_notes": revision.source_notes,
        "facts_hash": revision.facts_hash,
        "supersedes_id": str(revision.supersedes_id) if revision.supersedes_id else None,
        "confirmed_at": revision.confirmed_at.isoformat() if revision.confirmed_at else None,
        "created_at": revision.created_at.isoformat() if revision.created_at else None,
    }


def _serialize_plan(plan: PagePlan) -> dict:
    return {
        "id": str(plan.id),
        "project_id": str(plan.project_id),
        "slug": plan.slug,
        "objective": plan.objective,
        "intent": plan.intent,
        "risk_notes": plan.risk_notes,
        "kit_key": plan.kit_key,
        "block_selection": plan.block_selection or {},
        "fact_revision_id": str(plan.fact_revision_id) if plan.fact_revision_id else None,
        "keyword_snapshot": plan.keyword_snapshot or {},
        "geo_snapshot": plan.geo_snapshot or {},
        "source_refs": plan.source_refs or {},
        "state": plan.state,
        "version": plan.version,
        "decision_reason": plan.decision_reason,
        "submitted_at": plan.submitted_at.isoformat() if plan.submitted_at else None,
        "reviewed_at": plan.reviewed_at.isoformat() if plan.reviewed_at else None,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
    }


async def _confirmed_facts(db: AsyncSession, project: Project) -> ProjectFactRevision:
    if not project.current_fact_revision_id:
        raise HTTPException(status_code=409, detail={"blockers": ["Confirm business facts first"]})
    revision = (
        await db.execute(
            select(ProjectFactRevision).where(
                ProjectFactRevision.id == project.current_fact_revision_id,
                ProjectFactRevision.project_id == project.id,
                ProjectFactRevision.state == "confirmed",
            )
        )
    ).scalar_one_or_none()
    if not revision:
        raise HTTPException(status_code=409, detail={"blockers": ["Confirm business facts first"]})
    return revision


async def _selection_snapshots(db: AsyncSession, project: Project) -> tuple[dict, dict, list[str]]:
    keywords = list(
        (
            await db.execute(
                select(ProjectKeyword, Keyword)
                .join(Keyword, Keyword.id == ProjectKeyword.keyword_id)
                .where(ProjectKeyword.project_id == project.id)
                .order_by(ProjectKeyword.priority.desc().nullslast(), Keyword.phrase)
            )
        ).all()
    )
    places = list(
        (
            await db.execute(
                select(ProjectGeoPlace, GeoPlace)
                .join(GeoPlace, GeoPlace.id == ProjectGeoPlace.geo_id)
                .where(ProjectGeoPlace.project_id == project.id)
                .order_by(ProjectGeoPlace.position, GeoPlace.name)
            )
        ).all()
    )
    blockers: list[str] = []
    if not keywords:
        blockers.append("Select at least one project keyword")
    if not any(binding.role == "primary" for binding, _ in places):
        blockers.append("Select one primary geographic place")
    keyword_snapshot = {
        "items": [
            {
                "keyword_id": str(keyword.id),
                "phrase": keyword.phrase,
                "cluster": binding.cluster,
                "intent": binding.intent or (keyword.meta or {}).get("intent"),
                "priority": binding.priority,
            }
            for binding, keyword in keywords
        ]
    }
    geo_snapshot = {
        "items": [
            {
                "geo_id": str(place.id),
                "name": place.name,
                "kind": place.kind,
                "forms": {**(place.name_forms or {}), **(binding.morph_overrides or {})},
                "role": binding.role,
                "position": binding.position,
            }
            for binding, place in places
        ]
    }
    return keyword_snapshot, geo_snapshot, blockers


@router.get("")
async def list_projects(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    statement = (
        select(Project).where(Project.archived_at.is_(None)).order_by(Project.updated_at.desc())
    )
    if auth.role != "superadmin":
        statement = statement.where(Project.tenant_id == auth.tenant_id)
    return [
        _serialize_project(project) for project in (await db.execute(statement)).scalars().all()
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    existing = (
        await db.execute(
            select(Project).where(Project.tenant_id == auth.tenant_id, Project.slug == body.slug)
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Project slug already exists")
    try:
        domain = normalize_hostname(body.domain) if body.domain else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    project = Project(
        tenant_id=auth.tenant_id,
        name=body.name.strip(),
        slug=body.slug,
        domain=domain,
        locale=body.locale,
        niche=body.niche.strip() if body.niche else None,
    )
    db.add(project)
    await db.flush()
    await append_audit(
        db,
        action="project.create",
        payload={"project_id": str(project.id), "slug": project.slug},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(project)
    return _serialize_project(project)


@router.get("/{project_id}")
async def get_project(
    project_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return _serialize_project(await _project_or_404(db, project_id, auth))


@router.patch("/{project_id}")
async def update_project(
    project_id: UUID,
    body: ProjectUpdate,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    current_version = project.version
    if body.version != current_version:
        raise HTTPException(status_code=409, detail={"blockers": ["Project changed; reload it"]})
    changed: list[str] = []
    if body.domain is not None:
        try:
            domain = normalize_hostname(body.domain)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if project.site_id and domain != project.domain:
            raise HTTPException(
                status_code=409,
                detail={"blockers": ["Change an existing site domain through the domain workflow"]},
            )
        if project.domain != domain:
            project.domain = domain
            changed.append("domain")
    for field in ("name", "niche", "status"):
        value = getattr(body, field)
        if value is not None and getattr(project, field) != value:
            setattr(project, field, value.strip() if isinstance(value, str) else value)
            changed.append(field)
    project.version += 1
    await append_audit(
        db,
        action="project.update",
        payload={"project_id": str(project.id), "fields": changed, "version": current_version + 1},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(project)
    return _serialize_project(project)


@router.post("/{project_id}/domain/check")
async def check_project_domain(
    project_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    if not project.domain:
        raise HTTPException(status_code=409, detail={"blockers": ["Set a project domain first"]})
    probe = domain_probe(project.domain)
    project.domain_check_meta = {
        **probe,
        "checked_at": datetime.now(UTC).isoformat(),
    }
    await append_audit(
        db,
        action="project.domain.check",
        payload={
            "project_id": str(project.id),
            "domain": project.domain,
            "dns_status": probe["dns_status"],
            "ssl_status": probe["ssl_status"],
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return project.domain_check_meta


@router.get("/{project_id}/facts")
async def list_fact_revisions(
    project_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    project = await _project_or_404(db, project_id, auth)
    revisions = (
        (
            await db.execute(
                select(ProjectFactRevision)
                .where(ProjectFactRevision.project_id == project.id)
                .order_by(ProjectFactRevision.version.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_serialize_fact(revision) for revision in revisions]


@router.post("/{project_id}/facts", status_code=status.HTTP_201_CREATED)
async def create_fact_revision(
    project_id: UUID,
    body: FactRevisionCreate,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    previous = (
        await db.execute(
            select(ProjectFactRevision)
            .where(ProjectFactRevision.project_id == project.id)
            .order_by(ProjectFactRevision.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    revision = ProjectFactRevision(
        project_id=project.id,
        tenant_id=project.tenant_id,
        supersedes_id=previous.id if previous else None,
        version=(previous.version if previous else 0) + 1,
        facts=body.facts,
        source_notes=body.source_notes.strip() if body.source_notes else None,
        facts_hash=_facts_hash(body.facts),
        created_by=auth.user.id,
    )
    db.add(revision)
    await db.flush()
    await append_audit(
        db,
        action="project.facts.create",
        payload={
            "project_id": str(project.id),
            "revision_id": str(revision.id),
            "version": revision.version,
            "facts_hash": revision.facts_hash,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(revision)
    return _serialize_fact(revision)


@router.post("/{project_id}/facts/{revision_id}/confirm")
async def confirm_fact_revision(
    project_id: UUID,
    revision_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    revision = (
        await db.execute(
            select(ProjectFactRevision).where(
                ProjectFactRevision.id == revision_id,
                ProjectFactRevision.project_id == project.id,
            )
        )
    ).scalar_one_or_none()
    if not revision:
        raise HTTPException(status_code=404, detail="Fact revision not found")
    if revision.state != "draft":
        raise HTTPException(status_code=409, detail="Fact revision is already confirmed")
    revision.state = "confirmed"
    revision.confirmed_by = auth.user.id
    revision.confirmed_at = datetime.now(UTC)
    project.current_fact_revision_id = revision.id
    project.status = "active"
    await append_audit(
        db,
        action="project.facts.confirm",
        payload={
            "project_id": str(project.id),
            "revision_id": str(revision.id),
            "version": revision.version,
            "facts_hash": revision.facts_hash,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_fact(revision)


@router.post("/{project_id}/facts/{revision_id}/restore", status_code=status.HTTP_201_CREATED)
async def restore_fact_revision(
    project_id: UUID,
    revision_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    source = (
        await db.execute(
            select(ProjectFactRevision).where(
                ProjectFactRevision.id == revision_id,
                ProjectFactRevision.project_id == project.id,
            )
        )
    ).scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Fact revision not found")
    latest = (
        await db.execute(
            select(ProjectFactRevision)
            .where(ProjectFactRevision.project_id == project.id)
            .order_by(ProjectFactRevision.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    revision = ProjectFactRevision(
        project_id=project.id,
        tenant_id=project.tenant_id,
        supersedes_id=source.id,
        version=(latest.version if latest else 0) + 1,
        facts=dict(source.facts or {}),
        source_notes=source.source_notes,
        facts_hash=source.facts_hash,
        created_by=auth.user.id,
    )
    db.add(revision)
    await db.flush()
    await append_audit(
        db,
        action="project.facts.restore",
        payload={
            "project_id": str(project.id),
            "source_revision_id": str(source.id),
            "revision_id": str(revision.id),
            "version": revision.version,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_fact(revision)


@router.get("/{project_id}/keywords")
async def list_project_keywords(
    project_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    project = await _project_or_404(db, project_id, auth)
    rows = (
        await db.execute(
            select(ProjectKeyword, Keyword)
            .join(Keyword, Keyword.id == ProjectKeyword.keyword_id)
            .where(ProjectKeyword.project_id == project.id)
            .order_by(ProjectKeyword.priority.desc().nullslast(), Keyword.phrase)
        )
    ).all()
    return [
        {
            "keyword_id": str(keyword.id),
            "phrase": keyword.phrase,
            "cluster": binding.cluster,
            "intent": binding.intent or (keyword.meta or {}).get("intent"),
            "priority": binding.priority,
            "notes": binding.notes,
        }
        for binding, keyword in rows
    ]


@router.put("/{project_id}/keywords")
async def replace_project_keywords(
    project_id: UUID,
    body: ProjectKeywordsUpdate,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    project = await _project_or_404(db, project_id, auth)
    requested = {item.keyword_id for item in body.items}
    if requested:
        keywords = list(
            (await db.execute(select(Keyword).where(Keyword.id.in_(requested)))).scalars().all()
        )
        if len(keywords) != len(requested) or any(
            keyword.tenant_id != project.tenant_id for keyword in keywords
        ):
            raise HTTPException(
                status_code=400, detail="Keywords must belong to this project owner"
            )
    await db.execute(delete(ProjectKeyword).where(ProjectKeyword.project_id == project.id))
    db.add_all(
        [
            ProjectKeyword(
                project_id=project.id,
                tenant_id=project.tenant_id,
                keyword_id=item.keyword_id,
                cluster=item.cluster.strip() if item.cluster else None,
                intent=item.intent.strip() if item.intent else None,
                priority=item.priority,
                notes=item.notes.strip() if item.notes else None,
            )
            for item in body.items
        ]
    )
    await append_audit(
        db,
        action="project.keywords.replace",
        payload={"project_id": str(project.id), "count": len(body.items)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return await list_project_keywords(project_id, auth, db)


@router.get("/{project_id}/geo")
async def list_project_geo(
    project_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    project = await _project_or_404(db, project_id, auth)
    rows = (
        await db.execute(
            select(ProjectGeoPlace, GeoPlace)
            .join(GeoPlace, GeoPlace.id == ProjectGeoPlace.geo_id)
            .where(ProjectGeoPlace.project_id == project.id)
            .order_by(ProjectGeoPlace.position, GeoPlace.name)
        )
    ).all()
    return [
        {
            "geo_id": str(place.id),
            "name": place.name,
            "kind": place.kind,
            "validated": place.is_validated,
            "role": binding.role,
            "position": binding.position,
            "forms": {**(place.name_forms or {}), **(binding.morph_overrides or {})},
        }
        for binding, place in rows
    ]


@router.put("/{project_id}/geo")
async def replace_project_geo(
    project_id: UUID,
    body: ProjectGeoUpdate,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    project = await _project_or_404(db, project_id, auth)
    requested = {item.geo_id for item in body.items}
    if requested:
        places = list(
            (await db.execute(select(GeoPlace).where(GeoPlace.id.in_(requested)))).scalars().all()
        )
        if len(places) != len(requested) or any(not place.is_validated for place in places):
            raise HTTPException(
                status_code=400, detail="Use validated places from the local reference"
            )
    await db.execute(delete(ProjectGeoPlace).where(ProjectGeoPlace.project_id == project.id))
    db.add_all(
        [
            ProjectGeoPlace(
                project_id=project.id,
                tenant_id=project.tenant_id,
                geo_id=item.geo_id,
                role=item.role,
                position=item.position,
                morph_overrides=item.morph_overrides,
            )
            for item in body.items
        ]
    )
    await append_audit(
        db,
        action="project.geo.replace",
        payload={"project_id": str(project.id), "count": len(body.items)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return await list_project_geo(project_id, auth, db)


@router.get("/{project_id}/page-plans")
async def list_page_plans(
    project_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    project = await _project_or_404(db, project_id, auth)
    plans = (
        (
            await db.execute(
                select(PagePlan)
                .where(PagePlan.project_id == project.id)
                .order_by(PagePlan.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [_serialize_plan(plan) for plan in plans]


@router.post("/{project_id}/page-plans", status_code=status.HTTP_201_CREATED)
async def create_page_plan(
    project_id: UUID,
    body: PagePlanCreate,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    if body.kit_key not in {kit["key"] for kit in list_kits()}:
        raise HTTPException(status_code=400, detail="Unknown curated kit")
    existing = (
        await db.execute(
            select(PagePlan)
            .where(PagePlan.project_id == project.id, PagePlan.slug == body.slug)
            .order_by(PagePlan.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing and existing.state in {"draft", "review"}:
        raise HTTPException(status_code=409, detail="Finish or reject the current page plan first")
    plan = PagePlan(
        project_id=project.id,
        tenant_id=project.tenant_id,
        supersedes_id=existing.id if existing else None,
        version=(existing.version + 1) if existing else 1,
        slug=body.slug,
        objective=body.objective.strip(),
        intent=body.intent.strip() if body.intent else None,
        risk_notes=body.risk_notes.strip() if body.risk_notes else None,
        kit_key=body.kit_key,
        block_selection=body.block_selection,
        source_refs=body.source_refs,
    )
    db.add(plan)
    await db.flush()
    await append_audit(
        db,
        action="page_plan.create",
        payload={"project_id": str(project.id), "page_plan_id": str(plan.id), "slug": plan.slug},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_plan(plan)


@router.patch("/{project_id}/page-plans/{plan_id}")
async def update_page_plan(
    project_id: UUID,
    plan_id: UUID,
    body: PagePlanUpdate,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    plan = (
        await db.execute(
            select(PagePlan).where(PagePlan.id == plan_id, PagePlan.project_id == project.id)
        )
    ).scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Page plan not found")
    if plan.state != "draft":
        raise HTTPException(
            status_code=409, detail={"blockers": ["Create a successor draft to change this plan"]}
        )
    if body.version != plan.version:
        raise HTTPException(status_code=409, detail={"blockers": ["Page plan changed; reload it"]})
    changed: list[str] = []
    for field in ("objective", "intent", "risk_notes", "kit_key", "block_selection", "source_refs"):
        value = getattr(body, field)
        if value is not None and getattr(plan, field) != value:
            setattr(plan, field, value)
            changed.append(field)
    plan.version += 1
    await append_audit(
        db,
        action="page_plan.update",
        payload={"page_plan_id": str(plan.id), "fields": changed, "version": plan.version},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_plan(plan)


@router.post("/{project_id}/page-plans/{plan_id}/submit-review")
async def submit_page_plan_review(
    project_id: UUID,
    plan_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    plan = (
        await db.execute(
            select(PagePlan).where(PagePlan.id == plan_id, PagePlan.project_id == project.id)
        )
    ).scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Page plan not found")
    if plan.state != "draft":
        raise HTTPException(status_code=409, detail="Only draft plans can be submitted")
    facts = await _confirmed_facts(db, project)
    keywords, geo, blockers = await _selection_snapshots(db, project)
    if blockers:
        raise HTTPException(status_code=409, detail={"blockers": blockers})
    plan.fact_revision_id = facts.id
    plan.keyword_snapshot = keywords
    plan.geo_snapshot = geo
    plan.state = "review"
    plan.submitted_at = datetime.now(UTC)
    plan.version += 1
    await append_audit(
        db,
        action="page_plan.submit_review",
        payload={
            "page_plan_id": str(plan.id),
            "fact_revision_id": str(facts.id),
            "version": plan.version,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_plan(plan)


@router.post("/{project_id}/page-plans/{plan_id}/approve")
async def approve_page_plan(
    project_id: UUID,
    plan_id: UUID,
    body: PagePlanDecision,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    plan = (
        await db.execute(
            select(PagePlan).where(PagePlan.id == plan_id, PagePlan.project_id == project.id)
        )
    ).scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Page plan not found")
    if plan.state != "review":
        raise HTTPException(status_code=409, detail="Only plans under review can be approved")
    plan.state = "approved"
    plan.reviewed_at = datetime.now(UTC)
    plan.reviewed_by = auth.user.id
    plan.decision_reason = body.reason.strip() if body.reason else None
    await append_audit(
        db,
        action="page_plan.approve",
        payload={"page_plan_id": str(plan.id), "fact_revision_id": str(plan.fact_revision_id)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_plan(plan)


@router.post("/{project_id}/page-plans/{plan_id}/reject")
async def reject_page_plan(
    project_id: UUID,
    plan_id: UUID,
    body: PagePlanDecision,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not body.reason or not body.reason.strip():
        raise HTTPException(status_code=400, detail="Provide a rejection reason")
    project = await _project_or_404(db, project_id, auth)
    plan = (
        await db.execute(
            select(PagePlan).where(PagePlan.id == plan_id, PagePlan.project_id == project.id)
        )
    ).scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Page plan not found")
    if plan.state != "review":
        raise HTTPException(status_code=409, detail="Only plans under review can be rejected")
    plan.state = "rejected"
    plan.reviewed_at = datetime.now(UTC)
    plan.reviewed_by = auth.user.id
    plan.decision_reason = body.reason.strip()
    await append_audit(
        db,
        action="page_plan.reject",
        payload={"page_plan_id": str(plan.id)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_plan(plan)


@router.get("/{project_id}/coverage")
async def project_coverage(
    project_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    selected = list(
        (
            await db.execute(
                select(ProjectKeyword, Keyword)
                .join(Keyword, Keyword.id == ProjectKeyword.keyword_id)
                .where(ProjectKeyword.project_id == project.id)
            )
        ).all()
    )
    plans = list(
        (await db.execute(select(PagePlan).where(PagePlan.project_id == project.id)))
        .scalars()
        .all()
    )
    covered = {
        item.get("keyword_id")
        for plan in plans
        for item in (plan.keyword_snapshot or {}).get("items", [])
        if item.get("keyword_id")
    }
    return {
        "selected": len(selected),
        "covered": sum(str(keyword.id) in covered for _, keyword in selected),
        "uncovered": [
            {"keyword_id": str(keyword.id), "phrase": keyword.phrase}
            for _, keyword in selected
            if str(keyword.id) not in covered
        ],
        "plans": len(plans),
    }


async def _plan_or_404(db: AsyncSession, project: Project, plan_id: UUID) -> PagePlan:
    plan = (
        await db.execute(
            select(PagePlan).where(PagePlan.id == plan_id, PagePlan.project_id == project.id)
        )
    ).scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Page plan not found")
    return plan


def _serialize_draft(draft: PageDraft) -> dict:
    return {
        "id": str(draft.id),
        "page_plan_id": str(draft.page_plan_id),
        "revision": draft.revision,
        "state": draft.state,
        "content_hash": draft.content_hash,
        "page_manifest": draft.page_manifest or {},
        "generator_meta": draft.generator_meta or {},
        "qa_runs": draft.qa_runs or [],
        "last_qa_verdict": draft.last_qa_verdict,
        "qa_override": draft.qa_override or {},
        "failure_message": draft.failure_message,
        "created_at": draft.created_at.isoformat() if draft.created_at else None,
        "updated_at": draft.updated_at.isoformat() if draft.updated_at else None,
    }


@router.get("/{project_id}/page-drafts")
async def list_page_drafts(
    project_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    project = await _project_or_404(db, project_id, auth)
    drafts = (
        (
            await db.execute(
                select(PageDraft)
                .where(PageDraft.project_id == project.id)
                .order_by(PageDraft.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_serialize_draft(draft) for draft in drafts]


@router.post("/{project_id}/page-plans/{plan_id}/drafts", status_code=status.HTTP_201_CREATED)
async def generate_page_draft(
    project_id: UUID,
    plan_id: UUID,
    body: PageDraftRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    del body
    project = await _project_or_404(db, project_id, auth)
    plan = await _plan_or_404(db, project, plan_id)
    if plan.state != "approved":
        raise HTTPException(status_code=409, detail={"blockers": ["Approve the page plan first"]})
    facts = await _confirmed_facts(db, project)
    latest = (
        await db.execute(
            select(PageDraft)
            .where(PageDraft.page_plan_id == plan.id)
            .order_by(PageDraft.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    page_manifest, input_snapshot, candidate_text = create_page_draft(
        project=project,
        plan=plan,
        facts=facts,
    )
    content_hash = sha256_hex(candidate_text)
    draft = PageDraft(
        page_plan_id=plan.id,
        project_id=project.id,
        tenant_id=project.tenant_id,
        revision=(latest.revision if latest else 0) + 1,
        state="draft",
        input_snapshot=input_snapshot,
        page_manifest=page_manifest,
        generator_meta=input_snapshot["generator_meta"],
        content_hash=content_hash,
        requested_by=auth.user.id,
    )
    db.add(draft)
    await db.flush()
    await append_audit(
        db,
        action="page_draft.generate",
        payload={
            "project_id": str(project.id),
            "page_plan_id": str(plan.id),
            "page_draft_id": str(draft.id),
            "revision": draft.revision,
            "content_hash": content_hash,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_draft(draft)


@router.get("/{project_id}/page-drafts/{draft_id}")
async def get_page_draft(
    project_id: UUID,
    draft_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    draft = (
        await db.execute(
            select(PageDraft).where(PageDraft.id == draft_id, PageDraft.project_id == project.id)
        )
    ).scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="Page draft not found")
    return _serialize_draft(draft)


@router.post("/{project_id}/page-drafts/{draft_id}/qa")
async def run_draft_qa(
    project_id: UUID,
    draft_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    draft = (
        await db.execute(
            select(PageDraft).where(PageDraft.id == draft_id, PageDraft.project_id == project.id)
        )
    ).scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="Page draft not found")
    if draft.state not in {"draft", "review"}:
        raise HTTPException(status_code=409, detail="Only a candidate draft can be checked")
    other_drafts = (
        (
            await db.execute(
                select(PageDraft).where(
                    PageDraft.project_id == project.id,
                    PageDraft.id != draft.id,
                    PageDraft.content_hash.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    existing_texts = [
        " ".join(
            [
                str((item.page_manifest or {}).get("title_template") or ""),
                str((item.page_manifest or {}).get("h1_template") or ""),
                str((item.page_manifest or {}).get("unique_core") or ""),
            ]
        )
        for item in other_drafts
    ]
    result = run_page_qa(
        page_manifest=draft.page_manifest or {},
        input_snapshot=draft.input_snapshot or {},
        existing_texts=existing_texts,
    )
    qa_run = {
        "source_hash": draft.content_hash,
        "verdict": result["verdict"],
        "findings": result["findings"],
        "created_at": datetime.now(UTC).isoformat(),
    }
    draft.qa_runs = [*(draft.qa_runs or []), qa_run]
    draft.last_qa_verdict = result["verdict"]
    await append_audit(
        db,
        action="page_draft.qa",
        payload={
            "page_draft_id": str(draft.id),
            "content_hash": draft.content_hash,
            "verdict": result["verdict"],
            "finding_count": len(result["findings"]),
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_draft(draft)


@router.post("/{project_id}/page-drafts/{draft_id}/submit-review")
async def submit_draft_review(
    project_id: UUID,
    draft_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    draft = (
        await db.execute(
            select(PageDraft).where(PageDraft.id == draft_id, PageDraft.project_id == project.id)
        )
    ).scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="Page draft not found")
    if draft.state != "draft" or not draft.last_qa_verdict:
        raise HTTPException(status_code=409, detail={"blockers": ["Run QA before review"]})
    draft.state = "review"
    await append_audit(
        db,
        action="page_draft.submit_review",
        payload={"page_draft_id": str(draft.id), "verdict": draft.last_qa_verdict},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_draft(draft)


@router.post("/{project_id}/page-drafts/{draft_id}/apply")
async def apply_page_draft(
    project_id: UUID,
    draft_id: UUID,
    override: QaOverrideIn | None = None,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    draft = (
        await db.execute(
            select(PageDraft).where(PageDraft.id == draft_id, PageDraft.project_id == project.id)
        )
    ).scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="Page draft not found")
    if draft.state != "review":
        raise HTTPException(
            status_code=409, detail={"blockers": ["Submit the draft for review first"]}
        )
    if draft.last_qa_verdict == "block":
        raise HTTPException(status_code=409, detail={"blockers": ["Resolve blocking QA findings"]})
    if draft.last_qa_verdict == "warn" and override is None:
        raise HTTPException(
            status_code=409, detail={"blockers": ["Provide an audited warning override"]}
        )
    if draft.last_qa_verdict not in {"pass", "warn"}:
        raise HTTPException(status_code=409, detail={"blockers": ["Run QA before apply"]})
    plan = await _plan_or_404(db, project, draft.page_plan_id)
    if plan.state != "approved" or not plan.fact_revision_id:
        raise HTTPException(status_code=409, detail={"blockers": ["Approve the page plan first"]})
    facts = (
        await db.execute(
            select(ProjectFactRevision).where(
                ProjectFactRevision.id == plan.fact_revision_id,
                ProjectFactRevision.project_id == project.id,
                ProjectFactRevision.state == "confirmed",
            )
        )
    ).scalar_one_or_none()
    if not facts:
        raise HTTPException(
            status_code=409, detail={"blockers": ["Plan fact revision is unavailable"]}
        )
    fact_values = _public_fact_values(facts.facts or {})
    contacts = dict(fact_values.get("contacts") or {})
    if not project.domain:
        raise HTTPException(
            status_code=409, detail={"blockers": ["Set a project domain before apply"]}
        )
    primary = next(
        (
            item
            for item in (plan.geo_snapshot or {}).get("items", [])
            if item.get("role") == "primary"
        ),
        {},
    )
    forms = primary.get("forms") or {}
    context = {
        "domain": project.domain,
        "service": fact_values.get("service") or (fact_values.get("services") or ["Услуги"])[0],
        "phone": contacts.get("phone", ""),
        "city_nom": forms.get("nom") or primary.get("name", ""),
        "city_gen": forms.get("gen") or primary.get("name", ""),
        "city_prep": forms.get("prep") or primary.get("name", ""),
        "city_dat": forms.get("dat") or primary.get("name", ""),
    }
    page = PageManifest.model_validate(draft.page_manifest)
    if project.site_id:
        site = (await db.execute(select(Site).where(Site.id == project.site_id))).scalar_one()
        manifest = SiteManifest.model_validate(site.manifest)
        pages = [existing for existing in manifest.pages if existing.slug != page.slug]
        manifest.pages = [*pages, page]
        protected_contacts = {
            key: value
            for key, value in (manifest.contacts or {}).items()
            if key in PROTECTED_CONTACT_FIELDS
        }
        manifest.contacts = {**contacts, **protected_contacts}
        manifest.legal = dict(fact_values.get("legal") or {})
        manifest.context = {**(manifest.context or {}), **context}
        manifest.version += 1
        site.manifest = manifest.model_dump(mode="json")
        site.version += 1
    else:
        site_id = uuid4()
        _blocks, css_vars, _meta = instantiate_kit_for_site(
            plan.kit_key,
            site_id,
            service=page.service,
        )
        manifest = SiteManifest(
            site_id=site_id,
            tenant_id=project.tenant_id,
            domain=project.domain,
            locale=project.locale,
            css_vars=css_vars,
            pages=[page],
            contacts=contacts,
            legal=dict(fact_values.get("legal") or {}),
            context=context,
        )
        site = Site(
            id=site_id,
            tenant_id=project.tenant_id,
            project_id=project.id,
            domain=project.domain,
            locale=project.locale,
            niche=project.niche,
            manifest=manifest.model_dump(mode="json"),
            lead_token=secrets.token_urlsafe(32),
            indexnow_key=new_indexnow_key(),
        )
        db.add(site)
        project.site_id = site.id
    draft.state = "applied"
    draft.applied_by = auth.user.id
    if override:
        draft.qa_override = {
            "reason": override.reason,
            "justification": override.justification,
            "actor_id": str(auth.user.id),
            "created_at": datetime.now(UTC).isoformat(),
        }
    await append_audit(
        db,
        action="page_draft.apply",
        payload={
            "page_draft_id": str(draft.id),
            "page_plan_id": str(plan.id),
            "site_id": str(site.id),
            "warning_override": bool(override),
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {
        "page_draft_id": str(draft.id),
        "site_id": str(site.id),
        "manifest_version": SiteManifest.model_validate(site.manifest).version,
        "published": False,
    }


@router.post("/{project_id}/page-drafts/{draft_id}/reject")
async def reject_page_draft(
    project_id: UUID,
    draft_id: UUID,
    body: PageDraftDecision,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not body.reason or not body.reason.strip():
        raise HTTPException(status_code=400, detail="Provide a rejection reason")
    project = await _project_or_404(db, project_id, auth)
    draft = (
        await db.execute(
            select(PageDraft).where(PageDraft.id == draft_id, PageDraft.project_id == project.id)
        )
    ).scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="Page draft not found")
    if draft.state not in {"draft", "review"}:
        raise HTTPException(status_code=409, detail="Only a candidate draft can be rejected")
    draft.state = "rejected"
    draft.failure_message = body.reason.strip()
    await append_audit(
        db,
        action="page_draft.reject",
        payload={"page_draft_id": str(draft.id)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_draft(draft)


@router.get("/{project_id}/builds")
async def list_project_builds(
    project_id: UUID,
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
    ),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    project = await _project_or_404(db, project_id, auth)
    builds = (
        (
            await db.execute(
                select(SiteBuild)
                .where(SiteBuild.project_id == project.id)
                .order_by(SiteBuild.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(build.id),
            "status": build.status,
            "build_hash": build.build_hash,
            "previous_build_hash": build.previous_build_hash,
            "pages_built": build.pages_built,
            "created_at": build.created_at.isoformat() if build.created_at else None,
            "activated_at": build.activated_at.isoformat() if build.activated_at else None,
        }
        for build in builds
    ]


@router.post("/{project_id}/builds", status_code=status.HTTP_201_CREATED)
async def materialize_project_build(
    project_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    if not project.site_id:
        raise HTTPException(
            status_code=409, detail={"blockers": ["Apply a page draft before building"]}
        )
    site = (await db.execute(select(Site).where(Site.id == project.site_id))).scalar_one()
    manifest = SiteManifest.model_validate(site.manifest)
    rows = list(
        (await db.execute(select(SitePage).where(SitePage.site_id == site.id))).scalars().all()
    )
    index_states = {row.slug: row.index_state for row in rows}
    contacts = site.manifest.get("contacts") or {}
    context = {
        **(manifest.context or {}),
        "phone": contacts.get("phone", ""),
        "lead_token": site.lead_token,
        "lead_api_url": "/api/v1/leads/public",
    }
    builder = SiteBuilder(Path(settings.sites_root))
    started = time.perf_counter()
    try:
        result = builder.build(manifest, context, index_states=index_states, activate=False)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Candidate build failed: {exc}") from exc
    plan_ids = [
        str(plan_id)
        for plan_id in (
            await db.execute(
                select(PagePlan.id).where(
                    PagePlan.project_id == project.id, PagePlan.state == "approved"
                )
            )
        )
        .scalars()
        .all()
    ]
    applied_drafts = (
        (
            await db.execute(
                select(PageDraft).where(
                    PageDraft.project_id == project.id,
                    PageDraft.state == "applied",
                )
            )
        )
        .scalars()
        .all()
    )
    drafts_by_slug = {
        str((draft.page_manifest or {}).get("slug") or ""): draft for draft in applied_drafts
    }
    existing_pages = {row.slug: row for row in rows}
    for page_meta in result["pages"]:
        page = existing_pages.get(page_meta["slug"])
        if not page:
            page = SitePage(
                site_id=site.id,
                tenant_id=site.tenant_id,
                slug=page_meta["slug"],
                publish_state="draft",
                index_state=page_meta["index_state"],
            )
            db.add(page)
        draft = drafts_by_slug.get(page_meta["slug"])
        page.project_id = project.id
        page.page_draft_id = draft.id if draft else None
        page.page_plan_id = draft.page_plan_id if draft else None
        page.manifest = next(
            (
                item.model_dump(mode="json")
                for item in manifest.pages
                if item.slug == page_meta["slug"]
            ),
            {},
        )
        page.content_chars = page_meta["content_chars"]
        page.thin = page_meta["thin"]
        if page.thin:
            page.index_state = "noindex"
    build = SiteBuild(
        site_id=site.id,
        tenant_id=site.tenant_id,
        project_id=project.id,
        status="ready",
        build_hash=result["build_hash"],
        previous_build_hash=site.build_hash,
        pages_built=len(result["pages"]),
        duration_ms=int((time.perf_counter() - started) * 1000),
        log=f"candidate=true; indexed={result['indexed_count']}",
        manifest_snapshot=manifest.model_dump(mode="json"),
        page_plan_ids=plan_ids,
        requested_by=auth.user.id,
    )
    db.add(build)
    await append_audit(
        db,
        action="project.build.materialize",
        payload={"project_id": str(project.id), "build_hash": build.build_hash, "activated": False},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {
        "id": str(build.id),
        "status": build.status,
        "build_hash": build.build_hash,
        "activated": False,
    }


@router.get("/{project_id}/builds/{build_id}/preview/{preview_path:path}")
async def preview_project_build(
    project_id: UUID,
    build_id: UUID,
    preview_path: str = "",
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
    ),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    project = await _project_or_404(db, project_id, auth)
    build = (
        await db.execute(
            select(SiteBuild).where(SiteBuild.id == build_id, SiteBuild.project_id == project.id)
        )
    ).scalar_one_or_none()
    if (
        not build
        or build.status not in {"ready", "published"}
        or not build.build_hash
        or not project.site_id
    ):
        raise HTTPException(status_code=404, detail="Preview build not found")
    root = (
        SiteBuilder(Path(settings.sites_root))
        .release_path(str(project.site_id), build.build_hash)
        .resolve()
    )
    relative = preview_path.strip("/")
    target = (root / relative / "index.html" if relative else root / "index.html").resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="Preview page not found")
    return FileResponse(
        target, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"}
    )


@router.post("/{project_id}/builds/{build_id}/publish")
async def publish_project_build(
    project_id: UUID,
    build_id: UUID,
    body: BuildPublishRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="Explicit publish confirmation is required")
    project = await _project_or_404(db, project_id, auth)
    if not project.site_id or not project.domain:
        raise HTTPException(
            status_code=409, detail={"blockers": ["Project domain and site are required"]}
        )
    if (project.domain_check_meta or {}).get("dns_status") != "ok":
        raise HTTPException(
            status_code=409,
            detail={"blockers": ["Run a successful DNS check before publish"]},
        )
    build = (
        await db.execute(
            select(SiteBuild).where(SiteBuild.id == build_id, SiteBuild.project_id == project.id)
        )
    ).scalar_one_or_none()
    if not build or build.status != "ready" or not build.build_hash:
        raise HTTPException(
            status_code=409, detail={"blockers": ["Select a ready candidate build"]}
        )
    site = (await db.execute(select(Site).where(Site.id == project.site_id))).scalar_one()
    old_hash = site.build_hash
    builder = SiteBuilder(Path(settings.sites_root))
    if not builder.activate(str(site.id), build.build_hash):
        raise HTTPException(status_code=409, detail="Candidate release is unavailable")
    caddy = await CaddyClient().upsert_site_vhost(
        site.domain, str(Path(settings.caddy_sites_root) / str(site.id) / "current")
    )
    if not caddy.get("ok"):
        if old_hash:
            builder.activate(str(site.id), old_hash)
        raise HTTPException(
            status_code=503, detail="Caddy configuration failed; previous release restored"
        )
    site.previous_build_hash = old_hash
    site.build_hash = build.build_hash
    site.publish_state = "published"
    build.status = "published"
    build.activated_at = datetime.now(UTC)
    manifest = SiteManifest.model_validate(build.manifest_snapshot or site.manifest)
    existing = {
        page.slug: page
        for page in (await db.execute(select(SitePage).where(SitePage.site_id == site.id)))
        .scalars()
        .all()
    }
    for page_manifest in manifest.pages:
        page = existing.get(page_manifest.slug)
        if not page:
            page = SitePage(site_id=site.id, tenant_id=site.tenant_id, slug=page_manifest.slug)
            db.add(page)
        page.project_id = project.id
        page.publish_state = "published"
        page.index_state = (
            page_manifest.index_state.value
            if hasattr(page_manifest.index_state, "value")
            else str(page_manifest.index_state)
        )
    await append_audit(
        db,
        action="project.build.publish",
        payload={
            "project_id": str(project.id),
            "build_id": str(build.id),
            "build_hash": build.build_hash,
            "caddy": caddy,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {
        "project_id": str(project.id),
        "site_id": str(site.id),
        "build_hash": build.build_hash,
        "published": True,
    }


@router.post("/{project_id}/rollbacks")
async def rollback_project_build(
    project_id: UUID,
    body: BuildRollbackRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="Explicit rollback confirmation is required")
    project = await _project_or_404(db, project_id, auth)
    if not project.site_id:
        raise HTTPException(status_code=409, detail="Project has no site")
    site = (await db.execute(select(Site).where(Site.id == project.site_id))).scalar_one()
    target = (
        await db.execute(
            select(SiteBuild).where(
                SiteBuild.project_id == project.id,
                SiteBuild.build_hash == body.build_hash,
                SiteBuild.status.in_(["published", "ready", "rolled_back"]),
            )
        )
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Approved build hash not found")
    old_hash = site.build_hash
    builder = SiteBuilder(Path(settings.sites_root))
    if not builder.activate(str(site.id), body.build_hash):
        raise HTTPException(status_code=409, detail="Rollback release is unavailable")
    caddy = await CaddyClient().upsert_site_vhost(
        site.domain, str(Path(settings.caddy_sites_root) / str(site.id) / "current")
    )
    if not caddy.get("ok"):
        if old_hash:
            builder.activate(str(site.id), old_hash)
        raise HTTPException(
            status_code=503, detail="Caddy configuration failed; previous release restored"
        )
    site.previous_build_hash = old_hash
    site.build_hash = body.build_hash
    target.status = "published"
    await append_audit(
        db,
        action="project.build.rollback",
        payload={
            "project_id": str(project.id),
            "from_hash": old_hash,
            "to_hash": body.build_hash,
            "caddy": caddy,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {
        "project_id": str(project.id),
        "build_hash": body.build_hash,
        "previous_build_hash": old_hash,
    }
