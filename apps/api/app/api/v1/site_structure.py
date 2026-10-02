from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.api.v1.projects import _confirmed_facts, _project_or_404, _selection_snapshots
from app.db.session import get_db
from app.models import (
    AIRun,
    KnowledgeDoc,
    PagePlan,
    Project,
    ProjectFamilyMember,
    ProjectSemanticCollection,
    SiteStructureAIImport,
    SiteStructureRevision,
)
from app.schemas.ai import PageProposal
from app.schemas.site_structure import (
    SiteStructureAIImportCreate,
    SiteStructureAIImportOut,
    SiteStructureCityChildMaterializationOut,
    SiteStructureCityChildrenMaterializationOut,
    SiteStructureCityChildrenMaterializeCreate,
    SiteStructurePageIn,
    SiteStructureRevisionCreate,
    SiteStructureRevisionDecision,
    SiteStructureRevisionOut,
)
from app.services.audit import append_audit
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import ValidationError
from site_panel_blocks import list_kits
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
_READ = require_roles("superadmin", "tenant_admin", "manager", "editor")
_WRITE = require_roles("superadmin", "tenant_admin", "manager", "editor")
_REVIEW = require_roles("superadmin", "tenant_admin", "manager")


def _hash(value: dict) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _serialize(revision: SiteStructureRevision) -> SiteStructureRevisionOut:
    return SiteStructureRevisionOut(
        id=revision.id,
        project_id=revision.project_id,
        version=revision.version,
        state=revision.state,
        semantic_collection_id=revision.semantic_collection_id,
        evidence_ids=revision.evidence_ids or [],
        structure=revision.structure or {},
        structure_hash=revision.structure_hash,
        source_snapshot=revision.source_snapshot or {},
        source_snapshot_hash=revision.source_snapshot_hash,
        submitted_at=revision.submitted_at.isoformat() if revision.submitted_at else None,
        reviewed_at=revision.reviewed_at.isoformat() if revision.reviewed_at else None,
        decision_reason=revision.decision_reason,
        materialized_at=revision.materialized_at.isoformat() if revision.materialized_at else None,
        materialized_page_plan_ids=[
            UUID(item) for item in revision.materialized_page_plan_ids or []
        ],
        created_at=revision.created_at.isoformat() if revision.created_at else None,
    )


async def _revision_or_404(
    db: AsyncSession,
    *,
    project_id: UUID,
    revision_id: UUID,
    auth: AuthContext,
) -> SiteStructureRevision:
    revision = (
        await db.execute(
            select(SiteStructureRevision).where(
                SiteStructureRevision.id == revision_id,
                SiteStructureRevision.project_id == project_id,
                SiteStructureRevision.tenant_id == auth.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not revision:
        raise HTTPException(status_code=404, detail="Site structure revision not found")
    return revision


async def _validated_sources(
    db: AsyncSession,
    *,
    project_id: UUID,
    tenant_id: UUID,
    body: SiteStructureRevisionCreate,
) -> tuple[ProjectSemanticCollection, list[KnowledgeDoc]]:
    collection = (
        await db.execute(
            select(ProjectSemanticCollection).where(
                ProjectSemanticCollection.id == body.semantic_collection_id,
                ProjectSemanticCollection.project_id == project_id,
                ProjectSemanticCollection.tenant_id == tenant_id,
                ProjectSemanticCollection.state == "approved",
            )
        )
    ).scalar_one_or_none()
    if not collection:
        raise HTTPException(status_code=409, detail="Select an approved semantic collection")
    evidence = (
        list(
            (
                await db.execute(
                    select(KnowledgeDoc).where(
                        KnowledgeDoc.id.in_(body.evidence_ids),
                        KnowledgeDoc.project_id == project_id,
                        KnowledgeDoc.tenant_id == tenant_id,
                        KnowledgeDoc.state == "approved",
                        KnowledgeDoc.kind.in_(("competitor_evidence", "competitor_crawl_evidence")),
                    )
                )
            )
            .scalars()
            .all()
        )
        if body.evidence_ids
        else []
    )
    if len(evidence) != len(body.evidence_ids):
        raise HTTPException(status_code=409, detail="Select approved project competitor evidence")
    return collection, evidence


def _validate_catalog_pages(body: SiteStructureRevisionCreate) -> dict:
    catalogs = {item["key"]: set(item["blocks"]) for item in list_kits()}
    pages = [item.model_dump(mode="json") for item in body.pages]
    for page in pages:
        if page["kit_key"] not in catalogs:
            raise HTTPException(status_code=422, detail="Site structure references an unknown kit")
        if any(block not in catalogs[page["kit_key"]] for block in page["block_ids"]):
            raise HTTPException(
                status_code=422, detail="Site structure references an unknown block"
            )
    return {"pages": pages}


async def _create_draft_revision(
    db: AsyncSession,
    *,
    project_id: UUID,
    tenant_id: UUID,
    body: SiteStructureRevisionCreate,
    source_snapshot: dict | None = None,
) -> SiteStructureRevision:
    collection, evidence = await _validated_sources(
        db, project_id=project_id, tenant_id=tenant_id, body=body
    )
    structure = _validate_catalog_pages(body)
    current = (
        await db.execute(
            select(SiteStructureRevision)
            .where(SiteStructureRevision.project_id == project_id)
            .order_by(SiteStructureRevision.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if current and current.state in {"draft", "review"}:
        raise HTTPException(
            status_code=409, detail="Finish or reject the current structure revision first"
        )
    revision = SiteStructureRevision(
        project_id=project_id,
        tenant_id=tenant_id,
        supersedes_id=current.id if current else None,
        version=(current.version + 1) if current else 1,
        semantic_collection_id=collection.id,
        evidence_ids=[str(item.id) for item in evidence],
        structure=structure,
        structure_hash=_hash(structure),
        source_snapshot=source_snapshot or {},
        source_snapshot_hash=_hash(source_snapshot) if source_snapshot else None,
    )
    db.add(revision)
    await db.flush()
    return revision


def _ai_structure_body(
    body: SiteStructureAIImportCreate, run: AIRun
) -> tuple[SiteStructureRevisionCreate, str]:
    try:
        proposals = [PageProposal.model_validate(item) for item in run.output.get("pages", [])]
        pages = [
            SiteStructurePageIn(
                key=proposal.key,
                parent_key=proposal.parent_key,
                slug=proposal.slug,
                title=proposal.title,
                meta_description=proposal.meta_description,
                h1=proposal.h1,
                heading_outline=[item.model_dump() for item in proposal.heading_outline],
                objective=proposal.purpose,
                kit_key=proposal.kit_key,
                block_ids=proposal.block_ids,
                risk_notes="\n".join(proposal.uncertainty_notes) or None,
            )
            for proposal in proposals
        ]
        structure_body = SiteStructureRevisionCreate(
            semantic_collection_id=body.semantic_collection_id,
            evidence_ids=body.evidence_ids,
            pages=pages,
        )
    except (TypeError, ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="Approved AI output cannot be imported as a valid site structure",
        ) from exc
    output_hash = _hash({"pages": [proposal.model_dump(mode="json") for proposal in proposals]})
    return structure_body, output_hash


@router.get("/{project_id}/site-structure/revisions", response_model=list[SiteStructureRevisionOut])
async def list_structure_revisions(
    project_id: UUID,
    auth: AuthContext = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> list[SiteStructureRevisionOut]:
    project = await _project_or_404(db, project_id, auth)
    rows = list(
        (
            await db.execute(
                select(SiteStructureRevision)
                .where(
                    SiteStructureRevision.project_id == project.id,
                    SiteStructureRevision.tenant_id == project.tenant_id,
                )
                .order_by(SiteStructureRevision.version.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_serialize(item) for item in rows]


@router.post(
    "/{project_id}/site-structure/revisions",
    response_model=SiteStructureRevisionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_structure_revision(
    project_id: UUID,
    body: SiteStructureRevisionCreate,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> SiteStructureRevisionOut:
    project = await _project_or_404(db, project_id, auth)
    revision = await _create_draft_revision(
        db,
        project_id=project.id,
        tenant_id=project.tenant_id,
        body=body,
    )
    await append_audit(
        db,
        action="site_structure.create",
        payload={"project_id": str(project.id), "revision_id": str(revision.id)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize(revision)


@router.post(
    "/{project_id}/site-structure/revisions/import-approved-ai-run",
    response_model=SiteStructureAIImportOut,
)
async def import_approved_ai_run(
    project_id: UUID,
    body: SiteStructureAIImportCreate,
    response: Response,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> SiteStructureAIImportOut:
    project = await _project_or_404(db, project_id, auth)
    run = (
        await db.execute(
            select(AIRun)
            .where(
                AIRun.id == body.ai_run_id,
                AIRun.tenant_id == project.tenant_id,
                AIRun.project_id == project.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Approved architecture run not found")
    if run.action != "architecture.site-map":
        raise HTTPException(status_code=409, detail="AI run is not a site architecture proposal")
    if run.status != "approved" or run.operator_decision != "approve":
        raise HTTPException(status_code=409, detail="Approve the AI architecture proposal first")

    previous = (
        await db.execute(
            select(SiteStructureAIImport).where(
                SiteStructureAIImport.ai_run_id == run.id,
                SiteStructureAIImport.tenant_id == project.tenant_id,
                SiteStructureAIImport.project_id == project.id,
            )
        )
    ).scalar_one_or_none()
    if previous:
        revision = await _revision_or_404(
            db,
            project_id=project.id,
            revision_id=previous.site_structure_revision_id,
            auth=auth,
        )
        return SiteStructureAIImportOut(
            revision=_serialize(revision),
            imported=False,
            ai_run_id=run.id,
            source_output_hash=previous.output_hash,
        )

    structure_body, output_hash = _ai_structure_body(body, run)
    source_snapshot = {
        "ai_import": {
            "source_type": "ai.architecture.site-map",
            "ai_run_id": str(run.id),
            "prompt_id": run.prompt_id,
            "prompt_version": run.prompt_version,
            "prompt_hash": run.prompt_hash,
            "input_snapshot_hash": run.input_snapshot_hash,
            "output_hash": output_hash,
            "page_count": len(structure_body.pages),
        }
    }
    revision = await _create_draft_revision(
        db,
        project_id=project.id,
        tenant_id=project.tenant_id,
        body=structure_body,
        source_snapshot=source_snapshot,
    )
    db.add(
        SiteStructureAIImport(
            project_id=project.id,
            tenant_id=project.tenant_id,
            ai_run_id=run.id,
            site_structure_revision_id=revision.id,
            input_snapshot_hash=run.input_snapshot_hash,
            output_hash=output_hash,
            created_by=auth.user.id,
        )
    )
    await db.flush()
    await append_audit(
        db,
        action="site_structure.ai_architecture.imported",
        payload={
            "project_id": str(project.id),
            "revision_id": str(revision.id),
            "ai_run_id": str(run.id),
            "input_snapshot_hash": run.input_snapshot_hash,
            "output_hash": output_hash,
            "page_count": len(structure_body.pages),
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    response.status_code = status.HTTP_201_CREATED
    return SiteStructureAIImportOut(
        revision=_serialize(revision),
        imported=True,
        ai_run_id=run.id,
        source_output_hash=output_hash,
    )


@router.post(
    "/{project_id}/site-structure/revisions/{revision_id}/submit-review",
    response_model=SiteStructureRevisionOut,
)
async def submit_structure_revision(
    project_id: UUID,
    revision_id: UUID,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> SiteStructureRevisionOut:
    project = await _project_or_404(db, project_id, auth)
    revision = await _revision_or_404(db, project_id=project_id, revision_id=revision_id, auth=auth)
    if revision.state != "draft":
        raise HTTPException(status_code=409, detail="Only draft site structures can be submitted")
    facts = await _confirmed_facts(db, project)
    keywords, geo, blockers = await _selection_snapshots(db, project)
    if blockers:
        raise HTTPException(status_code=409, detail={"blockers": blockers})
    collection = await db.get(ProjectSemanticCollection, revision.semantic_collection_id)
    if not collection or collection.state != "approved":
        raise HTTPException(
            status_code=409, detail="The structure semantic collection is no longer approved"
        )
    source_snapshot = {
        "fact_revision_id": str(facts.id),
        "facts_hash": facts.facts_hash,
        "semantic_collection_id": str(collection.id),
        "semantic_collection_version": collection.version,
        "keyword_snapshot": keywords,
        "geo_snapshot": geo,
        "evidence_ids": revision.evidence_ids or [],
        "structure_hash": revision.structure_hash,
        **(revision.source_snapshot or {}),
    }
    revision.source_snapshot = source_snapshot
    revision.source_snapshot_hash = _hash(source_snapshot)
    revision.state = "review"
    revision.submitted_at = datetime.now(UTC)
    await append_audit(
        db,
        action="site_structure.submit_review",
        payload={"project_id": str(project.id), "revision_id": str(revision.id)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize(revision)


@router.post(
    "/{project_id}/site-structure/revisions/{revision_id}/approve",
    response_model=SiteStructureRevisionOut,
)
async def approve_structure_revision(
    project_id: UUID,
    revision_id: UUID,
    body: SiteStructureRevisionDecision,
    auth: AuthContext = Depends(_REVIEW),
    db: AsyncSession = Depends(get_db),
) -> SiteStructureRevisionOut:
    project = await _project_or_404(db, project_id, auth)
    revision = await _revision_or_404(db, project_id=project_id, revision_id=revision_id, auth=auth)
    if revision.state != "review":
        raise HTTPException(status_code=409, detail="Only structures under review can be approved")
    revision.state = "approved"
    revision.reviewed_at = datetime.now(UTC)
    revision.reviewed_by = auth.user.id
    revision.decision_reason = body.reason.strip() if body.reason else None
    await append_audit(
        db,
        action="site_structure.approve",
        payload={"project_id": str(project.id), "revision_id": str(revision.id)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize(revision)


@router.post(
    "/{project_id}/site-structure/revisions/{revision_id}/reject",
    response_model=SiteStructureRevisionOut,
)
async def reject_structure_revision(
    project_id: UUID,
    revision_id: UUID,
    body: SiteStructureRevisionDecision,
    auth: AuthContext = Depends(_REVIEW),
    db: AsyncSession = Depends(get_db),
) -> SiteStructureRevisionOut:
    if not body.reason or not body.reason.strip():
        raise HTTPException(status_code=400, detail="Provide a rejection reason")
    project = await _project_or_404(db, project_id, auth)
    revision = await _revision_or_404(db, project_id=project_id, revision_id=revision_id, auth=auth)
    if revision.state != "review":
        raise HTTPException(status_code=409, detail="Only structures under review can be rejected")
    revision.state = "rejected"
    revision.reviewed_at = datetime.now(UTC)
    revision.reviewed_by = auth.user.id
    revision.decision_reason = body.reason.strip()
    await append_audit(
        db,
        action="site_structure.reject",
        payload={"project_id": str(project.id), "revision_id": str(revision.id)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize(revision)


def _page_plan_from_structure(
    *,
    revision: SiteStructureRevision,
    page: dict,
    project: Project,
    existing: PagePlan | None,
    family_materialization: dict | None = None,
) -> PagePlan:
    source_refs = {
        "site_structure_revision_id": str(revision.id),
        "site_structure_version": revision.version,
        "page_key": page["key"],
        "parent_key": page.get("parent_key"),
        "seo_blueprint": {
            "title": page["title"],
            "meta_description": page.get("meta_description", ""),
            "h1": page.get("h1", ""),
            "heading_outline": page.get("heading_outline", []),
        },
    }
    if family_materialization:
        source_refs["family_materialization"] = family_materialization
    return PagePlan(
        project_id=project.id,
        tenant_id=project.tenant_id,
        supersedes_id=existing.id if existing else None,
        version=(existing.version + 1) if existing else 1,
        state="draft",
        keyword_snapshot={},
        geo_snapshot={},
        semantic_target_snapshot={},
        slug=page["slug"],
        objective=page["objective"],
        intent=page.get("intent"),
        risk_notes=page.get("risk_notes"),
        kit_key=page["kit_key"],
        block_selection={"blocks": page.get("block_ids", []), "claim_slot_bindings": []},
        source_refs=source_refs,
    )


@router.post(
    "/{project_id}/site-structure/revisions/{revision_id}/materialize",
    response_model=SiteStructureRevisionOut,
)
async def materialize_structure_revision(
    project_id: UUID,
    revision_id: UUID,
    auth: AuthContext = Depends(_REVIEW),
    db: AsyncSession = Depends(get_db),
) -> SiteStructureRevisionOut:
    project = await _project_or_404(db, project_id, auth)
    revision = await _revision_or_404(db, project_id=project_id, revision_id=revision_id, auth=auth)
    if revision.state != "approved":
        raise HTTPException(
            status_code=409, detail="Approve the site structure before materialization"
        )
    if revision.materialized_page_plan_ids:
        raise HTTPException(
            status_code=409, detail="This site structure has already been materialized"
        )
    plans: list[PagePlan] = []
    for page in revision.structure.get("pages", []):
        slug = page["slug"]
        existing = (
            await db.execute(
                select(PagePlan)
                .where(PagePlan.project_id == project.id, PagePlan.slug == slug)
                .order_by(PagePlan.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing and existing.state in {"draft", "review"}:
            raise HTTPException(status_code=409, detail=f"Finish draft PagePlan {slug} first")
        plans.append(
            _page_plan_from_structure(
                revision=revision,
                page=page,
                project=project,
                existing=existing,
            )
        )
    db.add_all(plans)
    await db.flush()
    revision.materialized_at = datetime.now(UTC)
    revision.materialized_page_plan_ids = [str(item.id) for item in plans]
    await append_audit(
        db,
        action="site_structure.materialize",
        payload={
            "project_id": str(project.id),
            "revision_id": str(revision.id),
            "page_plan_count": len(plans),
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize(revision)


@router.post(
    "/{project_id}/site-structure/revisions/{revision_id}/materialize-city-children",
    response_model=SiteStructureCityChildrenMaterializationOut,
)
async def materialize_structure_city_children(
    project_id: UUID,
    revision_id: UUID,
    body: SiteStructureCityChildrenMaterializeCreate,
    auth: AuthContext = Depends(_REVIEW),
    db: AsyncSession = Depends(get_db),
) -> SiteStructureCityChildrenMaterializationOut:
    master = await _project_or_404(db, project_id, auth)
    revision = await _revision_or_404(db, project_id=master.id, revision_id=revision_id, auth=auth)
    if revision.state != "approved":
        raise HTTPException(
            status_code=409, detail="Approve the site structure before city materialization"
        )

    member_rows = list(
        (
            await db.execute(
                select(ProjectFamilyMember)
                .where(
                    ProjectFamilyMember.master_project_id == master.id,
                    ProjectFamilyMember.tenant_id == master.tenant_id,
                    ProjectFamilyMember.child_project_id.in_(body.child_project_ids),
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if len(member_rows) != len(body.child_project_ids):
        raise HTTPException(
            status_code=409,
            detail="Select only city children of this master project",
        )
    members = {member.child_project_id: member for member in member_rows}
    children = list(
        (
            await db.execute(
                select(Project)
                .where(
                    Project.id.in_(body.child_project_ids),
                    Project.tenant_id == master.tenant_id,
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if len(children) != len(body.child_project_ids):
        raise HTTPException(status_code=409, detail="Selected city project is unavailable")
    child_by_id = {child.id: child for child in children}
    pages = revision.structure.get("pages", [])
    preflight: dict[UUID, dict[str, PagePlan | None]] = {}
    for child_id in body.child_project_ids:
        child = child_by_id[child_id]
        member = members[child_id]
        existing_plans = list(
            (
                await db.execute(
                    select(PagePlan)
                    .where(PagePlan.project_id == child.id)
                    .order_by(PagePlan.slug, PagePlan.version.desc())
                )
            )
            .scalars()
            .all()
        )
        if any(
            (plan.source_refs or {})
            .get("family_materialization", {})
            .get("site_structure_revision_id")
            == str(revision.id)
            for plan in existing_plans
        ):
            raise HTTPException(
                status_code=409,
                detail="This structure is already materialized in a selected city project",
            )
        latest_by_slug: dict[str, PagePlan] = {}
        for plan in existing_plans:
            latest_by_slug.setdefault(plan.slug, plan)
        for page in pages:
            existing = latest_by_slug.get(page["slug"])
            if existing and existing.state in {"draft", "review"}:
                raise HTTPException(
                    status_code=409,
                    detail=f"Finish draft PagePlan {page['slug']} in city project first",
                )
        preflight[child_id] = latest_by_slug

    results: list[SiteStructureCityChildMaterializationOut] = []
    all_plans: list[PagePlan] = []
    for child_id in body.child_project_ids:
        child = child_by_id[child_id]
        member = members[child_id]
        latest_by_slug = preflight[child_id]
        child_plans: list[PagePlan] = []
        for page in pages:
            existing = latest_by_slug.get(page["slug"])
            plan = _page_plan_from_structure(
                revision=revision,
                page=page,
                project=child,
                existing=existing,
                family_materialization={
                    "site_structure_revision_id": str(revision.id),
                    "master_project_id": str(master.id),
                    "child_project_id": str(child.id),
                    "project_family_member_id": str(member.id),
                    "source_structure_hash": revision.structure_hash,
                },
            )
            child_plans.append(plan)
            latest_by_slug[page["slug"]] = plan
        db.add_all(child_plans)
        all_plans.extend(child_plans)
        results.append(
            SiteStructureCityChildMaterializationOut(
                child_project_id=child.id,
                project_family_member_id=member.id,
                page_plan_ids=[],
            )
        )
    await db.flush()
    for result, child_id in zip(results, body.child_project_ids, strict=True):
        result.page_plan_ids = [plan.id for plan in all_plans if plan.project_id == child_id]
    await append_audit(
        db,
        action="site_structure.city_children.materialize",
        payload={
            "master_project_id": str(master.id),
            "revision_id": str(revision.id),
            "structure_hash": revision.structure_hash,
            "children": [
                {
                    "child_project_id": str(item.child_project_id),
                    "project_family_member_id": str(item.project_family_member_id),
                    "page_plan_ids": [str(plan_id) for plan_id in item.page_plan_ids],
                }
                for item in results
            ],
        },
        tenant_id=master.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return SiteStructureCityChildrenMaterializationOut(
        revision_id=revision.id,
        master_project_id=master.id,
        structure_hash=revision.structure_hash,
        children=results,
    )
