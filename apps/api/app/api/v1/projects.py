from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from app.api.deps import AuthContext, require_roles
from app.api.v1.domains import normalize_hostname
from app.api.v1.media import _asset_path, ensure_media_review_allows_use
from app.core.config import get_settings
from app.core.security import sha256_hex
from app.db.session import get_db
from app.models import (
    AuditLog,
    AuthorProfileRevision,
    BuildReleaseGate,
    GeoPlace,
    Keyword,
    LeadRoutingPolicy,
    MediaAsset,
    PageDraft,
    PageIndexPromotion,
    PagePlan,
    Project,
    ProjectFactRevision,
    ProjectGeoPlace,
    ProjectKeyword,
    ProjectSemanticCollection,
    ProjectSemanticCollectionKeyword,
    ProjectSemanticKeywordGeoBinding,
    Site,
    SiteBuild,
    SiteBuildEvent,
    SitePage,
    SiteStructureRevision,
)
from app.schemas.author import AuthorProfileIn, author_profile_hash
from app.schemas.workflow import (
    PROTECTED_CONTACT_FIELDS,
    BuildLegalReviewIn,
    BuildPublishRequest,
    BuildRollbackRequest,
    CandidateBuildRequest,
    FactRevisionCreate,
    PageDraftAuthorAttachIn,
    PageDraftBlockMediaAttachIn,
    PageDraftDecision,
    PageDraftMediaAttachIn,
    PageDraftRequest,
    PageIndexPromotionIn,
    PagePlanCreate,
    PagePlanDecision,
    PagePlanUpdate,
    ProjectCreate,
    ProjectGeoUpdate,
    ProjectKeywordsUpdate,
    ProjectUpdate,
    QaOverrideIn,
    SemanticPlanTargetIn,
)
from app.services.audit import append_audit
from app.services.block_library import instantiate_kit_for_site
from app.services.caddy_client import CaddyClient
from app.services.captcha import captcha_public_config
from app.services.claim_slots import resolve_claim_slot_bindings
from app.services.design_profiles import design_snapshot, resolve_design_profile, theme_from_profile
from app.services.domain_health import domain_probe
from app.services.generation import create_page_draft
from app.services.indexnow import new_indexnow_key
from app.services.leads import get_blind, get_encryptor
from app.services.metrics import record_qa_verdict, record_release_transition
from app.services.operations import observe_alert, record_operational_event
from app.services.qa import run_page_qa
from app.services.release_gate import (
    evaluate_build_release_gate,
    legal_review_status,
    serialize_release_gate,
    store_release_gate,
)
from app.services.site_build_metadata import validate_page_metadata_snapshot
from app.services.site_build_queue import append_site_build_event
from app.services.telemetry import telemetry_token
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from site_panel_blocks import list_kits
from site_panel_shared.manifests import PageManifest, SiteManifest
from site_panel_ssg import BuildAsset, SiteBuilder
from sqlalchemy import delete, func, select
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


async def _approved_structure_for_new_page_plans(
    db: AsyncSession, project: Project
) -> SiteStructureRevision:
    revision = (
        await db.execute(
            select(SiteStructureRevision)
            .where(
                SiteStructureRevision.project_id == project.id,
                SiteStructureRevision.tenant_id == project.tenant_id,
                SiteStructureRevision.state == "approved",
            )
            .order_by(SiteStructureRevision.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not revision:
        raise HTTPException(
            status_code=409,
            detail="Approve a site structure revision before creating new PagePlans",
        )
    return revision


def _legal_publish_blockers(manifest: SiteManifest) -> list[str]:
    legal = manifest.legal or {}
    required = {
        "org": "Set the legal organization before publish",
        "address": "Set the legal address before publish",
        "jurisdiction": "Set the legal jurisdiction before publish",
        "privacy_email": "Set a public privacy/DSAR email before publish",
    }
    return [message for key, message in required.items() if not str(legal.get(key) or "").strip()]


def _record_release_event(db: AsyncSession, *, tenant_id: UUID, outcome: str) -> None:
    record_operational_event(
        db,
        tenant_id=tenant_id,
        event_type="release",
        severity="critical"
        if outcome == "failure"
        else "warning"
        if outcome == "blocked"
        else "info",
        outcome={"success": "success", "failure": "failure", "blocked": "warning"}[outcome],
    )


async def _lead_routing_publish_blockers(db: AsyncSession, site: Site) -> list[str]:
    if getattr(site, "project_id", None) is None:
        return []
    states = list(
        (
            await db.execute(
                select(LeadRoutingPolicy.state).where(
                    LeadRoutingPolicy.tenant_id == site.tenant_id,
                    LeadRoutingPolicy.site_id == site.id,
                )
            )
        )
        .scalars()
        .all()
    )
    if states and "active" not in states:
        return ["Activate the reviewed lead routing policy before publish"]
    return []


async def _project_site_or_409(db: AsyncSession, project: Project) -> Site:
    if not project.site_id:
        raise HTTPException(status_code=409, detail="Project has no site")
    site = (
        await db.execute(
            select(Site).where(Site.id == project.site_id, Site.tenant_id == project.tenant_id)
        )
    ).scalar_one_or_none()
    if (
        not site
        or site.tenant_id != project.tenant_id
        or (site.project_id is not None and site.project_id != project.id)
    ):
        raise HTTPException(status_code=409, detail="Project site link is invalid")
    return site


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


def _facts_hash(facts: dict, private_lead_email: str | None = None) -> str:
    payload = dict(facts)
    if private_lead_email:
        payload["private_lead_email_blind"] = get_blind().index(private_lead_email)
    return sha256_hex(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _public_fact_values(facts: dict) -> dict:
    values = dict(facts or {})
    contacts = values.get("contacts")
    if isinstance(contacts, dict):
        values["contacts"] = {
            key: value
            for key, value in contacts.items()
            if key not in PROTECTED_CONTACT_FIELDS | {"email", "private_lead_email"}
        }
    values.pop("private_lead_email", None)
    return values


def _serialize_fact(revision: ProjectFactRevision) -> dict:
    return {
        "id": str(revision.id),
        "version": revision.version,
        "state": revision.state,
        "facts": _public_fact_values(revision.facts or {}),
        "has_private_lead_email": bool(getattr(revision, "private_lead_email_enc", None)),
        "source_notes": revision.source_notes,
        "facts_hash": revision.facts_hash,
        "supersedes_id": str(revision.supersedes_id) if revision.supersedes_id else None,
        "confirmed_at": revision.confirmed_at.isoformat() if revision.confirmed_at else None,
        "created_at": revision.created_at.isoformat() if revision.created_at else None,
    }


async def _semantic_target_snapshot(
    db: AsyncSession,
    project: Project,
    target: SemanticPlanTargetIn,
) -> dict:
    collection = (
        await db.execute(
            select(ProjectSemanticCollection).where(
                ProjectSemanticCollection.id == target.collection_id,
                ProjectSemanticCollection.project_id == project.id,
                ProjectSemanticCollection.tenant_id == project.tenant_id,
                ProjectSemanticCollection.state == "approved",
            )
        )
    ).scalar_one_or_none()
    if not collection:
        raise HTTPException(status_code=409, detail="Select an approved semantic collection")
    member_ids = {item.collection_keyword_id for item in target.targets}
    members = list(
        (
            await db.execute(
                select(ProjectSemanticCollectionKeyword).where(
                    ProjectSemanticCollectionKeyword.id.in_(member_ids),
                    ProjectSemanticCollectionKeyword.collection_id == collection.id,
                    ProjectSemanticCollectionKeyword.project_id == project.id,
                    ProjectSemanticCollectionKeyword.tenant_id == project.tenant_id,
                )
            )
        )
        .scalars()
        .all()
    )
    if len(members) != len(member_ids):
        raise HTTPException(
            status_code=409, detail="Semantic targets must belong to the approved collection"
        )
    binding_ids = {binding_id for item in target.targets for binding_id in item.geo_binding_ids}
    bindings = list(
        (
            await db.execute(
                select(ProjectSemanticKeywordGeoBinding).where(
                    ProjectSemanticKeywordGeoBinding.id.in_(binding_ids),
                    ProjectSemanticKeywordGeoBinding.project_id == project.id,
                    ProjectSemanticKeywordGeoBinding.tenant_id == project.tenant_id,
                )
            )
        )
        .scalars()
        .all()
    )
    by_member = {binding.collection_keyword_id: [] for binding in bindings}
    for binding in bindings:
        by_member.setdefault(binding.collection_keyword_id, []).append(binding)
    if len(bindings) != len(binding_ids):
        raise HTTPException(status_code=409, detail="Semantic target geography is invalid")
    member_by_id = {member.id: member for member in members}
    snapshot_targets = []
    for item in target.targets:
        member = member_by_id[item.collection_keyword_id]
        selected = {binding.id for binding in by_member.get(member.id, [])}
        if selected != set(item.geo_binding_ids):
            raise HTTPException(
                status_code=409, detail="Geo bindings do not belong to the semantic keyword"
            )
        snapshot_targets.append(
            {
                "collection_keyword_id": str(member.id),
                "project_keyword_id": str(member.project_keyword_id),
                "geo_binding_ids": [str(binding_id) for binding_id in item.geo_binding_ids],
                "cluster": member.cluster,
                "intent": member.intent,
            }
        )
    return {
        "collection_id": str(collection.id),
        "collection_version": collection.version,
        "targets": snapshot_targets,
    }


def _block_selection_with_claim_slot_bindings(
    block_selection: dict, claim_slot_bindings: list[dict]
) -> dict:
    return {
        **{key: value for key, value in block_selection.items() if key != "claim_slot_bindings"},
        "claim_slot_bindings": claim_slot_bindings,
    }


def _effective_plan_block_ids(project: Project, plan: PagePlan) -> list[str]:
    blocks, _css_vars, _kit_meta = instantiate_kit_for_site(plan.kit_key, str(project.id))
    available = {block.type for block in blocks}
    selected = (plan.block_selection or {}).get("blocks")
    if selected is None:
        return [block.type for block in blocks]
    if not isinstance(selected, list) or len(selected) != len(set(selected)):
        raise ValueError("PagePlan contains an invalid curated block selection")
    if any(not isinstance(block_id, str) or block_id not in available for block_id in selected):
        raise ValueError("PagePlan contains an invalid curated block selection")
    return selected


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
        "semantic_target_snapshot": plan.semantic_target_snapshot or {},
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


@router.get("/{project_id}/activity")
async def project_activity(
    project_id: UUID,
    action: str | None = Query(default=None, max_length=128),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    statement = select(AuditLog).where(
        AuditLog.tenant_id == project.tenant_id,
        AuditLog.payload["project_id"].astext == str(project.id),
    )
    if action and action.strip():
        statement = statement.where(AuditLog.action == action.strip())
    total = await db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = list(
        (await db.execute(statement.order_by(AuditLog.id.desc()).offset(offset).limit(limit)))
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": entry.id,
                "action": entry.action,
                "actor_id": str(entry.actor_id) if entry.actor_id else None,
                "created_at": entry.created_at.isoformat() if entry.created_at else None,
                "record_hash": entry.record_hash,
            }
            for entry in rows
        ],
        "offset": offset,
        "limit": limit,
        "total": int(total or 0),
    }


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
    private_lead_email = str(body.private_lead_email) if body.private_lead_email else None
    revision = ProjectFactRevision(
        project_id=project.id,
        tenant_id=project.tenant_id,
        supersedes_id=previous.id if previous else None,
        version=(previous.version if previous else 0) + 1,
        facts=body.facts,
        private_lead_email_enc=get_encryptor().encrypt(private_lead_email)
        if private_lead_email
        else None,
        source_notes=body.source_notes.strip() if body.source_notes else None,
        facts_hash=_facts_hash(body.facts, private_lead_email),
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
        private_lead_email_enc=getattr(source, "private_lead_email_enc", None),
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
            "id": str(binding.id),
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
    semantic_keyword_ref = (
        await db.execute(
            select(ProjectSemanticCollectionKeyword.id)
            .where(ProjectSemanticCollectionKeyword.project_id == project.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if semantic_keyword_ref:
        raise HTTPException(
            status_code=409,
            detail=(
                "Project keywords are referenced by a semantic collection; "
                "edit the collection first"
            ),
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
            "id": str(binding.id),
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
    semantic_geo_ref = (
        await db.execute(
            select(ProjectSemanticKeywordGeoBinding.id)
            .where(ProjectSemanticKeywordGeoBinding.project_id == project.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if semantic_geo_ref:
        raise HTTPException(
            status_code=409,
            detail=(
                "Project geography is referenced by a semantic collection; "
                "edit the collection first"
            ),
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


_COMMERCIAL_PAGE_RECIPES = (
    ("/about", "О компании", "company_history"),
    ("/history", "История компании", "company_history"),
    ("/mission", "Миссия компании", "mission"),
    ("/business", "Услуги для юридических лиц", "legal_entities"),
    ("/payment", "Оплата и условия", "payment_terms"),
)


@router.post("/{project_id}/commercial-page-plans", status_code=status.HTTP_201_CREATED)
async def create_commercial_page_plans(
    project_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    project = await _project_or_404(db, project_id, auth)
    facts = await _confirmed_facts(db, project)
    values = _public_fact_values(facts.facts or {})
    service = str(values.get("service") or "Услуги")
    existing = list(
        (
            await db.execute(
                select(PagePlan).where(
                    PagePlan.project_id == project.id,
                    PagePlan.slug.in_([recipe[0] for recipe in _COMMERCIAL_PAGE_RECIPES]),
                    PagePlan.state.in_(("draft", "review", "approved")),
                )
            )
        )
        .scalars()
        .all()
    )
    existing_slugs = {plan.slug for plan in existing}
    recipes = [
        recipe
        for recipe in _COMMERCIAL_PAGE_RECIPES
        if recipe[0] not in existing_slugs and str(values.get(recipe[2]) or "").strip()
    ]
    structure = await _approved_structure_for_new_page_plans(db, project) if recipes else None
    plans = []
    for slug, title, fact_key in recipes:
        plan = PagePlan(
            project_id=project.id,
            tenant_id=project.tenant_id,
            version=1,
            slug=slug,
            objective=f"{title}: подтверждённые facts для {service}",
            intent="commercial",
            risk_notes=(
                "Создано из подтверждённых business facts; до публикации требуется "
                "review, QA и candidate preview."
            ),
            kit_key="service-local-v1",
            block_selection={
                "blocks": ["hero", "trust_bar", "faq", "contacts", "lead_form", "footer"]
            },
            source_refs={
                "commercial_fact_key": fact_key,
                "fact_revision_id": str(facts.id),
                "site_structure_revision_id": str(structure.id),
                "site_structure_version": structure.version,
            },
        )
        db.add(plan)
        plans.append(plan)
    await db.flush()
    await append_audit(
        db,
        action="project.commercial_page_plans.create",
        payload={"project_id": str(project.id), "page_plan_ids": [str(plan.id) for plan in plans]},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
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
    if "claim_slot_bindings" in body.block_selection:
        raise HTTPException(
            status_code=422, detail="Use the typed claim_slot_bindings field for claim placement"
        )
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
    structure = await _approved_structure_for_new_page_plans(db, project)
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
        block_selection=_block_selection_with_claim_slot_bindings(
            body.block_selection,
            [binding.model_dump(mode="json") for binding in body.claim_slot_bindings],
        ),
        source_refs={
            **body.source_refs,
            **(
                {"semantic_target": body.semantic_target.model_dump(mode="json")}
                if body.semantic_target
                else {}
            ),
            "site_structure_revision_id": str(structure.id),
            "site_structure_version": structure.version,
        },
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
    if "claim_slot_bindings" in (body.block_selection or {}):
        raise HTTPException(
            status_code=422, detail="Use the typed claim_slot_bindings field for claim placement"
        )
    changed: list[str] = []
    for field in ("objective", "intent", "risk_notes", "kit_key", "source_refs"):
        value = getattr(body, field)
        if value is not None and getattr(plan, field) != value:
            setattr(plan, field, value)
            changed.append(field)
    if body.block_selection is not None or body.claim_slot_bindings is not None:
        selection = (
            body.block_selection if body.block_selection is not None else plan.block_selection or {}
        )
        bindings = (
            [binding.model_dump(mode="json") for binding in body.claim_slot_bindings]
            if body.claim_slot_bindings is not None
            else (plan.block_selection or {}).get("claim_slot_bindings") or []
        )
        next_selection = _block_selection_with_claim_slot_bindings(selection, bindings)
        if plan.block_selection != next_selection:
            plan.block_selection = next_selection
            changed.append("block_selection")
    if body.semantic_target is not None:
        plan.source_refs = {
            **(plan.source_refs or {}),
            "semantic_target": body.semantic_target.model_dump(mode="json"),
        }
        changed.append("semantic_target")
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
    try:
        resolve_claim_slot_bindings(
            kit_key=plan.kit_key,
            block_ids=_effective_plan_block_ids(project, plan),
            block_selection=plan.block_selection,
            facts=facts.facts or {},
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    keywords, geo, blockers = await _selection_snapshots(db, project)
    if blockers:
        raise HTTPException(status_code=409, detail={"blockers": blockers})
    plan.fact_revision_id = facts.id
    plan.keyword_snapshot = keywords
    plan.geo_snapshot = geo
    raw_target = (plan.source_refs or {}).get("semantic_target")
    if raw_target:
        try:
            semantic_target = SemanticPlanTargetIn.model_validate(raw_target)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="Semantic target is invalid") from exc
        plan.semantic_target_snapshot = await _semantic_target_snapshot(
            db, project, semantic_target
        )
    else:
        plan.semantic_target_snapshot = {}
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
    covered_project_keywords = {
        item.get("project_keyword_id")
        for plan in plans
        if plan.state != "rejected"
        for item in (plan.semantic_target_snapshot or {}).get("targets", [])
        if isinstance(item, dict) and item.get("project_keyword_id")
    }
    unmapped_plans = [
        {"plan_id": str(plan.id), "slug": plan.slug, "state": plan.state}
        for plan in plans
        if plan.state != "rejected" and not (plan.semantic_target_snapshot or {}).get("targets")
    ]
    return {
        "selected": len(selected),
        "covered": sum(str(binding.id) in covered_project_keywords for binding, _ in selected),
        "uncovered": [
            {"keyword_id": str(keyword.id), "phrase": keyword.phrase}
            for binding, keyword in selected
            if str(binding.id) not in covered_project_keywords
        ],
        "plans": len(plans),
        "unmapped_plans": unmapped_plans,
        "coverage_basis": "explicit_semantic_target_snapshot_only",
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


def _verified_media_hash(asset: MediaAsset) -> str:
    meta = asset.meta or {}
    provenance = meta.get("provenance") or {}
    hashes = meta.get("hashes") or {}
    if provenance.get("kind") != "manual_upload" or provenance.get("rights_confirmed") is not True:
        raise ValueError("Media asset rights are not confirmed")
    expires_at = provenance.get("license_expires_at")
    if expires_at:
        try:
            if date.fromisoformat(str(expires_at)) < date.today():
                raise ValueError("Media asset license has expired")
        except ValueError as exc:
            if str(exc) == "Media asset license has expired":
                raise
            raise ValueError("Media asset license expiry is invalid") from exc
    stored_sha256 = str(hashes.get("stored_sha256") or "")
    if not re.fullmatch(r"[a-f0-9]{64}", stored_sha256):
        raise ValueError("Media asset hash is unavailable")
    return stored_sha256


async def _approved_author_snapshot_or_409(
    db: AsyncSession,
    *,
    project: Project,
    revision_id: UUID,
) -> dict:
    revision = (
        await db.execute(
            select(AuthorProfileRevision).where(
                AuthorProfileRevision.id == revision_id,
                AuthorProfileRevision.tenant_id == project.tenant_id,
                AuthorProfileRevision.project_id == project.id,
                AuthorProfileRevision.state == "approved",
            )
        )
    ).scalar_one_or_none()
    if not revision:
        raise HTTPException(
            status_code=409,
            detail="Approved author profile revision is unavailable",
        )
    profile = AuthorProfileIn(
        slug=revision.slug,
        name=revision.name,
        role=revision.role,
        biography=revision.biography,
        expertise=list(revision.expertise or []),
        evidence=list(revision.evidence or []),
        portrait_asset_id=revision.portrait_asset_id,
    )
    if author_profile_hash(profile) != revision.profile_hash:
        raise HTTPException(status_code=409, detail="Author profile integrity check failed")
    asset = (
        await db.execute(
            select(MediaAsset).where(
                MediaAsset.id == revision.portrait_asset_id,
                MediaAsset.tenant_id == project.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=409, detail="Author portrait is unavailable")
    try:
        _asset_path(asset)
        stored_sha256 = _verified_media_hash(asset)
        await ensure_media_review_allows_use(
            db,
            tenant_id=project.tenant_id,
            asset_id=asset.id,
            stored_sha256=stored_sha256,
        )
    except (HTTPException, ValueError) as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        raise HTTPException(status_code=409, detail=f"Author portrait blocker: {detail}") from exc
    return {
        "revision_id": revision.id,
        "profile_hash": revision.profile_hash,
        "slug": profile.slug,
        "name": profile.name,
        "role": profile.role,
        "biography": profile.biography,
        "expertise": profile.expertise,
        "evidence": profile.evidence,
        "portrait": {
            "asset_id": asset.id,
            "stored_sha256": stored_sha256,
            "alt": f"Портрет {profile.name}",
        },
    }


def _draft_manifest_hash(manifest: dict) -> str:
    page = PageManifest.model_validate(manifest)
    return sha256_hex(
        json.dumps(
            page.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    )


def _current_qa_run(draft: PageDraft) -> dict | None:
    runs = draft.qa_runs or []
    if not runs or not draft.content_hash:
        return None
    latest = runs[-1]
    if (
        not isinstance(latest, dict)
        or latest.get("source_hash") != draft.content_hash
        or latest.get("verdict") != draft.last_qa_verdict
    ):
        return None
    return latest


def _serialize_draft(draft: PageDraft) -> dict:
    return {
        "id": str(draft.id),
        "page_plan_id": str(draft.page_plan_id),
        "author_profile_revision_id": (
            str(author_revision_id)
            if (author_revision_id := getattr(draft, "author_profile_revision_id", None))
            else None
        ),
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
    resolved_design = await resolve_design_profile(
        db, tenant_id=project.tenant_id, project_id=project.id
    )
    profile_snapshot = design_snapshot(resolved_design)
    if (
        resolved_design.effective_profile
        and plan.kit_key not in resolved_design.effective_profile.layout.allowed_kits
    ):
        raise HTTPException(
            status_code=409,
            detail={"blockers": ["PagePlan kit is not allowed by the approved design profile"]},
        )
    page_manifest, input_snapshot, _candidate_text = create_page_draft(
        project=project,
        plan=plan,
        facts=facts,
        design=profile_snapshot,
        theme=(
            theme_from_profile(resolved_design.effective_profile)
            if resolved_design.effective_profile
            else None
        ),
    )
    content_hash = _draft_manifest_hash(page_manifest)
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


@router.post("/{project_id}/page-drafts/{draft_id}/media")
async def attach_draft_media(
    project_id: UUID,
    draft_id: UUID,
    body: PageDraftMediaAttachIn,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    draft = (
        await db.execute(
            select(PageDraft)
            .where(PageDraft.id == draft_id, PageDraft.project_id == project.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="Page draft not found")
    if draft.state != "draft":
        raise HTTPException(
            status_code=409, detail="Attach media before submitting the draft for review"
        )
    asset = (
        await db.execute(
            select(MediaAsset).where(
                MediaAsset.id == body.asset_id,
                MediaAsset.tenant_id == project.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Media asset not found")
    try:
        _asset_path(asset)
        stored_sha256 = _verified_media_hash(asset)
        await ensure_media_review_allows_use(
            db,
            tenant_id=project.tenant_id,
            asset_id=asset.id,
            stored_sha256=stored_sha256,
        )
        page = PageManifest.model_validate(draft.page_manifest or {})
        manifest = PageManifest.model_validate(
            {
                **page.model_dump(mode="json"),
                "media": [
                    *page.media,
                    {"asset_id": asset.id, "stored_sha256": stored_sha256, "alt": body.alt.strip()},
                ],
            }
        ).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    draft.page_manifest = manifest
    draft.content_hash = _draft_manifest_hash(manifest)
    draft.qa_runs = []
    draft.last_qa_verdict = None
    draft.qa_override = {}
    await append_audit(
        db,
        action="page_draft.media.attach",
        payload={
            "project_id": str(project.id),
            "page_draft_id": str(draft.id),
            "asset_id": str(asset.id),
            "stored_sha256": stored_sha256,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_draft(draft)


@router.post("/{project_id}/page-drafts/{draft_id}/author")
async def attach_draft_author(
    project_id: UUID,
    draft_id: UUID,
    body: PageDraftAuthorAttachIn,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    draft = (
        await db.execute(
            select(PageDraft)
            .where(PageDraft.id == draft_id, PageDraft.project_id == project.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="Page draft not found")
    if draft.state != "draft":
        raise HTTPException(
            status_code=409,
            detail="Attach an author before submitting the draft for review",
        )
    author = await _approved_author_snapshot_or_409(
        db,
        project=project,
        revision_id=body.author_profile_revision_id,
    )
    try:
        page = PageManifest.model_validate(draft.page_manifest or {})
        manifest = PageManifest.model_validate(
            {**page.model_dump(mode="json"), "author": author}
        ).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    draft.author_profile_revision_id = body.author_profile_revision_id
    draft.page_manifest = manifest
    draft.content_hash = _draft_manifest_hash(manifest)
    draft.qa_runs = []
    draft.last_qa_verdict = None
    draft.qa_override = {}
    await append_audit(
        db,
        action="page_draft.author.attach",
        payload={
            "project_id": str(project.id),
            "page_draft_id": str(draft.id),
            "author_profile_revision_id": str(body.author_profile_revision_id),
            "author_profile_hash": author["profile_hash"],
            "portrait_asset_id": str(author["portrait"]["asset_id"]),
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_draft(draft)


@router.post("/{project_id}/page-drafts/{draft_id}/block-media")
async def attach_draft_block_media(
    project_id: UUID,
    draft_id: UUID,
    body: PageDraftBlockMediaAttachIn,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    draft = (
        await db.execute(
            select(PageDraft)
            .where(PageDraft.id == draft_id, PageDraft.project_id == project.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="Page draft not found")
    if draft.state != "draft":
        raise HTTPException(
            status_code=409, detail="Attach media before submitting the draft for review"
        )
    asset = (
        await db.execute(
            select(MediaAsset).where(
                MediaAsset.id == body.asset_id,
                MediaAsset.tenant_id == project.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Media asset not found")
    try:
        _asset_path(asset)
        stored_sha256 = _verified_media_hash(asset)
        await ensure_media_review_allows_use(
            db,
            tenant_id=project.tenant_id,
            asset_id=asset.id,
            stored_sha256=stored_sha256,
        )
        page = PageManifest.model_validate(draft.page_manifest or {})
        manifest = PageManifest.model_validate(
            {
                **page.model_dump(mode="json"),
                "block_media": {
                    **page.block_media,
                    body.block_id: {
                        "asset_id": asset.id,
                        "stored_sha256": stored_sha256,
                        "alt": body.alt.strip(),
                    },
                },
            }
        ).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    draft.page_manifest = manifest
    draft.content_hash = _draft_manifest_hash(manifest)
    draft.qa_runs = []
    draft.last_qa_verdict = None
    draft.qa_override = {}
    await append_audit(
        db,
        action="page_draft.block_media.attach",
        payload={
            "project_id": str(project.id),
            "page_draft_id": str(draft.id),
            "block_id": body.block_id,
            "asset_id": str(asset.id),
            "stored_sha256": stored_sha256,
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
                *[
                    str(value or "")
                    for block_values in (
                        (item.page_manifest or {}).get("block_slot_values") or {}
                    ).values()
                    for value in block_values.values()
                ],
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
    record_qa_verdict(verdict=result["verdict"])
    record_operational_event(
        db,
        tenant_id=project.tenant_id,
        event_type="qa",
        severity="warning" if result["verdict"] in {"warn", "block"} else "info",
        outcome={"pass": "success", "warn": "warning", "block": "failure"}[result["verdict"]],
    )
    if result["verdict"] == "block":
        await observe_alert(
            db,
            tenant_id=project.tenant_id,
            signal_code="qa-block",
            active=True,
        )
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
    if draft.state != "draft" or not _current_qa_run(draft):
        raise HTTPException(
            status_code=409,
            detail={"blockers": ["Run QA for the current draft content before review"]},
        )
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
    if not _current_qa_run(draft):
        raise HTTPException(
            status_code=409,
            detail={"blockers": ["Run QA for the current draft content before apply"]},
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
    if page.author:
        if draft.author_profile_revision_id != page.author.revision_id:
            raise HTTPException(
                status_code=409,
                detail={"blockers": ["Author binding does not match the page artifact"]},
            )
        current_author = await _approved_author_snapshot_or_409(
            db,
            project=project,
            revision_id=page.author.revision_id,
        )
        expected_author = PageManifest.model_validate(
            {**page.model_dump(mode="json"), "author": current_author}
        ).author
        if expected_author != page.author:
            raise HTTPException(
                status_code=409,
                detail={
                    "blockers": ["Author profile or portrait changed; attach it in a new draft"]
                },
            )
    elif draft.author_profile_revision_id:
        raise HTTPException(
            status_code=409,
            detail={"blockers": ["Author binding is missing from the page artifact"]},
        )
    resolved_design = await resolve_design_profile(
        db, tenant_id=project.tenant_id, project_id=project.id
    )
    current_design = design_snapshot(resolved_design)
    if page.design and page.design.profile_hash != (current_design or {}).get("profile_hash"):
        raise HTTPException(
            status_code=409,
            detail={"blockers": ["Design profile changed; regenerate this PageDraft"]},
        )
    if project.site_id:
        site = await _project_site_or_409(db, project)
        manifest = SiteManifest.model_validate(site.manifest)
        pages = [existing for existing in manifest.pages if existing.slug != page.slug]
        if (
            manifest.design
            and page.design
            and manifest.design.profile_hash != page.design.profile_hash
        ):
            raise HTTPException(
                status_code=409,
                detail={"blockers": ["Applied site uses another approved design profile"]},
            )
        if not manifest.design and page.design:
            manifest.design = page.design
            _blocks, manifest.css_vars, _meta = instantiate_kit_for_site(
                plan.kit_key,
                site.id,
                service=page.service,
                theme=(
                    theme_from_profile(resolved_design.effective_profile)
                    if resolved_design.effective_profile
                    else None
                ),
            )
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
            theme=(
                theme_from_profile(resolved_design.effective_profile)
                if resolved_design.effective_profile
                else None
            ),
        )
        manifest = SiteManifest(
            site_id=site_id,
            tenant_id=project.tenant_id,
            domain=project.domain,
            locale=project.locale,
            css_vars=css_vars,
            design=page.design,
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


def _serialize_index_promotion(promotion: PageIndexPromotion) -> dict:
    return {
        "id": str(promotion.id),
        "slug": promotion.slug,
        "source_hash": promotion.source_hash,
        "qa_source_hash": promotion.qa_source_hash,
        "reason": promotion.reason,
        "decided_at": promotion.decided_at.isoformat() if promotion.decided_at else None,
    }


async def _index_promotion_candidates(
    db: AsyncSession, *, project: Project, site: Site
) -> list[dict]:
    manifest = SiteManifest.model_validate(site.manifest)
    drafts = list(
        (
            await db.execute(
                select(PageDraft).where(
                    PageDraft.project_id == project.id,
                    PageDraft.state == "applied",
                    PageDraft.content_hash.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    current_drafts = {
        draft.content_hash: draft
        for draft in drafts
        if draft.content_hash
        and _current_qa_run(draft)
        and draft.last_qa_verdict == "pass"
        and _draft_manifest_hash(draft.page_manifest or {}) == draft.content_hash
    }
    promotions = list(
        (
            await db.execute(
                select(PageIndexPromotion)
                .where(
                    PageIndexPromotion.project_id == project.id,
                    PageIndexPromotion.site_id == site.id,
                )
                .order_by(PageIndexPromotion.decided_at.desc())
            )
        )
        .scalars()
        .all()
    )
    approved = {(promotion.slug, promotion.source_hash): promotion for promotion in promotions}
    result = []
    for page in manifest.pages:
        source_hash = _draft_manifest_hash(page.model_dump(mode="json"))
        promotion = approved.get((page.slug, source_hash))
        draft = current_drafts.get(source_hash)
        result.append(
            {
                "slug": page.slug,
                "source_hash": source_hash,
                "qa_verdict": draft.last_qa_verdict if draft else None,
                "status": "approved" if promotion else "eligible" if draft else "stale",
                "promotion": _serialize_index_promotion(promotion) if promotion else None,
            }
        )
    return result


@router.get("/{project_id}/index-promotions")
async def list_index_promotions(
    project_id: UUID,
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
    ),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    project = await _project_or_404(db, project_id, auth)
    if not project.site_id:
        return []
    site = await _project_site_or_409(db, project)
    return await _index_promotion_candidates(db, project=project, site=site)


@router.post("/{project_id}/index-promotions", status_code=status.HTTP_201_CREATED)
async def create_index_promotion(
    project_id: UUID,
    body: PageIndexPromotionIn,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not body.confirmed:
        raise HTTPException(
            status_code=400, detail="Explicit index promotion confirmation is required"
        )
    project = await _project_or_404(db, project_id, auth)
    site = await _project_site_or_409(db, project)
    manifest = SiteManifest.model_validate(site.manifest)
    page = next((item for item in manifest.pages if item.slug == body.slug), None)
    if page is None:
        raise HTTPException(status_code=404, detail="Current project page not found")
    source_hash = _draft_manifest_hash(page.model_dump(mode="json"))
    drafts = list(
        (
            await db.execute(
                select(PageDraft)
                .where(
                    PageDraft.project_id == project.id,
                    PageDraft.state == "applied",
                    PageDraft.content_hash == source_hash,
                )
                .order_by(PageDraft.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    draft = next(
        (
            item
            for item in drafts
            if item.page_manifest
            and PageManifest.model_validate(item.page_manifest).slug == body.slug
            and _current_qa_run(item)
            and item.last_qa_verdict == "pass"
        ),
        None,
    )
    if draft is None:
        raise HTTPException(
            status_code=409,
            detail={
                "blockers": ["Run passing QA for the current page content before index promotion"]
            },
        )
    promotion = PageIndexPromotion(
        tenant_id=project.tenant_id,
        site_id=site.id,
        project_id=project.id,
        page_draft_id=draft.id,
        slug=body.slug,
        source_hash=source_hash,
        qa_source_hash=draft.content_hash,
        reason=body.reason.strip(),
        decided_by=auth.user.id,
    )
    db.add(promotion)
    await db.flush()
    await append_audit(
        db,
        action="page.index.promote",
        payload={
            "project_id": str(project.id),
            "site_id": str(site.id),
            "page_draft_id": str(draft.id),
            "slug": body.slug,
            "source_hash": source_hash,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_index_promotion(promotion)


def _build_index_promotion_provenance(build: SiteBuild) -> list[dict]:
    """Expose only immutable, non-actor promotion evidence recorded with a build."""
    try:
        metadata_by_slug = validate_page_metadata_snapshot(build.page_metadata_snapshot)
    except (AttributeError, ValueError):
        return []
    return [
        {
            "slug": slug,
            "reason": metadata["index_promotion"]["reason"],
            "decided_at": metadata["index_promotion"]["decided_at"],
        }
        for slug, metadata in metadata_by_slug.items()
        if metadata.get("index_promotion")
    ]


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
                .where(
                    SiteBuild.project_id == project.id,
                    SiteBuild.tenant_id == project.tenant_id,
                    SiteBuild.site_id == project.site_id,
                )
                .order_by(SiteBuild.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    gates = (
        {
            gate.build_id: gate
            for gate in (
                await db.execute(
                    select(BuildReleaseGate).where(
                        BuildReleaseGate.build_id.in_([build.id for build in builds])
                    )
                )
            )
            .scalars()
            .all()
        }
        if builds
        else {}
    )
    legal_events = (
        list(
            (
                await db.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.tenant_id == project.tenant_id,
                        AuditLog.action == "project.build.legal_review",
                        AuditLog.payload["project_id"].astext == str(project.id),
                        AuditLog.payload["build_id"].astext.in_(
                            [str(build.id) for build in builds]
                        ),
                    )
                    .order_by(AuditLog.id.desc())
                )
            )
            .scalars()
            .all()
        )
        if builds
        else []
    )
    legal_history: dict[str, list[dict]] = {}
    for event in legal_events:
        payload = event.payload or {}
        legal_history.setdefault(payload.get("build_id", ""), []).append(
            {
                "decision": payload.get("decision"),
                "evidence_ref": payload.get("evidence_ref"),
                "reason": payload.get("reason"),
                "replacement_guidance": payload.get("replacement_guidance"),
                "legal_snapshot_hash": payload.get("legal_snapshot_hash"),
                "actor_id": str(event.actor_id) if event.actor_id else None,
                "reviewed_at": event.created_at.isoformat() if event.created_at else None,
            }
        )
    build_events = (
        list(
            (
                await db.execute(
                    select(SiteBuildEvent)
                    .where(
                        SiteBuildEvent.tenant_id == project.tenant_id,
                        SiteBuildEvent.project_id == project.id,
                        SiteBuildEvent.site_build_id.in_([build.id for build in builds]),
                    )
                    .order_by(SiteBuildEvent.site_build_id, SiteBuildEvent.sequence.desc())
                )
            )
            .scalars()
            .all()
        )
        if builds
        else []
    )
    event_history: dict[UUID, list[dict]] = {}
    for event in reversed(build_events):
        event_history.setdefault(event.site_build_id, []).append(
            {
                "sequence": event.sequence,
                "attempt": event.attempt,
                "type": event.event_type,
                "code": event.safe_code,
                "details": event.details or {},
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
        )
    site = await _project_site_or_409(db, project) if project.site_id else None
    active_hash = site.build_hash if site else None
    return [
        {
            "id": str(build.id),
            "status": build.status,
            "build_hash": build.build_hash,
            "previous_build_hash": build.previous_build_hash,
            "pages_built": build.pages_built,
            "duration_ms": getattr(build, "duration_ms", 0),
            "attempt_count": getattr(build, "attempt_count", 0),
            "failure_code": getattr(build, "failure_code", None),
            "input_snapshot_hash": getattr(build, "input_snapshot_hash", None),
            "snapshot_version": getattr(build, "snapshot_version", 0),
            "queue_priority": getattr(build, "queue_priority", 50),
            "not_before": (
                build.not_before.isoformat() if getattr(build, "not_before", None) else None
            ),
            "created_at": build.created_at.isoformat() if build.created_at else None,
            "started_at": (
                build.started_at.isoformat() if getattr(build, "started_at", None) else None
            ),
            "completed_at": (
                build.completed_at.isoformat() if getattr(build, "completed_at", None) else None
            ),
            "activated_at": build.activated_at.isoformat() if build.activated_at else None,
            "first_published_at": (
                build.first_published_at.isoformat()
                if getattr(build, "first_published_at", None)
                else None
            ),
            "is_active": bool(build.build_hash and build.build_hash == active_hash),
            "is_historical_published": bool(
                getattr(build, "first_published_at", None)
                and build.build_hash
                and build.build_hash != active_hash
            ),
            "retryable": bool(
                build.status == "failed"
                and getattr(build, "snapshot_version", 0) == 1
                and getattr(build, "input_snapshot", None)
            ),
            "rollback_eligible": bool(
                build.status == "ready"
                and getattr(build, "first_published_at", None)
                and build.build_hash
                and build.build_hash != active_hash
            ),
            "events": event_history.get(build.id, [])[-20:],
            "release_gate": serialize_release_gate(gates.get(build.id)),
            "legal_review": {
                **legal_review_status(build),
                "history": legal_history.get(str(build.id), []),
            },
            "index_promotion_provenance": _build_index_promotion_provenance(build),
        }
        for build in builds
    ]


def _manifest_asset_usage(
    *,
    manifest: SiteManifest,
    scope: str,
    source: dict,
) -> list[dict]:
    usages = []
    for page in manifest.pages:
        for attachment in page.media:
            usages.append(
                {
                    "scope": scope,
                    "source": source,
                    "slug": page.slug,
                    "placement": "gallery",
                    "asset_id": str(attachment.asset_id),
                    "expected_sha256": attachment.stored_sha256,
                    "alt": attachment.alt,
                }
            )
        for block_id, attachment in page.block_media.items():
            usages.append(
                {
                    "scope": scope,
                    "source": source,
                    "slug": page.slug,
                    "placement": f"block:{block_id}",
                    "asset_id": str(attachment.asset_id),
                    "expected_sha256": attachment.stored_sha256,
                    "alt": attachment.alt,
                }
            )
        if page.author:
            usages.append(
                {
                    "scope": scope,
                    "source": source,
                    "slug": page.slug,
                    "placement": "author_portrait",
                    "asset_id": str(page.author.portrait.asset_id),
                    "expected_sha256": page.author.portrait.stored_sha256,
                    "alt": page.author.portrait.alt,
                    "author_profile_revision_id": str(page.author.revision_id),
                    "author_profile_hash": page.author.profile_hash,
                }
            )
    return usages


def _asset_usage_status(asset: MediaAsset | None, expected_sha256: str) -> str:
    if asset is None:
        return "missing_asset"
    try:
        if _verified_media_hash(asset) != expected_sha256:
            return "hash_mismatch"
        _asset_path(asset)
    except (HTTPException, ValueError):
        return "unavailable"
    return "verified"


async def _manifest_media_compliance_blockers(
    db: AsyncSession, *, manifest: SiteManifest, tenant_id: UUID
) -> list[str]:
    usages = _manifest_asset_usage(manifest=manifest, scope="release", source={})
    if not usages:
        return []
    asset_ids = {UUID(item["asset_id"]) for item in usages}
    assets = list(
        (
            await db.execute(
                select(MediaAsset).where(
                    MediaAsset.id.in_(asset_ids),
                    MediaAsset.tenant_id == tenant_id,
                )
            )
        )
        .scalars()
        .all()
    )
    by_id = {str(asset.id): asset for asset in assets}
    blockers = []
    for usage in usages:
        status = _asset_usage_status(by_id.get(usage["asset_id"]), usage["expected_sha256"])
        if status != "verified":
            blockers.append(
                f"Media asset {usage['asset_id']} for {usage['slug']} is {status}; "
                "replace it in a new draft"
            )
    return blockers


def _asset_usage_build_scope(build: SiteBuild, active_build: SiteBuild | None) -> str:
    if active_build is not None and build.id == active_build.id:
        return "published"
    if getattr(build, "first_published_at", None) or build.status in {"published", "rolled_back"}:
        return "historical"
    return "candidate"


@router.get("/{project_id}/asset-usage")
async def list_project_asset_usage(
    project_id: UUID,
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
    ),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    project = await _project_or_404(db, project_id, auth)
    drafts = list(
        (
            await db.execute(
                select(PageDraft).where(
                    PageDraft.project_id == project.id,
                    PageDraft.state.in_(("draft", "review")),
                )
            )
        )
        .scalars()
        .all()
    )
    builds = list(
        (
            await db.execute(
                select(SiteBuild).where(
                    SiteBuild.project_id == project.id,
                    SiteBuild.tenant_id == project.tenant_id,
                    SiteBuild.site_id == project.site_id,
                    SiteBuild.status == "ready",
                )
            )
        )
        .scalars()
        .all()
    )
    site = await _project_site_or_409(db, project) if project.site_id else None
    active_build = None
    if site and site.build_hash:
        active_build = (
            await db.execute(
                select(SiteBuild).where(
                    SiteBuild.project_id == project.id,
                    SiteBuild.tenant_id == project.tenant_id,
                    SiteBuild.site_id == site.id,
                    SiteBuild.build_hash == site.build_hash,
                )
            )
        ).scalar_one_or_none()
    usages = []
    for draft in drafts:
        try:
            manifest = SiteManifest.model_validate(
                {
                    "site_id": site.id if site else uuid4(),
                    "tenant_id": project.tenant_id,
                    "domain": project.domain or "draft.example.test",
                    "pages": [draft.page_manifest],
                }
            )
        except ValueError:
            continue
        usages.extend(
            _manifest_asset_usage(
                manifest=manifest,
                scope="draft",
                source={"draft_id": str(draft.id), "revision": draft.revision},
            )
        )
    usage_builds: dict[UUID, SiteBuild] = {build.id: build for build in builds}
    if active_build is not None:
        usage_builds[active_build.id] = active_build
    for build in usage_builds.values():
        try:
            manifest = SiteManifest.model_validate(build.manifest_snapshot)
        except ValueError:
            continue
        scope = _asset_usage_build_scope(build, active_build)
        usages.extend(
            _manifest_asset_usage(
                manifest=manifest,
                scope=scope,
                source={"build_id": str(build.id), "build_hash": build.build_hash},
            )
        )
    asset_ids = {UUID(item["asset_id"]) for item in usages}
    assets = (
        list(
            (
                await db.execute(
                    select(MediaAsset).where(
                        MediaAsset.id.in_(asset_ids),
                        MediaAsset.tenant_id == project.tenant_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if asset_ids
        else []
    )
    by_id = {str(asset.id): asset for asset in assets}
    return [
        {
            **item,
            "current_status": _asset_usage_status(
                by_id.get(item["asset_id"]), item["expected_sha256"]
            ),
        }
        for item in usages
    ]


async def _build_assets_for_manifest(
    db: AsyncSession,
    *,
    manifest: SiteManifest,
    tenant_id: UUID,
) -> list[BuildAsset]:
    expected_hashes: dict[UUID, str] = {}
    for page in manifest.pages:
        attachments = [*page.media, *page.block_media.values()]
        if page.author:
            attachments.append(page.author.portrait)
        for attachment in attachments:
            existing = expected_hashes.setdefault(attachment.asset_id, attachment.stored_sha256)
            if existing != attachment.stored_sha256:
                raise HTTPException(
                    status_code=422, detail="Media asset hash conflicts across pages"
                )
    if not expected_hashes:
        return []
    assets = list(
        (
            await db.execute(
                select(MediaAsset).where(
                    MediaAsset.id.in_(tuple(expected_hashes)),
                    MediaAsset.tenant_id == tenant_id,
                )
            )
        )
        .scalars()
        .all()
    )
    if len(assets) != len(expected_hashes):
        raise HTTPException(status_code=422, detail="A referenced media asset is unavailable")
    build_assets = []
    for asset in assets:
        try:
            stored_sha256 = _verified_media_hash(asset)
            if stored_sha256 != expected_hashes[asset.id]:
                raise ValueError("Media asset changed after draft review")
            await ensure_media_review_allows_use(
                db,
                tenant_id=tenant_id,
                asset_id=asset.id,
                stored_sha256=stored_sha256,
            )
            build_assets.append(
                BuildAsset(
                    asset_id=asset.id,
                    source_path=_asset_path(asset),
                    stored_sha256=stored_sha256,
                    content_type=asset.content_type,
                )
            )
        except (HTTPException, ValueError) as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            raise HTTPException(status_code=422, detail=f"Media asset blocker: {detail}") from exc
    return build_assets


def _selected_build_projection(
    build: SiteBuild, site: Site
) -> tuple[SiteManifest, dict[str, dict]]:
    try:
        manifest = SiteManifest.model_validate(build.manifest_snapshot)
    except ValueError as exc:
        raise ValueError("Selected build manifest snapshot is invalid") from exc
    if manifest.site_id != site.id or manifest.tenant_id != site.tenant_id:
        raise ValueError("Selected build manifest snapshot does not belong to this site")
    metadata = build.page_metadata_snapshot
    if metadata is None:
        raise ValueError("Selected build has no immutable page metadata snapshot")
    try:
        metadata_by_slug = validate_page_metadata_snapshot(
            metadata, expected_slugs={page.slug for page in manifest.pages}
        )
    except ValueError as exc:
        if str(exc) == "Build manifest and page metadata snapshots do not match":
            raise ValueError(
                "Selected build manifest and page metadata snapshots do not match"
            ) from exc
        raise ValueError("Selected build page metadata snapshot is invalid") from exc
    return manifest, metadata_by_slug


async def _candidate_index_states(
    db: AsyncSession,
    *,
    project: Project,
    site: Site,
    manifest: SiteManifest,
    rows: list[SitePage],
) -> tuple[
    dict[str, str],
    dict[str, str],
    dict[str, datetime | None],
    dict[str, PageIndexPromotion],
]:
    promotions = list(
        (
            await db.execute(
                select(PageIndexPromotion)
                .where(
                    PageIndexPromotion.project_id == project.id,
                    PageIndexPromotion.site_id == site.id,
                )
                .order_by(PageIndexPromotion.decided_at.desc())
            )
        )
        .scalars()
        .all()
    )
    approved = {(promotion.slug, promotion.source_hash): promotion for promotion in promotions}
    existing = {row.slug: row for row in rows}
    index_states: dict[str, str] = {}
    source_hashes: dict[str, str] = {}
    promoted_at: dict[str, datetime | None] = {}
    matched_promotions: dict[str, PageIndexPromotion] = {}
    for page in manifest.pages:
        source_hash = _draft_manifest_hash(page.model_dump(mode="json"))
        source_hashes[page.slug] = source_hash
        promotion = approved.get((page.slug, source_hash))
        previous = existing.get(page.slug)
        legacy_current = (
            previous is not None
            and previous.index_state == "indexed"
            and getattr(previous, "index_source_hash", None) is None
            and _draft_manifest_hash(previous.manifest or {}) == source_hash
        )
        current_promotion = (
            previous is not None
            and previous.index_state == "indexed"
            and getattr(previous, "index_source_hash", None) == source_hash
        )
        if promotion or legacy_current or current_promotion:
            index_states[page.slug] = "indexed"
            promoted_at[page.slug] = (
                promotion.decided_at if promotion else getattr(previous, "promoted_at", None)
            )
            if promotion:
                matched_promotions[page.slug] = promotion
        else:
            index_states[page.slug] = "noindex"
            promoted_at[page.slug] = None
    return index_states, source_hashes, promoted_at, matched_promotions


async def _manifest_author_compliance_blockers(
    db: AsyncSession, *, manifest: SiteManifest, project: Project
) -> list[str]:
    blockers = []
    for page in manifest.pages:
        if not page.author:
            continue
        try:
            snapshot = await _approved_author_snapshot_or_409(
                db,
                project=project,
                revision_id=page.author.revision_id,
            )
            expected = PageManifest.model_validate(
                {**page.model_dump(mode="json"), "author": snapshot}
            ).author
            if expected != page.author:
                blockers.append(
                    f"Author profile for {page.slug} no longer matches its approved revision"
                )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else "Author profile is unavailable"
            blockers.append(f"Author profile for {page.slug}: {detail}")
    return blockers


async def _freeze_candidate_build_input(
    db: AsyncSession, *, project: Project, site: Site
) -> tuple[dict, str]:
    """Capture every mutable content decision before the durable job is queued."""
    manifest = SiteManifest.model_validate(site.manifest)
    author_blockers = await _manifest_author_compliance_blockers(
        db,
        manifest=manifest,
        project=project,
    )
    if author_blockers:
        raise HTTPException(status_code=409, detail={"blockers": author_blockers})
    rows = list(
        (await db.execute(select(SitePage).where(SitePage.site_id == site.id))).scalars().all()
    )
    index_states, source_hashes, promoted_at, matched_promotions = await _candidate_index_states(
        db,
        project=project,
        site=site,
        manifest=manifest,
        rows=rows,
    )
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
    contacts = site.manifest.get("contacts") or {}
    snapshot = {
        "version": 1,
        "manifest": manifest.model_dump(mode="json"),
        "context": {
            "manifest_context": manifest.context or {},
            "phone": str(contacts.get("phone") or ""),
            "telemetry_token": telemetry_token(
                site_id=site.id,
                domain=str(getattr(site, "domain", None) or project.domain or manifest.domain),
            ),
            "captcha": captcha_public_config(),
        },
        "index_states": index_states,
        "source_hashes": source_hashes,
        "promoted_at": {
            slug: value.isoformat() if value else None for slug, value in promoted_at.items()
        },
        "index_promotions": {
            slug: _serialize_index_promotion(promotion)
            for slug, promotion in matched_promotions.items()
        },
        "page_plan_ids": plan_ids,
    }
    snapshot_hash = sha256_hex(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    return snapshot, snapshot_hash


def _page_metadata_from_candidate_result(result: dict, snapshot: dict) -> list[dict]:
    source_hashes = snapshot["source_hashes"]
    promoted_at = snapshot["promoted_at"]
    promotions = snapshot["index_promotions"]
    return [
        {
            **item,
            "source_hash": source_hashes[item["slug"]],
            "promoted_at": promoted_at[item["slug"]],
            **(
                {"index_promotion": promotions[item["slug"]]}
                if item["slug"] in promotions
                else {}
            ),
        }
        for item in result["pages"]
    ]


async def run_queued_candidate_build(db: AsyncSession, build_id: UUID) -> dict:
    """Run one already-frozen candidate build. It never activates or publishes a release."""
    build = (
        await db.execute(select(SiteBuild).where(SiteBuild.id == build_id).with_for_update())
    ).scalar_one_or_none()
    if build is None:
        return {"status": "missing", "build_id": str(build_id)}
    if build.status != "queued":
        return {"status": build.status, "build_id": str(build_id), "duplicate": True}
    if not build.input_snapshot or build.snapshot_version != 1 or build.project_id is None:
        build.status = "failed"
        build.failure_code = "snapshot_invalid"
        build.completed_at = datetime.now(UTC)
        build.lease_expires_at = None
        await append_site_build_event(
            db, build=build, event_type="failed", safe_code="snapshot_invalid"
        )
        await db.commit()
        return {"status": build.status, "build_id": str(build_id), "error_code": build.failure_code}

    snapshot = build.input_snapshot
    project = await db.get(Project, build.project_id)
    site = await db.get(Site, build.site_id)
    if (
        project is None
        or site is None
        or project.tenant_id != build.tenant_id
        or project.id != build.project_id
        or site.tenant_id != build.tenant_id
        or site.project_id not in {None, project.id}
    ):
        build.status = "failed"
        build.failure_code = "tenant_scope_violation"
        build.completed_at = datetime.now(UTC)
        build.lease_expires_at = None
        await append_site_build_event(
            db, build=build, event_type="failed", safe_code="tenant_scope_violation"
        )
        await db.commit()
        return {"status": build.status, "build_id": str(build_id), "error_code": build.failure_code}

    build.status = "running"
    build.attempt_count += 1
    build.started_at = datetime.now(UTC)
    build.completed_at = None
    build.failure_code = None
    build.lease_expires_at = build.started_at + timedelta(minutes=10)
    await append_site_build_event(db, build=build, event_type="started")
    await db.commit()

    try:
        manifest = SiteManifest.model_validate(snapshot["manifest"])
        if manifest.site_id != site.id or manifest.tenant_id != build.tenant_id:
            raise ValueError("snapshot_scope")
        assets = await _build_assets_for_manifest(
            db, manifest=manifest, tenant_id=build.tenant_id
        )
        frozen_context = snapshot["context"]
        context = {
            **dict(frozen_context["manifest_context"]),
            "phone": str(frozen_context["phone"]),
            # The token is never exposed from the frozen snapshot or list API.
            "lead_token": site.lead_token,
            "lead_api_url": "/api/v1/leads/public",
            "telemetry_token": str(frozen_context.get("telemetry_token") or ""),
            "captcha": dict(frozen_context.get("captcha") or {}),
        }
        started = time.perf_counter()
        result = await asyncio.to_thread(
            SiteBuilder(Path(settings.sites_root)).build,
            manifest,
            context,
            index_states=dict(snapshot["index_states"]),
            assets=assets,
            activate=False,
        )
        page_metadata_snapshot = _page_metadata_from_candidate_result(result, snapshot)
        build.build_hash = result["build_hash"]
        build.pages_built = len(result["pages"])
        build.duration_ms = int((time.perf_counter() - started) * 1000)
        build.log = f"candidate=true; indexed={result['indexed_count']}"
        build.manifest_snapshot = snapshot["manifest"]
        build.page_metadata_snapshot = page_metadata_snapshot
        build.page_plan_ids = snapshot["page_plan_ids"]
        build.status = "ready"
        build.completed_at = datetime.now(UTC)
        build.lease_expires_at = None
        await store_release_gate(db, build=build, site=site, actor_id=None)
        await append_site_build_event(db, build=build, event_type="ready")
        await append_audit(
            db,
            action="project.build.materialize",
            payload={
                "project_id": str(project.id),
                "build_id": str(build.id),
                "build_hash": build.build_hash,
                "activated": False,
            },
            tenant_id=build.tenant_id,
            actor_id=build.requested_by,
        )
        _record_release_event(db, tenant_id=build.tenant_id, outcome="success")
        await db.commit()
        record_release_transition(action="build", outcome="success")
        return {"status": build.status, "build_id": str(build_id), "build_hash": build.build_hash}
    except Exception:  # noqa: BLE001
        build.status = "failed"
        build.failure_code = "build_execution_failed"
        build.completed_at = datetime.now(UTC)
        build.lease_expires_at = None
        await append_site_build_event(
            db, build=build, event_type="failed", safe_code=build.failure_code
        )
        await append_audit(
            db,
            action="project.build.failed",
            payload={
                "project_id": str(build.project_id),
                "build_id": str(build.id),
                "code": build.failure_code,
            },
            tenant_id=build.tenant_id,
            actor_id=build.requested_by,
        )
        _record_release_event(db, tenant_id=build.tenant_id, outcome="failure")
        await db.commit()
        record_release_transition(action="build", outcome="failed")
        return {"status": build.status, "build_id": str(build_id), "error_code": build.failure_code}


async def _reconcile_site_page_projection(
    db: AsyncSession,
    *,
    site: Site,
    project: Project,
    manifest: SiteManifest,
    metadata_by_slug: dict[str, dict],
) -> None:
    existing = {
        page.slug: page
        for page in (await db.execute(select(SitePage).where(SitePage.site_id == site.id)))
        .scalars()
        .all()
    }
    selected_slugs = {page.slug for page in manifest.pages}
    for page_manifest in manifest.pages:
        page = existing.get(page_manifest.slug)
        if not page:
            page = SitePage(site_id=site.id, tenant_id=site.tenant_id, slug=page_manifest.slug)
            db.add(page)
        metadata = metadata_by_slug[page_manifest.slug]
        page.project_id = project.id
        page.page_plan_id = None
        page.page_draft_id = None
        page.manifest = page_manifest.model_dump(mode="json")
        page.publish_state = "published"
        page.index_state = metadata["index_state"]
        page.index_source_hash = (
            metadata.get("source_hash") if metadata["index_state"] == "indexed" else None
        )
        page.promoted_at = (
            datetime.fromisoformat(metadata["promoted_at"]) if metadata.get("promoted_at") else None
        )
        page.thin = metadata["thin"]
        page.content_chars = metadata["content_chars"]
    for slug, page in existing.items():
        if slug not in selected_slugs:
            page.publish_state = "archived"
            page.index_state = "noindex"
            page.index_source_hash = None
            page.promoted_at = None


@router.post("/{project_id}/builds", status_code=status.HTTP_201_CREATED)
async def materialize_project_build(
    project_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
    body: CandidateBuildRequest | None = None,
) -> dict:
    """Freeze a candidate; the bounded worker scheduler decides when it can build."""
    request = body or CandidateBuildRequest()
    project = await _project_or_404(db, project_id, auth)
    if not project.site_id:
        raise HTTPException(
            status_code=409, detail={"blockers": ["Apply a page draft before building"]}
        )
    site = await _project_site_or_409(db, project)
    snapshot, snapshot_hash = await _freeze_candidate_build_input(db, project=project, site=site)
    now = datetime.now(UTC)
    build = SiteBuild(
        site_id=site.id,
        tenant_id=site.tenant_id,
        project_id=project.id,
        status="queued",
        previous_build_hash=site.build_hash,
        input_snapshot=snapshot,
        input_snapshot_hash=snapshot_hash,
        snapshot_version=1,
        manifest_snapshot=snapshot["manifest"],
        page_plan_ids=snapshot["page_plan_ids"],
        requested_by=auth.user.id,
        queue_priority=request.queue_priority,
        not_before=request.not_before,
        last_enqueued_at=now,
    )
    db.add(build)
    await db.flush()
    await append_site_build_event(db, build=build, event_type="queued")
    await append_audit(
        db,
        action="project.build.queued",
        payload={
            "project_id": str(project.id),
            "build_id": str(build.id),
            "input_snapshot_hash": snapshot_hash,
            "queue_priority": request.queue_priority,
            "not_before": request.not_before.isoformat() if request.not_before else None,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()

    return {
        "id": str(build.id),
        "status": build.status,
        "input_snapshot_hash": snapshot_hash,
        "queue_priority": build.queue_priority,
        "not_before": build.not_before.isoformat() if build.not_before else None,
        "activated": False,
        "published": False,
    }


@router.post("/{project_id}/builds/{build_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_project_build(
    project_id: UUID,
    build_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    site = await _project_site_or_409(db, project)
    build = (
        await db.execute(
            select(SiteBuild)
            .where(
                SiteBuild.id == build_id,
                SiteBuild.project_id == project.id,
                SiteBuild.tenant_id == project.tenant_id,
                SiteBuild.site_id == site.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not build or build.status != "failed":
        raise HTTPException(status_code=409, detail="Select a failed candidate build")
    if not build.input_snapshot or build.snapshot_version != 1:
        raise HTTPException(status_code=409, detail="Legacy build has no retryable frozen snapshot")
    build.status = "queued"
    build.failure_code = None
    build.started_at = None
    build.completed_at = None
    build.lease_expires_at = None
    build.last_enqueued_at = datetime.now(UTC)
    build.not_before = build.last_enqueued_at
    await append_site_build_event(db, build=build, event_type="retry_requested")
    await append_audit(
        db,
        action="project.build.retry_queued",
        payload={"project_id": str(project.id), "build_id": str(build.id)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"id": str(build.id), "status": build.status, "retryable": False}


@router.post("/{project_id}/builds/{build_id}/legal-review")
async def review_project_build_legal(
    project_id: UUID,
    build_id: UUID,
    body: BuildLegalReviewIn,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    site = await _project_site_or_409(db, project)
    build = (
        await db.execute(
            select(SiteBuild).where(
                SiteBuild.id == build_id,
                SiteBuild.project_id == project.id,
                SiteBuild.tenant_id == project.tenant_id,
                SiteBuild.site_id == site.id,
            )
        )
    ).scalar_one_or_none()
    if not build or build.status != "ready":
        raise HTTPException(status_code=409, detail="Select a ready candidate build")
    review_state = legal_review_status(build)
    build.legal_review = {
        "state": body.decision,
        "legal_snapshot_hash": review_state["snapshot_hash"],
        "evidence_ref": body.evidence_ref.strip(),
        "reason": body.reason.strip() if body.reason else None,
        "replacement_guidance": (
            body.replacement_guidance.strip() if body.replacement_guidance else None
        ),
        "reviewed_at": datetime.now(UTC).isoformat(),
        "reviewed_by": str(auth.user.id),
    }
    updated = legal_review_status(build)
    await append_audit(
        db,
        action="project.build.legal_review",
        payload={
            "project_id": str(project.id),
            "build_id": str(build.id),
            "build_hash": build.build_hash,
            "legal_snapshot_hash": updated["snapshot_hash"],
            "decision": body.decision,
            "evidence_ref": body.evidence_ref.strip(),
            "reason": body.reason.strip() if body.reason else None,
            "replacement_guidance": (
                body.replacement_guidance.strip() if body.replacement_guidance else None
            ),
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return updated


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
    if not project.site_id:
        raise HTTPException(status_code=404, detail="Preview build not found")
    site = await _project_site_or_409(db, project)
    build = (
        await db.execute(
            select(SiteBuild).where(
                SiteBuild.id == build_id,
                SiteBuild.project_id == project.id,
                SiteBuild.tenant_id == project.tenant_id,
                SiteBuild.site_id == site.id,
            )
        )
    ).scalar_one_or_none()
    if not build or build.status != "ready" or not build.build_hash:
        raise HTTPException(status_code=404, detail="Preview build not found")
    root = (
        SiteBuilder(Path(settings.sites_root))
        .release_path(str(site.id), build.build_hash)
        .resolve()
    )
    relative = preview_path.strip("/")
    candidate = (root / relative).resolve() if relative else root / "index.html"
    target = candidate if candidate.is_file() else (candidate / "index.html").resolve()
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
    if (getattr(project, "domain_check_meta", None) or {}).get("dns_status") != "ok":
        raise HTTPException(
            status_code=409,
            detail={"blockers": ["Run a successful DNS check before publish"]},
        )
    site = await _project_site_or_409(db, project)
    build = (
        await db.execute(
            select(SiteBuild).where(
                SiteBuild.id == build_id,
                SiteBuild.project_id == project.id,
                SiteBuild.tenant_id == project.tenant_id,
                SiteBuild.site_id == site.id,
            )
        )
    ).scalar_one_or_none()
    if not build or build.status != "ready" or not build.build_hash:
        raise HTTPException(
            status_code=409, detail={"blockers": ["Select a ready candidate build"]}
        )
    try:
        manifest, metadata_by_slug = _selected_build_projection(build, site)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"blockers": [str(exc)]}) from exc
    gate_evaluation = evaluate_build_release_gate(build, site)
    legal_review = legal_review_status(build)
    routing_blockers = await _lead_routing_publish_blockers(db, site)
    media_blockers = await _manifest_media_compliance_blockers(
        db, manifest=manifest, tenant_id=project.tenant_id
    )
    blockers = [
        *gate_evaluation["blockers"],
        *legal_review["blockers"],
        *routing_blockers,
        *media_blockers,
    ]
    if blockers:
        record_release_transition(action="publish", outcome="blocked")
        raise HTTPException(status_code=409, detail={"blockers": blockers})
    old_hash = site.build_hash
    builder = SiteBuilder(Path(settings.sites_root))
    if not builder.activate(str(site.id), build.build_hash):
        raise HTTPException(status_code=409, detail="Candidate release is unavailable")
    caddy = await CaddyClient().upsert_site_vhost(
        site.domain, str(Path(settings.caddy_sites_root) / str(site.id) / "current")
    )
    if not caddy.get("ok"):
        restored = builder.restore_activation(str(site.id), build.build_hash, old_hash)
        if restored:
            detail = (
                "Caddy configuration failed; candidate activation reverted"
                if old_hash is None
                else "Caddy configuration failed; previous release restored"
            )
        else:
            detail = "Caddy configuration failed; release activation recovery is unverified"
        record_release_transition(action="publish", outcome="failed")
        raise HTTPException(status_code=503, detail=detail)
    activated_at = datetime.now(UTC)
    site.previous_build_hash = old_hash
    site.build_hash = build.build_hash
    site.publish_state = "published"
    build.first_published_at = getattr(build, "first_published_at", None) or activated_at
    build.activated_at = activated_at
    # Keep execution state ready: publication is a separate immutable-release fact.
    await _reconcile_site_page_projection(
        db,
        site=site,
        project=project,
        manifest=manifest,
        metadata_by_slug=metadata_by_slug,
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
    _record_release_event(db, tenant_id=project.tenant_id, outcome="success")
    await db.commit()
    record_release_transition(action="publish", outcome="success")
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
    if body.confirmation_text != f"ROLLBACK {body.build_hash}":
        raise HTTPException(status_code=400, detail="Exact rollback confirmation is required")
    project = await _project_or_404(db, project_id, auth)
    if not project.site_id or not project.domain:
        raise HTTPException(
            status_code=409, detail={"blockers": ["Project domain and site are required"]}
        )
    if (getattr(project, "domain_check_meta", None) or {}).get("dns_status") != "ok":
        raise HTTPException(
            status_code=409, detail={"blockers": ["Run a successful DNS check before rollback"]}
        )
    site = await _project_site_or_409(db, project)
    if site.build_hash == body.build_hash:
        raise HTTPException(status_code=409, detail="Selected build is already active")
    target = (
        await db.execute(
            select(SiteBuild).where(
                SiteBuild.project_id == project.id,
                SiteBuild.tenant_id == project.tenant_id,
                SiteBuild.site_id == site.id,
                SiteBuild.build_hash == body.build_hash,
                SiteBuild.status == "ready",
                SiteBuild.first_published_at.is_not(None),
            )
        )
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(
            status_code=404, detail="Previously published immutable build hash not found"
        )
    try:
        manifest, metadata_by_slug = _selected_build_projection(target, site)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"blockers": [str(exc)]}) from exc
    gate_evaluation = evaluate_build_release_gate(target, site)
    legal_review = legal_review_status(target)
    routing_blockers = await _lead_routing_publish_blockers(db, site)
    media_blockers = await _manifest_media_compliance_blockers(
        db, manifest=manifest, tenant_id=project.tenant_id
    )
    blockers = [
        *gate_evaluation["blockers"],
        *legal_review["blockers"],
        *routing_blockers,
        *media_blockers,
    ]
    if blockers:
        record_release_transition(action="rollback", outcome="blocked")
        raise HTTPException(status_code=409, detail={"blockers": blockers})
    old_hash = site.build_hash
    builder = SiteBuilder(Path(settings.sites_root))
    if not builder.activate(str(site.id), body.build_hash):
        raise HTTPException(status_code=409, detail="Rollback release is unavailable")
    caddy = await CaddyClient().upsert_site_vhost(
        site.domain, str(Path(settings.caddy_sites_root) / str(site.id) / "current")
    )
    if not caddy.get("ok"):
        restored = builder.restore_activation(str(site.id), body.build_hash, old_hash)
        if restored:
            detail = (
                "Caddy configuration failed; candidate activation reverted"
                if old_hash is None
                else "Caddy configuration failed; previous release restored"
            )
        else:
            detail = "Caddy configuration failed; release activation recovery is unverified"
        record_release_transition(action="rollback", outcome="failed")
        raise HTTPException(status_code=503, detail=detail)
    activated_at = datetime.now(UTC)
    site.previous_build_hash = old_hash
    site.build_hash = body.build_hash
    site.publish_state = "published"
    target.activated_at = activated_at
    # A rollback changes the active pointer, not the candidate execution state.
    await _reconcile_site_page_projection(
        db,
        site=site,
        project=project,
        manifest=manifest,
        metadata_by_slug=metadata_by_slug,
    )
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
    _record_release_event(db, tenant_id=project.tenant_id, outcome="success")
    await db.commit()
    record_release_transition(action="rollback", outcome="success")
    return {
        "project_id": str(project.id),
        "build_hash": body.build_hash,
        "previous_build_hash": old_hash,
    }
