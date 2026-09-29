from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.api.v1.sites import validate_webhook_target
from app.db.session import get_db
from app.models import Site
from app.models.leads import LeadRoutingDestination, LeadRoutingPolicy
from app.models.project import Project
from app.schemas.lead_routing import LeadRoutingDecisionIn, LeadRoutingPolicyCreate
from app.services.audit import append_audit
from app.services.leads import get_encryptor
from app.services.webhook_delivery import policy_hash
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


async def _project_site(
    db: AsyncSession, project_id: UUID, auth: AuthContext
) -> tuple[Project, Site]:
    statement = select(Project).where(Project.id == project_id, Project.archived_at.is_(None))
    if auth.role != "superadmin":
        statement = statement.where(Project.tenant_id == auth.tenant_id)
    project = (await db.execute(statement)).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.site_id:
        raise HTTPException(status_code=409, detail="Create the project site before lead routing")
    site = (
        await db.execute(
            select(Site).where(Site.id == project.site_id, Site.tenant_id == project.tenant_id)
        )
    ).scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=409, detail="Project site is unavailable")
    return project, site


def _serialize_destination(destination: LeadRoutingDestination) -> dict:
    return {
        "id": str(destination.id),
        "target_key": destination.target_key,
        "channel": destination.channel,
        "required": destination.required,
        "configured": bool(
            destination.target_recipient_enc
            if destination.channel == "email"
            else destination.target_url and destination.target_secret_enc
        ),
    }


def _serialize_policy(
    policy: LeadRoutingPolicy, destinations: list[LeadRoutingDestination]
) -> dict:
    return {
        "id": str(policy.id),
        "version": policy.version,
        "state": policy.state,
        "destinations": [_serialize_destination(destination) for destination in destinations],
        "submitted_at": policy.submitted_at.isoformat() if policy.submitted_at else None,
        "reviewed_at": policy.reviewed_at.isoformat() if policy.reviewed_at else None,
        "decision_reason": policy.decision_reason,
        "created_at": policy.created_at.isoformat() if policy.created_at else None,
    }


async def _policy_destinations(
    db: AsyncSession, policy: LeadRoutingPolicy
) -> list[LeadRoutingDestination]:
    return list(
        (
            await db.execute(
                select(LeadRoutingDestination)
                .where(LeadRoutingDestination.policy_id == policy.id)
                .order_by(LeadRoutingDestination.target_key)
            )
        )
        .scalars()
        .all()
    )


@router.get("/{project_id}/lead-routing")
async def list_lead_routing_policies(
    project_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project, _site = await _project_site(db, project_id, auth)
    policies = list(
        (
            await db.execute(
                select(LeadRoutingPolicy)
                .where(LeadRoutingPolicy.project_id == project.id)
                .order_by(LeadRoutingPolicy.version.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            _serialize_policy(policy, await _policy_destinations(db, policy)) for policy in policies
        ]
    }


@router.post("/{project_id}/lead-routing", status_code=status.HTTP_201_CREATED)
async def create_lead_routing_policy(
    project_id: UUID,
    body: LeadRoutingPolicyCreate,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project, site = await _project_site(db, project_id, auth)
    next_version = (
        await db.scalar(
            select(func.coalesce(func.max(LeadRoutingPolicy.version), 0)).where(
                LeadRoutingPolicy.project_id == project.id
            )
        )
    ) + 1
    source_destinations = []
    for destination in body.destinations:
        source_destinations.append(
            {
                "target_key": destination.target_key,
                "channel": destination.channel,
                "required": destination.required,
                "recipient": str(destination.recipient) if destination.recipient else None,
                "webhook_url": destination.webhook_url,
                "webhook_secret": destination.webhook_secret,
            }
        )
    policy = LeadRoutingPolicy(
        tenant_id=project.tenant_id,
        project_id=project.id,
        site_id=site.id,
        version=next_version,
        state="draft",
        policy_hash=policy_hash(source_destinations),
        created_by=auth.user.id,
    )
    db.add(policy)
    await db.flush()
    encryptor = get_encryptor()
    for destination in body.destinations:
        if destination.channel == "webhook":
            try:
                target_url = validate_webhook_target(destination.webhook_url or "")
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            target_secret_enc = encryptor.encrypt(destination.webhook_secret or "")
            target_recipient_enc = None
        else:
            target_url = None
            target_secret_enc = None
            target_recipient_enc = encryptor.encrypt(str(destination.recipient))
        db.add(
            LeadRoutingDestination(
                tenant_id=project.tenant_id,
                policy_id=policy.id,
                target_key=destination.target_key,
                channel=destination.channel,
                required=destination.required,
                target_url=target_url,
                target_recipient_enc=target_recipient_enc,
                target_secret_enc=target_secret_enc,
            )
        )
    await append_audit(
        db,
        action="lead_routing.policy.create",
        payload={
            "project_id": str(project.id),
            "policy_id": str(policy.id),
            "version": policy.version,
            "destinations": [
                {
                    "target_key": destination.target_key,
                    "channel": destination.channel,
                    "required": destination.required,
                }
                for destination in body.destinations
            ],
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_policy(policy, await _policy_destinations(db, policy))


@router.post("/{project_id}/lead-routing/{policy_id}/submit")
async def submit_lead_routing_policy(
    project_id: UUID,
    policy_id: UUID,
    body: LeadRoutingDecisionIn,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project, _site = await _project_site(db, project_id, auth)
    policy = (
        await db.execute(
            select(LeadRoutingPolicy)
            .where(
                LeadRoutingPolicy.id == policy_id,
                LeadRoutingPolicy.project_id == project.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Routing policy not found")
    if policy.state != "draft":
        raise HTTPException(status_code=409, detail="Only draft routing policies can be submitted")
    policy.state = "review"
    policy.submitted_at = datetime.now(UTC)
    await append_audit(
        db,
        action="lead_routing.policy.submit",
        payload={
            "project_id": str(project.id),
            "policy_id": str(policy.id),
            "version": policy.version,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_policy(policy, await _policy_destinations(db, policy))


@router.post("/{project_id}/lead-routing/{policy_id}/reject")
async def reject_lead_routing_policy(
    project_id: UUID,
    policy_id: UUID,
    body: LeadRoutingDecisionIn,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project, _site = await _project_site(db, project_id, auth)
    policy = (
        await db.execute(
            select(LeadRoutingPolicy)
            .where(
                LeadRoutingPolicy.id == policy_id,
                LeadRoutingPolicy.project_id == project.id,
                LeadRoutingPolicy.state == "review",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Routing policy under review not found")
    if not body.reason or not body.reason.strip():
        raise HTTPException(status_code=422, detail="Provide a rejection reason")
    policy.state = "rejected"
    policy.reviewed_by = auth.user.id
    policy.reviewed_at = datetime.now(UTC)
    policy.decision_reason = body.reason.strip()
    destinations = await _policy_destinations(db, policy)
    await append_audit(
        db,
        action="lead_routing.policy.reject",
        payload={
            "project_id": str(project.id),
            "policy_id": str(policy.id),
            "version": policy.version,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_policy(policy, destinations)


@router.post("/{project_id}/lead-routing/{policy_id}/activate")
async def activate_lead_routing_policy(
    project_id: UUID,
    policy_id: UUID,
    body: LeadRoutingDecisionIn,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project, site = await _project_site(db, project_id, auth)
    policy = (
        await db.execute(
            select(LeadRoutingPolicy)
            .where(
                LeadRoutingPolicy.id == policy_id,
                LeadRoutingPolicy.project_id == project.id,
                LeadRoutingPolicy.site_id == site.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Routing policy not found")
    if policy.state != "review":
        raise HTTPException(
            status_code=409, detail="Only reviewed routing policies can be activated"
        )
    destinations = await _policy_destinations(db, policy)
    if not destinations:
        raise HTTPException(status_code=409, detail="Routing policy requires a destination")
    active = (
        await db.execute(
            select(LeadRoutingPolicy)
            .where(LeadRoutingPolicy.site_id == site.id, LeadRoutingPolicy.state == "active")
            .with_for_update()
        )
    ).scalar_one_or_none()
    if active:
        active.state = "superseded"
    policy.state = "active"
    policy.reviewed_by = auth.user.id
    policy.reviewed_at = datetime.now(UTC)
    policy.decision_reason = body.reason.strip() if body.reason else None
    await append_audit(
        db,
        action="lead_routing.policy.activate",
        payload={
            "project_id": str(project.id),
            "policy_id": str(policy.id),
            "version": policy.version,
            "destination_count": len(destinations),
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_policy(policy, destinations)
