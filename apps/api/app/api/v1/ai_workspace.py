from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.api.v1.projects import _confirmed_facts, _selection_snapshots
from app.db.session import get_db
from app.models import AIProviderConnection, AIRun, KnowledgeDoc, PagePlan, Project
from app.providers import Usage
from app.schemas.ai import (
    AIRunOut,
    AIRunSummary,
    ArchitectureProposalOut,
    ArchitectureProposalRequest,
    ArchitectureQuoteOut,
    PageProposal,
)
from app.schemas.workflow import normalize_page_plan_slug
from app.services.ai_data_policy import public_fact_rows, safe_provider_context
from app.services.ai_queue import enqueue_ai_run
from app.services.audit import append_audit
from app.services.competitor import evidence_provider_rows
from app.services.managed_prompts import active_prompt
from app.services.prompt_catalog import list_prompts
from fastapi import APIRouter, Depends, HTTPException
from site_panel_blocks import list_kits
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


def _hash_snapshot(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _usage_payload(usage: Usage | None) -> dict[str, int]:
    if usage is None or not usage.known:
        return {}
    return {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}


def _actual_cost(
    usage: Usage | None,
    pricing: dict[str, Any],
    estimated_cost_usd: float,
) -> float:
    if usage is None or not usage.known:
        return estimated_cost_usd
    return (
        usage.input_tokens * float(pricing["input_price_usd_per_million"])
        + usage.output_tokens * float(pricing["output_price_usd_per_million"])
    ) / 1_000_000


def _architecture_context_summary(snapshot: dict[str, Any]) -> dict[str, int]:
    return {
        "project_profile": 1,
        "confirmed_public_facts": len(snapshot["confirmed_facts"]),
        "selected_keywords": len(snapshot["selected_keywords"]),
        "validated_geo": len(snapshot["validated_geo"]),
        "approved_competitor_evidence": len(snapshot["approved_competitor_evidence"]),
        "existing_page_plans": len(snapshot["existing_page_plans"]),
        "allowed_kits": len(snapshot["allowed_kits"]),
        "operator_constraints": len(snapshot["operator_constraints"]),
        "regenerate_page_ids": len(snapshot["regenerate_page_ids"]),
    }


def _proposal_out(run: AIRun) -> ArchitectureProposalOut:
    return ArchitectureProposalOut(
        run_id=run.id,
        status=run.status,
        provider_id=run.provider_id,
        model_id=run.model_id,
        pages=[PageProposal.model_validate(item) for item in run.output.get("pages", [])],
        prompt_id=run.prompt_id,
        prompt_version=run.prompt_version,
        prompt_hash=run.prompt_hash,
        input_snapshot_hash=run.input_snapshot_hash,
        estimated_cost_usd=run.input_snapshot.get("spend_policy", {}).get("estimated_cost_usd"),
        max_cost_usd=run.input_snapshot.get("spend_policy", {}).get("max_cost_usd"),
        error_code=run.error_code,
    )


def _run_out(run: AIRun) -> AIRunOut:
    return AIRunOut(
        id=run.id,
        action=run.action,
        status=run.status,
        provider_id=run.provider_id,
        model_id=run.model_id,
        prompt_id=run.prompt_id,
        prompt_version=run.prompt_version,
        prompt_hash=run.prompt_hash,
        input_snapshot_hash=run.input_snapshot_hash,
        output=run.output or {},
        usage=run.usage or {},
        cost_usd=run.cost_usd,
        error_code=run.error_code,
        created_at=run.created_at.isoformat() if run.created_at else None,
    )


def _summary_out(run: AIRun) -> AIRunSummary:
    return AIRunSummary(
        id=run.id,
        project_id=run.project_id,
        action=run.action,
        status=run.status,
        provider_id=run.provider_id,
        model_id=run.model_id,
        prompt_id=run.prompt_id,
        prompt_version=run.prompt_version,
        prompt_hash=run.prompt_hash,
        input_snapshot_hash=run.input_snapshot_hash,
        usage=run.usage or {},
        cost_usd=run.cost_usd,
        error_code=run.error_code,
        created_at=run.created_at.isoformat() if run.created_at else None,
    )


async def _reserve_ai_run(
    *,
    db: AsyncSession,
    auth: AuthContext,
    project_id: UUID | None,
    provider_id: str,
    model_id: str,
    prompt: Any,
    snapshot: dict[str, Any],
    estimated_cost_usd: float,
    action: str,
    execution_envelope: dict[str, Any] | None = None,
) -> AIRun:
    from app.services.ai_budget import reserve_ai_budget

    await reserve_ai_budget(
        db,
        tenant_id=auth.tenant_id,
        estimated_cost_usd=estimated_cost_usd,
    )
    run = AIRun(
        tenant_id=auth.tenant_id,
        project_id=project_id,
        action=action,
        status="reserved",
        provider_id=provider_id,
        model_id=model_id,
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.version,
        prompt_hash=prompt.content_hash,
        input_snapshot_hash=_hash_snapshot(snapshot),
        input_snapshot=snapshot,
        execution_envelope=execution_envelope or {},
        output={},
        usage={},
        cost_usd=estimated_cost_usd,
        error_code="budget_reserved",
    )
    db.add(run)
    await db.flush()
    await append_audit(
        db,
        action="ai.run.reserved",
        payload={
            "run_id": str(run.id),
            "project_id": str(project_id),
            "action": action,
            "provider_id": provider_id,
            "model_id": model_id,
            "estimated_cost_usd": round(estimated_cost_usd, 8),
        },
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return run


async def _record_failed_run(
    *,
    db: AsyncSession,
    auth: AuthContext,
    project_id: UUID,
    provider_id: str,
    model_id: str,
    prompt: Any,
    snapshot: dict[str, Any],
    error_code: str,
    action: str = "architecture.site-map",
    usage: dict[str, int] | None = None,
    cost_usd: float | None = None,
    request_id: str | None = None,
    reservation: AIRun | None = None,
) -> None:
    run = reservation or AIRun(
        tenant_id=auth.tenant_id,
        project_id=project_id,
        action=action,
        provider_id=provider_id,
        model_id=model_id,
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.version,
        prompt_hash=prompt.content_hash,
        input_snapshot_hash=_hash_snapshot(snapshot),
        input_snapshot=snapshot,
        output={},
        usage={},
    )
    run.status = "failed"
    run.request_id = request_id or run.request_id
    run.output = {"pages": []}
    run.usage = usage or {}
    if cost_usd is not None:
        run.cost_usd = cost_usd
    run.error_code = error_code
    if reservation is None:
        db.add(run)
    await db.flush()
    await append_audit(
        db,
        action="ai.run.failed",
        payload={
            "run_id": str(run.id),
            "project_id": str(project_id),
            "action": action,
            "provider_id": provider_id,
            "model_id": model_id,
            "error_code": error_code,
            "cost_usd": cost_usd,
        },
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()


def _validate_proposal(
    output: dict[str, Any],
    *,
    keyword_ids: set[str],
    geo_ids: set[str],
    fact_keys: set[str],
    catalogs: dict[str, set[str]],
) -> list[dict[str, Any]]:
    if (
        set(output) != {"pages"}
        or not isinstance(output["pages"], list)
        or len(output["pages"]) > 100
    ):
        raise ValueError("Architecture output has an invalid top-level shape")
    pages = [PageProposal.model_validate(item) for item in output["pages"]]
    keys: set[str] = set()
    slugs: set[str] = set()
    result = []
    for page in pages:
        if page.key in keys or page.slug in slugs:
            raise ValueError("Architecture output contains duplicate page keys or slugs")
        keys.add(page.key)
        slugs.add(page.slug)
        if any(str(item) not in keyword_ids for item in page.keyword_ids):
            raise ValueError("Architecture output referenced an unselected keyword")
        if any(str(item) not in geo_ids for item in page.geo_ids):
            raise ValueError("Architecture output referenced an unselected place")
        if any(item not in fact_keys for item in page.fact_keys):
            raise ValueError("Architecture output referenced an unknown fact")
        if page.kit_key not in catalogs:
            raise ValueError("Architecture output referenced an unknown kit")
        if any(block_id not in catalogs[page.kit_key] for block_id in page.block_ids):
            raise ValueError("Architecture output referenced an unknown block")
        try:
            normalized_slug = normalize_page_plan_slug(page.slug)
        except ValueError as exc:
            raise ValueError("Architecture output contains an invalid slug") from exc
        if normalized_slug != page.slug:
            raise ValueError("Architecture output contains an invalid slug")
        result.append(page.model_dump(mode="json"))
    for page in pages:
        if page.parent_key and page.parent_key not in keys:
            raise ValueError("Architecture output referenced an unknown parent page")
        if page.parent_key == page.key:
            raise ValueError("Architecture output cannot make a page its own parent")
    return result


async def _prepare_architecture_context(
    project_id: UUID,
    body: ArchitectureProposalRequest,
    auth: AuthContext,
    db: AsyncSession,
) -> dict[str, Any]:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    project = (
        await db.execute(
            select(Project).where(Project.id == project_id, Project.tenant_id == auth.tenant_id)
        )
    ).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    connection = await db.get(AIProviderConnection, body.provider_connection_id)
    if not connection or not connection.enabled:
        raise HTTPException(status_code=409, detail="Select an activated AI provider connection")
    if body.model not in connection.model_ids:
        raise HTTPException(status_code=422, detail="Model is not registered for this connection")
    pricing = (connection.metadata_json or {}).get("model_pricing", {}).get(body.model)
    if not pricing:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "model_pricing_required",
                "message": "Add current pricing metadata before generation",
            },
        )
    try:
        observed_at = datetime.fromisoformat(pricing["observed_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail={"code": "model_pricing_invalid"}) from exc
    age = datetime.now(UTC) - observed_at
    if observed_at.tzinfo is None or age.days > 30 or age.total_seconds() < -3600:
        raise HTTPException(status_code=409, detail={"code": "model_pricing_stale"})

    facts_revision = await _confirmed_facts(db, project)
    keyword_snapshot, geo_snapshot, blockers = await _selection_snapshots(db, project)
    if blockers:
        raise HTTPException(status_code=409, detail={"blockers": blockers})
    plans = list(
        (
            await db.execute(
                select(PagePlan).where(PagePlan.project_id == project.id).order_by(PagePlan.slug)
            )
        )
        .scalars()
        .all()
    )
    approved_evidence = list(
        (
            await db.execute(
                select(KnowledgeDoc)
                .where(
                    KnowledgeDoc.project_id == project.id,
                    KnowledgeDoc.tenant_id == project.tenant_id,
                    KnowledgeDoc.kind.in_(("competitor_evidence", "competitor_crawl_evidence")),
                    KnowledgeDoc.state == "approved",
                )
                .order_by(KnowledgeDoc.approved_at.asc())
            )
        )
        .scalars()
        .all()
    )
    kits = list_kits()
    catalogs = {item["key"]: set(item["blocks"]) for item in kits}
    facts = facts_revision.facts or {}
    snapshot = {
        "project": {"name": project.name, "locale": project.locale, "niche": project.niche},
        "confirmed_facts": public_fact_rows(facts),
        "selected_keywords": keyword_snapshot["items"],
        "validated_geo": geo_snapshot["items"],
        "approved_competitor_evidence": [
            evidence_provider_rows(item.content) for item in approved_evidence
        ],
        "existing_page_plans": [
            {
                "id": str(plan.id),
                "slug": plan.slug,
                "objective": plan.objective,
                "intent": plan.intent,
                "state": plan.state,
            }
            for plan in plans
        ],
        "allowed_kits": kits,
        "operator_constraints": body.operator_constraints,
        "regenerate_page_ids": [str(item) for item in body.regenerate_page_ids],
    }
    try:
        snapshot = safe_provider_context(snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "unsafe_ai_context"}) from exc
    if len(json.dumps(snapshot, ensure_ascii=False)) > 64_000:
        raise HTTPException(status_code=413, detail="Project context exceeds the AI request limit")
    prompt = await active_prompt(
        db,
        tenant_id=auth.tenant_id,
        relative_path="architecture/propose-site-map.md",
    )
    user_prompt = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    input_tokens_bound = len(user_prompt.encode("utf-8")) + len(prompt.content.encode("utf-8"))
    estimated_cost = (
        input_tokens_bound * float(pricing["input_price_usd_per_million"])
        + body.max_output_tokens * float(pricing["output_price_usd_per_million"])
    ) / 1_000_000
    if estimated_cost > body.max_cost_usd:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "estimated_cost_exceeds_limit",
                "estimated_cost_usd": round(estimated_cost, 8),
            },
        )
    from app.services.ai_budget import enforce_ai_budget

    await enforce_ai_budget(
        db,
        tenant_id=auth.tenant_id,
        estimated_cost_usd=estimated_cost,
    )
    return {
        "project": project,
        "connection": connection,
        "pricing": pricing,
        "prompt": prompt,
        "facts": facts,
        "keyword_snapshot": keyword_snapshot,
        "geo_snapshot": geo_snapshot,
        "catalogs": catalogs,
        "snapshot": snapshot,
        "context_summary": _architecture_context_summary(snapshot),
        "user_prompt": user_prompt,
        "estimated_cost": estimated_cost,
        "quote_snapshot_hash": _hash_snapshot(
            {
                **snapshot,
                "provider_connection_id": str(connection.id),
                "model": body.model,
                "max_cost_usd": body.max_cost_usd,
                "max_output_tokens": body.max_output_tokens,
                "pricing": pricing,
                "prompt_id": prompt.prompt_id,
                "prompt_version": prompt.version,
                "prompt_hash": prompt.content_hash,
            }
        ),
    }


@router.get("/prompt-assets")
async def list_prompt_assets(
    _auth: AuthContext = Depends(require_roles("superadmin")),
) -> list[dict[str, str]]:
    return [
        {
            "id": prompt.prompt_id,
            "version": prompt.version,
            "hash": prompt.content_hash,
            "path": "/".join(prompt.path.parts[-3:]),
        }
        for prompt in list_prompts()
    ]


@router.post("/projects/{project_id}/architecture/quote", response_model=ArchitectureQuoteOut)
async def quote_architecture(
    project_id: UUID,
    body: ArchitectureProposalRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> ArchitectureQuoteOut:
    context = await _prepare_architecture_context(project_id, body, auth, db)
    return ArchitectureQuoteOut(
        provider_id=context["connection"].provider_id,
        model_id=body.model,
        estimated_cost_usd=round(context["estimated_cost"], 8),
        max_cost_usd=body.max_cost_usd,
        input_snapshot_hash=context["quote_snapshot_hash"],
        pricing_source=context["pricing"]["source"],
        pricing_observed_at=context["pricing"]["observed_at"],
        context_summary=context["context_summary"],
    )


@router.post(
    "/projects/{project_id}/architecture", response_model=ArchitectureProposalOut, status_code=202
)
async def propose_architecture(
    project_id: UUID,
    body: ArchitectureProposalRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> ArchitectureProposalOut:
    context = await _prepare_architecture_context(project_id, body, auth, db)
    if not body.confirm_external_processing or not body.confirm_provider_budget:
        raise HTTPException(status_code=409, detail={"code": "operator_confirmation_required"})
    if body.confirmed_estimated_cost_usd is None or body.quote_snapshot_hash is None:
        raise HTTPException(status_code=409, detail={"code": "cost_quote_confirmation_required"})
    if (
        not math.isclose(
            body.confirmed_estimated_cost_usd,
            context["estimated_cost"],
            rel_tol=0,
            abs_tol=1e-8,
        )
        or body.quote_snapshot_hash != context["quote_snapshot_hash"]
    ):
        raise HTTPException(status_code=409, detail={"code": "cost_quote_changed"})

    connection = context["connection"]
    pricing = context["pricing"]
    prompt = context["prompt"]
    keyword_snapshot = context["keyword_snapshot"]
    geo_snapshot = context["geo_snapshot"]
    catalogs = context["catalogs"]
    snapshot = context["snapshot"]
    user_prompt = context["user_prompt"]
    estimated_cost = context["estimated_cost"]
    snapshot["spend_policy"] = {
        "estimated_cost_usd": round(estimated_cost, 8),
        "max_cost_usd": body.max_cost_usd,
        "pricing_source": pricing["source"],
        "pricing_observed_at": pricing["observed_at"],
        "provider_budget_confirmed": True,
        "estimate_confirmed_by_operator": body.confirmed_estimated_cost_usd,
    }
    envelope = {
        "tenant_id": str(auth.tenant_id),
        "provider_connection_id": str(connection.id),
        "model": body.model,
        "system_prompt": prompt.content,
        "user_prompt": user_prompt,
        "output_schema": {"type": "object", "required": ["pages"]},
        "temperature": 0.2,
        "max_output_tokens": body.max_output_tokens,
        "max_cost_usd": body.max_cost_usd,
        "pricing": pricing,
        "validation": {
            "keyword_ids": sorted(item["keyword_id"] for item in keyword_snapshot["items"]),
            "geo_ids": sorted(item["geo_id"] for item in geo_snapshot["items"]),
            "fact_keys": sorted(row["fact_key"] for row in snapshot["confirmed_facts"]),
            "catalogs": {key: sorted(value) for key, value in catalogs.items()},
        },
    }
    run = await _reserve_ai_run(
        db=db,
        auth=auth,
        project_id=project_id,
        provider_id=connection.provider_id,
        model_id=body.model,
        prompt=prompt,
        snapshot=snapshot,
        estimated_cost_usd=estimated_cost,
        action="architecture.site-map",
        execution_envelope=envelope,
    )
    try:
        await enqueue_ai_run(run.id)
    except Exception:  # noqa: BLE001
        # Keep the reservation as conservative accounting: no provider call occurred.
        run.status = "failed"
        run.error_code = "queue_unavailable"
        await append_audit(
            db,
            action="ai.run.queue_failed",
            payload={"run_id": str(run.id), "error_code": "queue_unavailable"},
            tenant_id=auth.tenant_id,
            actor_id=auth.user.id,
        )
        await db.commit()
    await db.refresh(run)
    return _proposal_out(run)


@router.get("/runs", response_model=list[AIRunSummary])
async def list_ai_runs(
    project_id: UUID | None = None,
    action: str | None = None,
    status: str | None = None,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> list[AIRunSummary]:
    if auth.tenant_id is None:
        raise HTTPException(status_code=403, detail="Tenant required")
    statement = select(AIRun).where(AIRun.tenant_id == auth.tenant_id)
    if project_id is not None:
        statement = statement.where(AIRun.project_id == project_id)
    if action is not None:
        statement = statement.where(AIRun.action == action)
    if status is not None:
        statement = statement.where(AIRun.status == status)
    runs = (
        (await db.execute(statement.order_by(AIRun.created_at.desc()).limit(100))).scalars().all()
    )
    return [_summary_out(run) for run in runs]


@router.get("/runs/{run_id}", response_model=AIRunOut)
async def get_ai_run(
    run_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> AIRunOut:
    run = (
        await db.execute(select(AIRun).where(AIRun.id == run_id, AIRun.tenant_id == auth.tenant_id))
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="AI run not found")
    return _run_out(run)


@router.post("/runs/{run_id}/decision", response_model=ArchitectureProposalOut | AIRunOut)
async def decide_ai_run(
    run_id: UUID,
    decision: dict[str, str],
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> ArchitectureProposalOut:
    if decision.get("decision") not in {"approve", "reject"}:
        raise HTTPException(status_code=422, detail="decision must be approve or reject")
    run = (
        await db.execute(
            select(AIRun)
            .where(AIRun.id == run_id, AIRun.tenant_id == auth.tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="AI run not found")
    if run.action not in {
        "architecture.site-map",
        "seo.create-brief",
        "content.block-slot-copy",
        "content.intent-page-proposal",
        "geo.city-hierarchy",
    }:
        raise HTTPException(
            status_code=409,
            detail="This AI action has no approval decision endpoint",
        )
    if run.status != "pending_approval":
        raise HTTPException(status_code=409, detail="AI run is not awaiting approval")
    run.operator_decision = decision["decision"]
    run.status = "approved" if decision["decision"] == "approve" else "rejected"
    await append_audit(
        db,
        action="ai.run.decision",
        payload={"run_id": str(run.id), "decision": decision["decision"]},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(run)
    return _proposal_out(run) if run.action == "architecture.site-map" else _run_out(run)
