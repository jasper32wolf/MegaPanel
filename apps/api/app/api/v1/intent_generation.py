"""Durable, approval-gated intent and art-direction proposals for approved PagePlans."""

from __future__ import annotations

import json
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.api.v1.ai_content import _prepare_draft_context
from app.api.v1.ai_workspace import _reserve_ai_run, _run_out
from app.api.v1.projects import (
    _draft_manifest_hash,
    _plan_or_404,
    _project_or_404,
    _serialize_draft,
)
from app.core.security import sha256_hex
from app.db.session import get_db
from app.models import AIRun, PageDraft
from app.schemas.ai import (
    AIRunOut,
    ArchitectureQuoteOut,
    IntentGenerationRequest,
    IntentPageProposalOut,
)
from app.services.ai_queue import enqueue_intent_generation_run
from app.services.audit import append_audit
from app.services.intent_generation import (
    compile_intent_generation_context,
    validate_intent_page_proposal,
)
from app.services.managed_prompts import active_prompt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
_READ = require_roles("superadmin", "tenant_admin", "manager", "editor")
_WRITE = require_roles("superadmin", "tenant_admin", "manager", "editor")


def _hash_json(value: dict) -> str:
    return sha256_hex(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _allowed_block_slots(provider_snapshot: dict) -> dict[str, dict[str, dict]]:
    return {
        str(item["id"]): dict(item.get("slots") or {})
        for item in provider_snapshot.get("allowed_blocks") or []
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _semantic_keyword_ids(source_binding: dict) -> set[str]:
    targets = (source_binding.get("semantic_target_snapshot") or {}).get("targets") or []
    return {
        str(item["project_keyword_id"])
        for item in targets
        if isinstance(item, dict) and item.get("project_keyword_id")
    }


async def _prepare_intent_context(
    project_id: UUID,
    plan_id: UUID,
    body: IntentGenerationRequest,
    auth: AuthContext,
    db: AsyncSession,
) -> dict:
    """Reuse provider preparation, then replace mutable draft context with frozen intent input."""
    base = await _prepare_draft_context(project_id, plan_id, body, auth, db)
    compiled = await compile_intent_generation_context(
        db, project=base["project"], plan=base["plan"]
    )
    prompt = await active_prompt(
        db,
        tenant_id=auth.tenant_id,
        relative_path="content/propose-intent-page.md",
    )
    user_prompt = json.dumps(compiled.provider_snapshot, ensure_ascii=False, sort_keys=True)
    pricing = base["pricing"]
    estimated_cost = (
        (len(user_prompt.encode("utf-8")) + len(prompt.content.encode("utf-8")))
        * float(pricing["input_price_usd_per_million"])
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
    quote_hash = _hash_json(
        {
            **compiled.provider_snapshot,
            "source_binding": compiled.source_binding,
            "provider_connection_id": str(base["connection"].id),
            "model": body.model,
            "max_cost_usd": body.max_cost_usd,
            "max_output_tokens": body.max_output_tokens,
            "pricing": pricing,
            "prompt_id": prompt.prompt_id,
            "prompt_version": prompt.version,
            "prompt_hash": prompt.content_hash,
        }
    )
    return {
        **base,
        "compiled": compiled,
        "prompt": prompt,
        "user_prompt": user_prompt,
        "estimated_cost": estimated_cost,
        "quote_hash": quote_hash,
    }


@router.get("/{project_id}/intent-generation-runs", response_model=list[AIRunOut])
async def list_intent_generation_runs(
    project_id: UUID,
    auth: AuthContext = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> list[AIRunOut]:
    project = await _project_or_404(db, project_id, auth)
    runs = list(
        (
            await db.execute(
                select(AIRun)
                .where(
                    AIRun.project_id == project.id,
                    AIRun.tenant_id == project.tenant_id,
                    AIRun.action == "content.intent-page-proposal",
                )
                .order_by(AIRun.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return [_run_out(run) for run in runs]


@router.post(
    "/{project_id}/page-plans/{plan_id}/intent-generation/quote",
    response_model=ArchitectureQuoteOut,
)
async def quote_intent_generation(
    project_id: UUID,
    plan_id: UUID,
    body: IntentGenerationRequest,
    auth: AuthContext = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ArchitectureQuoteOut:
    context = await _prepare_intent_context(project_id, plan_id, body, auth, db)
    return ArchitectureQuoteOut(
        provider_id=context["connection"].provider_id,
        model_id=body.model,
        estimated_cost_usd=round(context["estimated_cost"], 8),
        max_cost_usd=body.max_cost_usd,
        input_snapshot_hash=context["quote_hash"],
        pricing_source=context["pricing"]["source"],
        pricing_observed_at=context["pricing"]["observed_at"],
    )


@router.post(
    "/{project_id}/page-plans/{plan_id}/intent-generation",
    response_model=AIRunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_intent_generation(
    project_id: UUID,
    plan_id: UUID,
    body: IntentGenerationRequest,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> AIRunOut:
    context = await _prepare_intent_context(project_id, plan_id, body, auth, db)
    if (
        not body.operator_confirmed_external_processing
        or not body.operator_confirmed_provider_budget
    ):
        raise HTTPException(status_code=409, detail={"code": "operator_confirmation_required"})
    if body.confirmed_estimated_cost_usd is None or not body.quote_snapshot_hash:
        raise HTTPException(status_code=409, detail={"code": "cost_quote_confirmation_required"})
    if (
        abs(body.confirmed_estimated_cost_usd - context["estimated_cost"]) > 1e-8
        or body.quote_snapshot_hash != context["quote_hash"]
    ):
        raise HTTPException(status_code=409, detail={"code": "cost_quote_changed"})
    compiled = context["compiled"]
    snapshot = {
        **compiled.provider_snapshot,
        "source_binding": compiled.source_binding,
        "spend_policy": {
            "estimated_cost_usd": round(context["estimated_cost"], 8),
            "max_cost_usd": body.max_cost_usd,
            "pricing_source": context["pricing"]["source"],
            "pricing_observed_at": context["pricing"]["observed_at"],
            "provider_budget_confirmed": True,
        },
    }
    validation = {
        "source_binding": compiled.source_binding,
        "allowed_block_slots": _allowed_block_slots(compiled.provider_snapshot),
        "fact_keys": [item["fact_key"] for item in compiled.provider_snapshot["confirmed_facts"]],
        "semantic_project_keyword_ids": sorted(_semantic_keyword_ids(compiled.source_binding)),
    }
    envelope = {
        "tenant_id": str(context["project"].tenant_id),
        "provider_connection_id": str(context["connection"].id),
        "model": body.model,
        "system_prompt": context["prompt"].content,
        "user_prompt": context["user_prompt"],
        "output_schema": IntentPageProposalOut.model_json_schema(),
        "max_output_tokens": body.max_output_tokens,
        "max_cost_usd": body.max_cost_usd,
        "pricing": context["pricing"],
        "validation": validation,
    }
    run = await _reserve_ai_run(
        db=db,
        auth=auth,
        project_id=project_id,
        provider_id=context["connection"].provider_id,
        model_id=body.model,
        prompt=context["prompt"],
        snapshot=snapshot,
        estimated_cost_usd=context["estimated_cost"],
        action="content.intent-page-proposal",
        execution_envelope=envelope,
    )
    try:
        await enqueue_intent_generation_run(run.id)
    except Exception:  # noqa: BLE001
        # Reservation stays durable; explicit worker recovery may redeliver only the run id.
        pass
    return _run_out(run)


@router.post(
    "/{project_id}/intent-generation-runs/{run_id}/materialize-page-draft",
    status_code=status.HTTP_201_CREATED,
)
async def materialize_intent_page_draft(
    project_id: UUID,
    run_id: UUID,
    auth: AuthContext = Depends(_WRITE),
    db: AsyncSession = Depends(get_db),
) -> dict:
    project = await _project_or_404(db, project_id, auth)
    run = (
        await db.execute(
            select(AIRun)
            .where(
                AIRun.id == run_id,
                AIRun.project_id == project.id,
                AIRun.tenant_id == project.tenant_id,
                AIRun.action == "content.intent-page-proposal",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Intent generation run not found")
    if run.status != "approved":
        raise HTTPException(
            status_code=409,
            detail="Approve the intent proposal before materialization",
        )
    if run.output.get("page_draft_id"):
        draft = await db.get(PageDraft, UUID(run.output["page_draft_id"]))
        if draft:
            return {"draft": _serialize_draft(draft), "materialized": False}
    binding = (run.input_snapshot or {}).get("source_binding") or {}
    try:
        plan_id = UUID(binding["page_plan_id"])
    except (KeyError, TypeError, ValueError) as exc:
        binding_error = HTTPException(
            status_code=409,
            detail="Intent proposal source binding is unavailable",
        )
        raise binding_error from exc
    plan = await _plan_or_404(db, project, plan_id)
    try:
        compiled = await compile_intent_generation_context(db, project=project, plan=plan)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if compiled.source_binding != binding:
        raise HTTPException(
            status_code=409,
            detail="Frozen PagePlan or design profile changed; regenerate",
        )
    proposal = validate_intent_page_proposal(
        run.output.get("proposal") or {},
        source_binding=binding,
        allowed_block_slots=_allowed_block_slots(compiled.provider_snapshot),
        fact_keys={item["fact_key"] for item in compiled.provider_snapshot["confirmed_facts"]},
        semantic_project_keyword_ids=_semantic_keyword_ids(binding),
    )
    manifest = dict(compiled.manifest)
    manifest.update(
        title_template=proposal["title"],
        h1_template=proposal["h1"],
        meta_description_template=proposal["meta_description"],
        unique_core=proposal["unique_core"],
        index_state="noindex",
    )
    slots = dict(manifest.get("block_slot_values") or {})
    slots.update(proposal["block_slots"])
    manifest["block_slot_values"] = slots
    latest = (
        await db.execute(
            select(PageDraft)
            .where(PageDraft.page_plan_id == plan.id)
            .order_by(PageDraft.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    generator_meta = {
        **compiled.deterministic_snapshot["generator_meta"],
        "intent_generation_run_id": str(run.id),
        "prompt_id": run.prompt_id,
        "prompt_version": run.prompt_version,
        "prompt_hash": run.prompt_hash,
        "provider_id": run.provider_id,
        "model_id": run.model_id,
        "cost_usd": run.cost_usd,
        "art_direction": proposal["art_direction"],
        "warnings": proposal["warnings"],
    }
    input_snapshot = {
        **compiled.deterministic_snapshot,
        "generator_meta": generator_meta,
        "intent_generation": proposal,
        "ai_provenance": {
            "provider_id": run.provider_id,
            "model_id": run.model_id,
            "prompt_id": run.prompt_id,
            "prompt_version": run.prompt_version,
            "prompt_hash": run.prompt_hash,
            "fact_keys": proposal["fact_keys"],
        },
    }
    draft = PageDraft(
        page_plan_id=plan.id,
        project_id=project.id,
        tenant_id=project.tenant_id,
        revision=(latest.revision if latest else 0) + 1,
        state="draft",
        input_snapshot=input_snapshot,
        page_manifest=manifest,
        generator_meta=generator_meta,
        content_hash=_draft_manifest_hash(manifest),
        requested_by=auth.user.id,
    )
    db.add(draft)
    await db.flush()
    run.output = {**run.output, "page_draft_id": str(draft.id)}
    await append_audit(
        db,
        action="ai.intent_page.materialize_draft",
        payload={
            "run_id": str(run.id),
            "page_plan_id": str(plan.id),
            "page_draft_id": str(draft.id),
            "content_hash": draft.content_hash,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"draft": _serialize_draft(draft), "materialized": True}
