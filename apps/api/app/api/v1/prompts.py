from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models.ai import PromptEntry
from app.schemas.prompts import PromptRevisionCreate
from app.services.audit import append_audit
from app.services.managed_prompts import effective_prompt
from app.services.prompt_catalog import list_prompts
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


def _baseline(prompt_id: str):
    return next((item for item in list_prompts() if item.prompt_id == prompt_id), None)


def _serialize(entry: PromptEntry) -> dict:
    return {
        "id": str(entry.id),
        "key": entry.key,
        "version": entry.version,
        "instructions": entry.template,
        "active": entry.is_active,
        "state": entry.state,
        "baseline_hash": (entry.schema_json or {}).get("baseline_hash"),
        "created_by": str(entry.created_by) if entry.created_by else None,
        "reviewed_by": str(entry.reviewed_by) if entry.reviewed_by else None,
        "submitted_at": entry.submitted_at.isoformat() if entry.submitted_at else None,
        "reviewed_at": entry.reviewed_at.isoformat() if entry.reviewed_at else None,
        "decision_reason": entry.decision_reason,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


async def _revision_or_404(
    db: AsyncSession, prompt_id: str, revision_id: UUID, auth: AuthContext
) -> PromptEntry:
    if auth.tenant_id is None:
        raise HTTPException(status_code=403, detail="Tenant required")
    entry = (
        await db.execute(
            select(PromptEntry).where(
                PromptEntry.id == revision_id,
                PromptEntry.tenant_id == auth.tenant_id,
                PromptEntry.key == prompt_id,
            )
        )
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Prompt revision not found")
    return entry


@router.get("")
async def list_managed_prompts(
    auth: AuthContext = Depends(require_roles("superadmin")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    if auth.tenant_id is None:
        raise HTTPException(status_code=403, detail="Tenant required")
    revisions = list(
        (
            await db.execute(
                select(PromptEntry)
                .where(PromptEntry.tenant_id == auth.tenant_id)
                .order_by(PromptEntry.key, PromptEntry.version.desc())
            )
        )
        .scalars()
        .all()
    )
    by_key: dict[str, list[PromptEntry]] = {}
    for revision in revisions:
        by_key.setdefault(revision.key, []).append(revision)
    return [
        {
            "id": baseline.prompt_id,
            "baseline_version": baseline.version,
            "baseline_hash": baseline.content_hash,
            "path": "/".join(baseline.path.parts[-3:]),
            "revisions": [_serialize(item) for item in by_key.get(baseline.prompt_id, [])],
        }
        for baseline in list_prompts()
    ]


@router.post("/{prompt_id}/revisions", status_code=status.HTTP_201_CREATED)
async def create_prompt_revision(
    prompt_id: str,
    body: PromptRevisionCreate,
    auth: AuthContext = Depends(require_roles("superadmin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if auth.tenant_id is None:
        raise HTTPException(status_code=403, detail="Tenant required")
    baseline = _baseline(prompt_id)
    if baseline is None:
        raise HTTPException(status_code=404, detail="Built-in prompt not found")
    version = await db.scalar(
        select(func.coalesce(func.max(PromptEntry.version), 0)).where(
            PromptEntry.tenant_id == auth.tenant_id,
            PromptEntry.key == prompt_id,
        )
    )
    entry = PromptEntry(
        tenant_id=auth.tenant_id,
        key=prompt_id,
        version=int(version or 0) + 1,
        tier="operator",
        template=body.instructions,
        schema_json={"baseline_hash": baseline.content_hash},
        is_active=False,
        state="draft",
        created_by=auth.user.id,
    )
    db.add(entry)
    await db.flush()
    await append_audit(
        db,
        action="ai.prompt_revision.create",
        payload={
            "prompt_id": prompt_id,
            "revision_id": str(entry.id),
            "version": entry.version,
            "instructions_hash": effective_prompt(baseline, entry).content_hash,
        },
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(entry)
    return _serialize(entry)


@router.post("/{prompt_id}/revisions/{revision_id}/submit-review")
async def submit_prompt_revision(
    prompt_id: str,
    revision_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    entry = await _revision_or_404(db, prompt_id, revision_id, auth)
    if entry.state != "draft":
        raise HTTPException(status_code=409, detail="Only draft revisions can be submitted")
    entry.state = "review"
    entry.submitted_at = datetime.now(UTC)
    await append_audit(
        db,
        action="ai.prompt_revision.submit_review",
        payload={"prompt_id": prompt_id, "revision_id": str(entry.id)},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize(entry)


@router.post("/{prompt_id}/revisions/{revision_id}/approve")
async def approve_prompt_revision(
    prompt_id: str,
    revision_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    entry = await _revision_or_404(db, prompt_id, revision_id, auth)
    if entry.state != "review":
        raise HTTPException(status_code=409, detail="Only revisions under review can be approved")
    entry.state = "approved"
    entry.reviewed_by = auth.user.id
    entry.reviewed_at = datetime.now(UTC)
    await append_audit(
        db,
        action="ai.prompt_revision.approve",
        payload={"prompt_id": prompt_id, "revision_id": str(entry.id)},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize(entry)


@router.post("/{prompt_id}/revisions/{revision_id}/activate")
async def activate_prompt_revision(
    prompt_id: str,
    revision_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    entry = await _revision_or_404(db, prompt_id, revision_id, auth)
    baseline = _baseline(prompt_id)
    if baseline is None:
        raise HTTPException(status_code=404, detail="Built-in prompt not found")
    if entry.state != "approved":
        raise HTTPException(status_code=409, detail="Approve the prompt revision before activation")
    if (entry.schema_json or {}).get("baseline_hash") != baseline.content_hash:
        raise HTTPException(
            status_code=409, detail="Prompt baseline changed; create a new revision"
        )
    await db.execute(
        update(PromptEntry)
        .where(PromptEntry.tenant_id == auth.tenant_id, PromptEntry.key == prompt_id)
        .values(is_active=False, state="superseded")
    )
    entry.is_active = True
    entry.state = "active"
    await append_audit(
        db,
        action="ai.prompt_revision.activate",
        payload={"prompt_id": prompt_id, "revision_id": str(entry.id), "version": entry.version},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize(entry)


@router.post("/{prompt_id}/rollback-baseline")
async def rollback_prompt_baseline(
    prompt_id: str,
    auth: AuthContext = Depends(require_roles("superadmin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if auth.tenant_id is None or _baseline(prompt_id) is None:
        raise HTTPException(status_code=404, detail="Built-in prompt not found")
    await db.execute(
        update(PromptEntry)
        .where(PromptEntry.tenant_id == auth.tenant_id, PromptEntry.key == prompt_id)
        .values(is_active=False, state="superseded")
    )
    await append_audit(
        db,
        action="ai.prompt_revision.rollback_baseline",
        payload={"prompt_id": prompt_id},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"prompt_id": prompt_id, "active": False, "using_packaged_baseline": True}
