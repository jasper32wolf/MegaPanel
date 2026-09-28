from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.api.v1.projects import _project_or_404
from app.db.session import get_db
from app.models import (
    KnowledgeDoc,
    PagePlan,
    ProjectGeoPlace,
    ProjectKeyword,
    ProjectSemanticCollection,
    ProjectSemanticCollectionKeyword,
    ProjectSemanticKeywordGeoBinding,
)
from app.schemas.workflow import (
    SemanticCollectionCreate,
    SemanticCollectionDecision,
    SemanticCollectionUpdate,
)
from app.services.audit import append_audit
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
_READ = require_roles("superadmin", "tenant_admin", "manager", "editor")
_WRITE = require_roles("superadmin", "tenant_admin", "manager", "editor")
_REVIEW = require_roles("superadmin", "tenant_admin", "manager")


def _collection_out(collection: ProjectSemanticCollection, members: list[dict]) -> dict:
    return {
        "id": str(collection.id),
        "project_id": str(collection.project_id),
        "name": collection.name,
        "description": collection.description,
        "state": collection.state,
        "version": collection.version,
        "source_refs": collection.source_refs or {},
        "members": members,
        "submitted_at": collection.submitted_at.isoformat() if collection.submitted_at else None,
        "reviewed_at": collection.reviewed_at.isoformat() if collection.reviewed_at else None,
        "decision_reason": collection.decision_reason,
    }


async def _collection_or_404(
    db: AsyncSession, project_id: UUID, collection_id: UUID, auth: AuthContext
):
    project = await _project_or_404(db, project_id, auth)
    collection = (
        await db.execute(
            select(ProjectSemanticCollection).where(
                ProjectSemanticCollection.id == collection_id,
                ProjectSemanticCollection.project_id == project.id,
                ProjectSemanticCollection.tenant_id == project.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not collection:
        raise HTTPException(status_code=404, detail="Semantic collection not found")
    return project, collection


async def _member_rows(db: AsyncSession, collection_id: UUID) -> list[dict]:
    rows = (
        await db.execute(
            select(ProjectSemanticCollectionKeyword, ProjectKeyword)
            .join(
                ProjectKeyword,
                ProjectKeyword.id == ProjectSemanticCollectionKeyword.project_keyword_id,
            )
            .where(ProjectSemanticCollectionKeyword.collection_id == collection_id)
            .order_by(ProjectSemanticCollectionKeyword.created_at)
        )
    ).all()
    member_ids = [row[0].id for row in rows]
    bindings = (
        (
            await db.execute(
                select(ProjectSemanticKeywordGeoBinding).where(
                    ProjectSemanticKeywordGeoBinding.collection_keyword_id.in_(member_ids)
                )
            )
        )
        .scalars()
        .all()
        if member_ids
        else []
    )
    by_member: dict[UUID, list[dict]] = {}
    for binding in bindings:
        by_member.setdefault(binding.collection_keyword_id, []).append(
            {
                "id": str(binding.id),
                "project_geo_place_id": str(binding.project_geo_place_id),
                "scope": binding.scope,
                "notes": binding.notes,
            }
        )
    return [
        {
            "id": str(member.id),
            "project_keyword_id": str(member.project_keyword_id),
            "keyword_id": str(project_keyword.keyword_id),
            "cluster": member.cluster,
            "intent": member.intent,
            "priority": member.priority,
            "notes": member.notes,
            "geo_bindings": by_member.get(member.id, []),
        }
        for member, project_keyword in rows
    ]


async def _validate_input(
    db: AsyncSession, project_id: UUID, tenant_id: UUID, body: SemanticCollectionCreate
) -> list[dict]:
    member_ids = {item.project_keyword_id for item in body.members}
    project_keywords = (
        list(
            (
                await db.execute(
                    select(ProjectKeyword).where(
                        ProjectKeyword.id.in_(member_ids),
                        ProjectKeyword.project_id == project_id,
                        ProjectKeyword.tenant_id == tenant_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if member_ids
        else []
    )
    if len(project_keywords) != len(member_ids):
        raise HTTPException(
            status_code=400, detail="Collection keywords must be selected for this project"
        )
    geo_ids = {geo.project_geo_place_id for item in body.members for geo in item.geo_bindings}
    project_geos = (
        list(
            (
                await db.execute(
                    select(ProjectGeoPlace).where(
                        ProjectGeoPlace.id.in_(geo_ids),
                        ProjectGeoPlace.project_id == project_id,
                        ProjectGeoPlace.tenant_id == tenant_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if geo_ids
        else []
    )
    if len(project_geos) != len(geo_ids):
        raise HTTPException(
            status_code=400, detail="Semantic geography must be selected for this project"
        )
    if any(geo.role == "reference" for geo in project_geos):
        raise HTTPException(
            status_code=400, detail="Reference geography cannot be a semantic target"
        )
    evidence_ids = set(body.evidence_ids)
    evidence = (
        list(
            (
                await db.execute(
                    select(KnowledgeDoc).where(
                        KnowledgeDoc.id.in_(evidence_ids),
                        KnowledgeDoc.project_id == project_id,
                        KnowledgeDoc.tenant_id == tenant_id,
                        KnowledgeDoc.kind == "competitor_evidence",
                        KnowledgeDoc.state == "approved",
                    )
                )
            )
            .scalars()
            .all()
        )
        if evidence_ids
        else []
    )
    if len(evidence) != len(evidence_ids):
        raise HTTPException(status_code=400, detail="Evidence must be approved for this project")
    return [{"evidence_id": str(item.id), "reference_only": True} for item in evidence]


async def _replace_members(
    db: AsyncSession, collection: ProjectSemanticCollection, body: SemanticCollectionCreate
) -> None:
    await db.execute(
        delete(ProjectSemanticCollectionKeyword).where(
            ProjectSemanticCollectionKeyword.collection_id == collection.id
        )
    )
    for item in body.members:
        member = ProjectSemanticCollectionKeyword(
            collection_id=collection.id,
            project_keyword_id=item.project_keyword_id,
            project_id=collection.project_id,
            tenant_id=collection.tenant_id,
            cluster=item.cluster.strip() if item.cluster else None,
            intent=item.intent.strip() if item.intent else None,
            priority=item.priority,
            notes=item.notes.strip() if item.notes else None,
        )
        db.add(member)
        await db.flush()
        for geo in item.geo_bindings:
            db.add(
                ProjectSemanticKeywordGeoBinding(
                    collection_keyword_id=member.id,
                    project_geo_place_id=geo.project_geo_place_id,
                    project_id=collection.project_id,
                    tenant_id=collection.tenant_id,
                    scope=geo.scope,
                    notes=geo.notes.strip() if geo.notes else None,
                )
            )


@router.get("/{project_id}/semantic-collections")
async def list_collections(
    project_id: UUID, auth: AuthContext = Depends(_READ), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    project = await _project_or_404(db, project_id, auth)
    collections = list(
        (
            await db.execute(
                select(ProjectSemanticCollection)
                .where(
                    ProjectSemanticCollection.project_id == project.id,
                    ProjectSemanticCollection.tenant_id == project.tenant_id,
                )
                .order_by(ProjectSemanticCollection.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [_collection_out(item, await _member_rows(db, item.id)) for item in collections]


@router.post("/{project_id}/semantic-collections", status_code=status.HTTP_201_CREATED)
async def create_collection(
    project_id: UUID,
    body: SemanticCollectionCreate,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    evidence_refs = await _validate_input(db, project.id, project.tenant_id, body)
    collection = ProjectSemanticCollection(
        project_id=project.id,
        tenant_id=project.tenant_id,
        name=body.name.strip(),
        description=body.description.strip() if body.description else None,
        source_refs={"evidence": evidence_refs},
    )
    db.add(collection)
    await db.flush()
    await _replace_members(db, collection, body)
    await append_audit(
        db,
        action="semantic.collection.create",
        payload={"project_id": str(project.id), "collection_id": str(collection.id)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _collection_out(collection, await _member_rows(db, collection.id))


@router.patch("/{project_id}/semantic-collections/{collection_id}")
async def update_collection(
    project_id: UUID,
    collection_id: UUID,
    body: SemanticCollectionUpdate,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project, collection = await _collection_or_404(db, project_id, collection_id, auth)
    if collection.state != "draft" or body.version != collection.version:
        raise HTTPException(
            status_code=409, detail="Only the current draft collection can be changed"
        )
    evidence_refs = await _validate_input(db, project.id, project.tenant_id, body)
    collection.name = body.name.strip()
    collection.description = body.description.strip() if body.description else None
    collection.source_refs = {"evidence": evidence_refs}
    collection.version += 1
    await _replace_members(db, collection, body)
    await append_audit(
        db,
        action="semantic.collection.update",
        payload={
            "project_id": str(project.id),
            "collection_id": str(collection.id),
            "version": collection.version,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _collection_out(collection, await _member_rows(db, collection.id))


@router.post("/{project_id}/semantic-collections/{collection_id}/submit-review")
async def submit_collection(
    project_id: UUID,
    collection_id: UUID,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project, collection = await _collection_or_404(db, project_id, collection_id, auth)
    if collection.state != "draft":
        raise HTTPException(status_code=409, detail="Only draft collections can be submitted")
    if not (await _member_rows(db, collection.id)):
        raise HTTPException(status_code=409, detail="Add at least one keyword to the collection")
    collection.state = "review"
    collection.submitted_at = datetime.now(UTC)
    collection.version += 1
    await append_audit(
        db,
        action="semantic.collection.submit_review",
        payload={"project_id": str(project.id), "collection_id": str(collection.id)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _collection_out(collection, await _member_rows(db, collection.id))


@router.post("/{project_id}/semantic-collections/{collection_id}/approve")
async def approve_collection(
    project_id: UUID,
    collection_id: UUID,
    body: SemanticCollectionDecision,
    auth: AuthContext = Depends(_REVIEW),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project, collection = await _collection_or_404(db, project_id, collection_id, auth)
    if collection.state != "review":
        raise HTTPException(status_code=409, detail="Only collections under review can be approved")
    collection.state = "approved"
    collection.reviewed_at = datetime.now(UTC)
    collection.reviewed_by = auth.user.id
    collection.decision_reason = body.reason.strip() if body.reason else None
    await append_audit(
        db,
        action="semantic.collection.approve",
        payload={"project_id": str(project.id), "collection_id": str(collection.id)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _collection_out(collection, await _member_rows(db, collection.id))


@router.post("/{project_id}/semantic-collections/{collection_id}/reject")
async def reject_collection(
    project_id: UUID,
    collection_id: UUID,
    body: SemanticCollectionDecision,
    auth: AuthContext = Depends(_REVIEW),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not body.reason or not body.reason.strip():
        raise HTTPException(status_code=400, detail="Provide a rejection reason")
    project, collection = await _collection_or_404(db, project_id, collection_id, auth)
    if collection.state != "review":
        raise HTTPException(status_code=409, detail="Only collections under review can be rejected")
    collection.state = "rejected"
    collection.reviewed_at = datetime.now(UTC)
    collection.reviewed_by = auth.user.id
    collection.decision_reason = body.reason.strip()
    await append_audit(
        db,
        action="semantic.collection.reject",
        payload={"project_id": str(project.id), "collection_id": str(collection.id)},
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _collection_out(collection, await _member_rows(db, collection.id))


@router.get("/{project_id}/semantic-signals")
async def semantic_signals(
    project_id: UUID,
    collection_id: UUID | None = None,
    auth: AuthContext = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    collections_query = select(ProjectSemanticCollection).where(
        ProjectSemanticCollection.project_id == project.id,
        ProjectSemanticCollection.tenant_id == project.tenant_id,
        ProjectSemanticCollection.state == "approved",
    )
    if collection_id:
        collections_query = collections_query.where(ProjectSemanticCollection.id == collection_id)
    collections = list((await db.execute(collections_query)).scalars().all())
    if collection_id and not collections:
        raise HTTPException(status_code=404, detail="Approved semantic collection not found")
    members = []
    for collection in collections:
        rows = await _member_rows(db, collection.id)
        for member in rows:
            members.append(
                {"collection_id": str(collection.id), "collection_name": collection.name, **member}
            )
    plans = list(
        (await db.execute(select(PagePlan).where(PagePlan.project_id == project.id)))
        .scalars()
        .all()
    )
    targets: dict[tuple[str, str], list[dict]] = {}
    unmapped_plans = []
    for plan in plans:
        snapshot = plan.semantic_target_snapshot or {}
        items = snapshot.get("targets", []) if isinstance(snapshot, dict) else []
        if not items and plan.state != "rejected":
            unmapped_plans.append({"plan_id": str(plan.id), "slug": plan.slug, "state": plan.state})
        for item in items:
            if not isinstance(item, dict):
                continue
            for geo_id in item.get("geo_binding_ids", []):
                key = (str(item.get("collection_keyword_id")), str(geo_id))
                targets.setdefault(key, []).append(
                    {"plan_id": str(plan.id), "slug": plan.slug, "state": plan.state}
                )
    coverage = []
    for member in members:
        bindings = member.get("geo_bindings", [])
        if not bindings:
            coverage.append(
                {
                    "collection_keyword_id": member["id"],
                    "project_keyword_id": member["project_keyword_id"],
                    "keyword_id": member["keyword_id"],
                    "status": "unbound",
                    "geo_binding_id": None,
                }
            )
        for binding in bindings:
            key = (member["id"], binding["id"])
            linked = targets.get(key, [])
            active = [item for item in linked if item["state"] != "rejected"]
            coverage.append(
                {
                    "collection_keyword_id": member["id"],
                    "project_keyword_id": member["project_keyword_id"],
                    "keyword_id": member["keyword_id"],
                    "geo_binding_id": binding["id"],
                    "status": "covered" if active else "uncovered",
                    "plans": linked,
                }
            )
    collisions = []
    for key, linked in targets.items():
        active = [item for item in linked if item["state"] != "rejected"]
        if len(active) > 1:
            collisions.append(
                {
                    "target": {"collection_keyword_id": key[0], "geo_binding_id": key[1]},
                    "plans": active,
                    "reason": "Multiple non-rejected plans target the same keyword and geography",
                }
            )
    return {
        "collections": [
            {"id": str(item.id), "name": item.name, "version": item.version} for item in collections
        ],
        "totals": {
            "members": len(members),
            "bindings": sum(len(item.get("geo_bindings", [])) for item in members),
            "covered": sum(item["status"] == "covered" for item in coverage),
            "uncovered": sum(item["status"] == "uncovered" for item in coverage),
            "unbound": sum(item["status"] == "unbound" for item in coverage),
        },
        "coverage": coverage,
        "cannibalization": collisions,
        "unmapped_plans": unmapped_plans,
    }
