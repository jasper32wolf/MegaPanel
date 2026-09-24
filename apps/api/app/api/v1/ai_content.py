from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.api.v1.ai_providers import _adapter
from app.api.v1.ai_workspace import _record_failed_run, _run_out
from app.api.v1.projects import _plan_or_404, _project_or_404, _serialize_draft
from app.core.security import sha256_hex
from app.db.session import get_db
from app.models import AIProviderConnection, AIRun, PageDraft, ProjectFactRevision
from app.providers import ProviderError, StructuredRequest
from app.schemas.ai import (
    AIBlockSlotCopyOut,
    AIBlockSlotCopyRequest,
    AIDraftGenerationRequest,
    AIDraftTextOut,
    AIRunOut,
    ArchitectureQuoteOut,
    SEOBriefOut,
)
from app.services.ai_data_policy import public_fact_rows, safe_provider_context
from app.services.ai_secrets import decrypt_provider_key
from app.services.audit import append_audit
from app.services.generation import create_page_draft
from app.services.prompt_catalog import load_prompt
from fastapi import APIRouter, Depends, HTTPException
from site_panel_blocks import block_slot_schema
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


def _hash_json(value: dict[str, Any]) -> str:
    return sha256_hex(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def _validate_page_copy(value: dict[str, Any], fact_keys: set[str]) -> dict[str, Any]:
    copy = AIDraftTextOut.model_validate(value)
    strings = (copy.title, copy.h1, copy.meta_description, copy.unique_core)
    if any("<" in item or ">" in item or "{" in item or "}" in item for item in strings):
        raise ValueError("AI copy must be plain text without markup or placeholders")
    if any(any(ord(char) < 32 and char not in "\n\t" for char in item) for item in strings):
        raise ValueError("AI copy contains control characters")
    if any(key not in fact_keys for key in copy.fact_keys):
        raise ValueError("AI copy references an unknown business fact")
    return copy.model_dump()


def _validate_seo_brief(
    value: dict[str, Any], *, plan_slug: str, keyword_ids: set[str], fact_keys: set[str]
) -> dict[str, Any]:
    brief = SEOBriefOut.model_validate(value)
    if brief.canonical_path != plan_slug:
        raise ValueError("SEO canonical path differs from the approved PagePlan")
    if any(str(item) not in keyword_ids for item in brief.keyword_ids):
        raise ValueError("SEO brief references an unselected keyword")
    if any(item not in fact_keys for item in brief.fact_keys):
        raise ValueError("SEO brief references an unconfirmed fact")
    if brief.structured_data_types:
        raise ValueError("Structured data needs a separately approved evidence policy")
    if brief.robots == "index,follow" and not brief.fact_keys:
        raise ValueError("Indexable SEO brief must cite confirmed facts")
    text_fields = (brief.title, brief.description, brief.h1, *brief.uncertainty_notes)
    if any(any(char in text for char in "<>{}") for text in text_fields):
        raise ValueError("SEO brief must use plain text without markup or placeholders")
    if any(any(ord(char) < 32 for char in text) for text in text_fields):
        raise ValueError("SEO brief contains control characters")
    return brief.model_dump(mode="json")


def _apply_approved_seo_brief(manifest: dict[str, Any], brief: dict[str, Any]) -> dict[str, Any]:
    return {
        **manifest,
        "title_template": brief["title"],
        "h1_template": brief["h1"],
        "meta_description_template": brief["description"],
        "index_state": "noindex",
    }


def _validate_block_slot_copy(
    value: dict[str, Any],
    *,
    block_id: str,
    slot_schema: dict[str, dict[str, Any]],
    fact_keys: set[str],
) -> dict[str, Any]:
    proposal = AIBlockSlotCopyOut.model_validate(value)
    if proposal.block_id != block_id:
        raise ValueError("Block slot copy targets a different block")
    if set(proposal.slots) != set(slot_schema):
        raise ValueError("Block slot copy does not match the server slot contract")
    if any(key not in fact_keys for key in proposal.fact_keys):
        raise ValueError("Block slot copy references an unknown business fact")
    values = [item for item in proposal.slots.values() if item is not None]
    if values and not proposal.fact_keys:
        raise ValueError("Block slot copy requires confirmed fact provenance")
    text_values = [*values, *proposal.warnings]
    for text in text_values:
        if not text.strip():
            raise ValueError("Block slot copy contains empty text")
        if any(char in text for char in "<>{}"):
            raise ValueError("Block slot copy must be plain text without markup or placeholders")
        if any(ord(char) < 32 and char not in "\n\t" for char in text):
            raise ValueError("Block slot copy contains control characters")
    for name, text in proposal.slots.items():
        if text is not None and len(text) > int(slot_schema[name]["max_length"]):
            raise ValueError("Block slot copy exceeds the server slot length")
    return proposal.model_dump()


async def _prepare_draft_context(
    project_id: UUID,
    plan_id: UUID,
    body: AIDraftGenerationRequest,
    auth: AuthContext,
    db: AsyncSession,
) -> dict[str, Any]:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    project = await _project_or_404(db, project_id, auth)
    plan = await _plan_or_404(db, project, plan_id)
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
            status_code=409, detail={"blockers": ["PagePlan facts are no longer confirmed"]}
        )
    connection = await db.get(AIProviderConnection, body.provider_connection_id)
    if not connection or not connection.enabled:
        raise HTTPException(status_code=409, detail="Select an activated AI provider connection")
    if body.model not in connection.model_ids:
        raise HTTPException(status_code=422, detail="Model is not registered for this connection")
    pricing = (connection.metadata_json or {}).get("model_pricing", {}).get(body.model)
    if not pricing:
        raise HTTPException(status_code=409, detail={"code": "model_pricing_required"})
    try:
        observed_at = datetime.fromisoformat(pricing["observed_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail={"code": "model_pricing_invalid"}) from exc
    age = datetime.now(UTC) - observed_at
    if observed_at.tzinfo is None or age.days > 30 or age.total_seconds() < -3600:
        raise HTTPException(status_code=409, detail={"code": "model_pricing_stale"})

    manifest, deterministic_snapshot, _ = create_page_draft(project=project, plan=plan, facts=facts)
    snapshot = {
        "project": {"name": project.name, "locale": project.locale, "niche": project.niche},
        "approved_page_plan": {
            "id": str(plan.id),
            "slug": plan.slug,
            "objective": plan.objective,
            "intent": plan.intent,
            "kit_key": plan.kit_key,
            "block_selection": {"blocks": [block["type"] for block in manifest["blocks"]]},
        },
        "confirmed_facts": public_fact_rows(facts.facts or {}),
        "selected_keywords": (plan.keyword_snapshot or {}).get("items", []),
        "validated_geo": (plan.geo_snapshot or {}).get("items", []),
        "allowed_blocks": [block["type"] for block in manifest["blocks"]],
    }
    try:
        snapshot = safe_provider_context(snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "unsafe_ai_context"}) from exc
    if len(json.dumps(snapshot, ensure_ascii=False)) > 64_000:
        raise HTTPException(status_code=413, detail="Project context exceeds the AI request limit")
    prompt = load_prompt("content/page-draft-copy.md")
    user_prompt = json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
    bounded_tokens = len(user_prompt.encode("utf-8")) + len(prompt.content.encode("utf-8"))
    estimated_cost = (
        bounded_tokens * float(pricing["input_price_usd_per_million"])
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
    quote_hash = _hash_json(
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
            "fact_revision_id": str(facts.id),
            "facts_hash": facts.facts_hash,
            "plan_version": plan.version,
        }
    )
    return {
        "project": project,
        "plan": plan,
        "facts": facts,
        "connection": connection,
        "pricing": pricing,
        "prompt": prompt,
        "manifest": manifest,
        "deterministic_snapshot": deterministic_snapshot,
        "snapshot": snapshot,
        "user_prompt": user_prompt,
        "estimated_cost": estimated_cost,
        "quote_hash": quote_hash,
    }


async def _prepare_seo_brief_context(
    project_id: UUID,
    plan_id: UUID,
    body: AIDraftGenerationRequest,
    auth: AuthContext,
    db: AsyncSession,
) -> dict[str, Any]:
    context = await _prepare_draft_context(project_id, plan_id, body, auth, db)
    snapshot = {
        "approved_page_plan": context["snapshot"]["approved_page_plan"],
        "approved_blocks": context["snapshot"]["allowed_blocks"],
        "confirmed_facts": context["snapshot"]["confirmed_facts"],
        "selected_keywords": context["snapshot"]["selected_keywords"],
        "validated_geo": context["snapshot"]["validated_geo"],
        "site_policy": {"canonical_path": context["plan"].slug},
    }
    prompt = load_prompt("seo/create-seo-brief.md")
    user_prompt = json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
    pricing = context["pricing"]
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
    context.update(
        snapshot=snapshot,
        prompt=prompt,
        user_prompt=user_prompt,
        estimated_cost=estimated_cost,
        quote_hash=_hash_json(
            {
                **snapshot,
                "provider_connection_id": str(context["connection"].id),
                "model": body.model,
                "max_cost_usd": body.max_cost_usd,
                "max_output_tokens": body.max_output_tokens,
                "pricing": pricing,
                "prompt_id": prompt.prompt_id,
                "prompt_version": prompt.version,
                "prompt_hash": prompt.content_hash,
                "fact_revision_id": str(context["facts"].id),
                "facts_hash": context["facts"].facts_hash,
                "plan_version": context["plan"].version,
            }
        ),
    )
    return context


@router.get("/{project_id}/seo-briefs", response_model=list[AIRunOut])
async def list_seo_briefs(
    project_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> list[AIRunOut]:
    project = await _project_or_404(db, project_id, auth)
    runs = (
        (
            await db.execute(
                select(AIRun)
                .where(
                    AIRun.project_id == project.id,
                    AIRun.tenant_id == project.tenant_id,
                    AIRun.action == "seo.create-brief",
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
    "/{project_id}/page-plans/{plan_id}/seo-brief/quote",
    response_model=ArchitectureQuoteOut,
)
async def quote_seo_brief(
    project_id: UUID,
    plan_id: UUID,
    body: AIDraftGenerationRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> ArchitectureQuoteOut:
    context = await _prepare_seo_brief_context(project_id, plan_id, body, auth, db)
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
    "/{project_id}/page-plans/{plan_id}/seo-brief",
    response_model=AIRunOut,
    status_code=201,
)
async def generate_seo_brief(
    project_id: UUID,
    plan_id: UUID,
    body: AIDraftGenerationRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> AIRunOut:
    context = await _prepare_seo_brief_context(project_id, plan_id, body, auth, db)
    if (
        not body.operator_confirmed_external_processing
        or not body.operator_confirmed_provider_budget
    ):
        raise HTTPException(status_code=409, detail={"code": "operator_confirmation_required"})
    if body.confirmed_estimated_cost_usd is None or body.quote_snapshot_hash is None:
        raise HTTPException(status_code=409, detail={"code": "cost_quote_confirmation_required"})
    if (
        abs(body.confirmed_estimated_cost_usd - context["estimated_cost"]) > 1e-8
        or body.quote_snapshot_hash != context["quote_hash"]
    ):
        raise HTTPException(status_code=409, detail={"code": "cost_quote_changed"})

    connection = context["connection"]
    pricing = context["pricing"]
    prompt = context["prompt"]
    snapshot = {
        **context["snapshot"],
        "source_binding": {
            "page_plan_id": str(context["plan"].id),
            "plan_version": context["plan"].version,
            "fact_revision_id": str(context["facts"].id),
            "facts_hash": context["facts"].facts_hash,
        },
        "spend_policy": {
            "estimated_cost_usd": round(context["estimated_cost"], 8),
            "max_cost_usd": body.max_cost_usd,
            "pricing_source": pricing["source"],
            "pricing_observed_at": pricing["observed_at"],
            "provider_budget_confirmed": True,
        },
    }
    response = None
    try:
        response = await _adapter(
            connection, decrypt_provider_key(connection.encrypted_api_key)
        ).generate_structured(
            StructuredRequest(
                model=body.model,
                system_prompt=prompt.content,
                user_prompt=context["user_prompt"],
                output_schema={
                    "type": "object",
                    "required": ["title", "description", "h1", "canonical_path", "robots"],
                },
                temperature=0.2,
                max_tokens=body.max_output_tokens,
            )
        )
        output = _validate_seo_brief(
            response.data,
            plan_slug=context["plan"].slug,
            keyword_ids={item["keyword_id"] for item in snapshot["selected_keywords"]},
            fact_keys={item["fact_key"] for item in snapshot["confirmed_facts"]},
        )
    except ProviderError as exc:
        await _record_failed_run(
            db=db,
            auth=auth,
            project_id=project_id,
            provider_id=connection.provider_id,
            model_id=body.model,
            prompt=prompt,
            snapshot=snapshot,
            error_code=exc.code,
            action="seo.create-brief",
        )
        raise HTTPException(status_code=502, detail={"code": exc.code}) from exc
    except (ValueError, TypeError) as exc:
        usage = None
        actual_cost = None
        request_id = None
        if response is not None:
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
            actual_cost = (
                response.usage.input_tokens * float(pricing["input_price_usd_per_million"])
                + response.usage.output_tokens * float(pricing["output_price_usd_per_million"])
            ) / 1_000_000
            request_id = response.request_id
        await _record_failed_run(
            db=db,
            auth=auth,
            project_id=project_id,
            provider_id=connection.provider_id,
            model_id=body.model,
            prompt=prompt,
            snapshot=snapshot,
            error_code="invalid_ai_output",
            action="seo.create-brief",
            usage=usage,
            cost_usd=actual_cost,
            request_id=request_id,
        )
        raise HTTPException(status_code=502, detail={"code": "invalid_ai_output"}) from exc

    actual_cost = (
        response.usage.input_tokens * float(pricing["input_price_usd_per_million"])
        + response.usage.output_tokens * float(pricing["output_price_usd_per_million"])
    ) / 1_000_000
    cost_exceeded = actual_cost > body.max_cost_usd
    run = AIRun(
        tenant_id=context["project"].tenant_id,
        project_id=project_id,
        action="seo.create-brief",
        status="failed" if cost_exceeded else "pending_approval",
        provider_id=response.provider_id,
        model_id=response.model,
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.version,
        prompt_hash=prompt.content_hash,
        input_snapshot_hash=_hash_json(snapshot),
        request_id=response.request_id,
        input_snapshot=snapshot,
        output={"brief": output},
        usage={
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
        cost_usd=actual_cost,
        error_code="actual_cost_exceeded_limit" if cost_exceeded else None,
    )
    db.add(run)
    await db.flush()
    await append_audit(
        db,
        action="ai.seo.brief.created" if not cost_exceeded else "ai.seo.brief.cost_limit_exceeded",
        payload={
            "run_id": str(run.id),
            "project_id": str(project_id),
            "page_plan_id": str(plan_id),
            "prompt_hash": prompt.content_hash,
            "actual_cost_usd": round(actual_cost, 8),
            "requires_operator_approval": True,
        },
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(run)
    return _run_out(run)


@router.post("/{project_id}/seo-briefs/{run_id}/drafts", status_code=201)
async def create_draft_from_seo_brief(
    project_id: UUID,
    run_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    project = await _project_or_404(db, project_id, auth)
    run = (
        await db.execute(
            select(AIRun)
            .where(
                AIRun.id == run_id,
                AIRun.project_id == project.id,
                AIRun.tenant_id == project.tenant_id,
                AIRun.action == "seo.create-brief",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="SEO brief not found")
    if run.status != "approved" or run.operator_decision != "approve":
        raise HTTPException(status_code=409, detail="Approve the SEO brief first")
    if run.output.get("page_draft_id"):
        raise HTTPException(status_code=409, detail="SEO brief already created a PageDraft")
    binding = (run.input_snapshot or {}).get("source_binding") or {}
    try:
        plan_id = UUID(binding["page_plan_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409, detail="SEO brief source snapshot is unavailable"
        ) from exc
    plan = await _plan_or_404(db, project, plan_id)
    if plan.state != "approved" or plan.version != binding.get("plan_version"):
        raise HTTPException(status_code=409, detail="PagePlan changed; regenerate the SEO brief")
    facts = (
        await db.execute(
            select(ProjectFactRevision).where(
                ProjectFactRevision.id == plan.fact_revision_id,
                ProjectFactRevision.project_id == project.id,
                ProjectFactRevision.state == "confirmed",
            )
        )
    ).scalar_one_or_none()
    if (
        not facts
        or str(facts.id) != binding.get("fact_revision_id")
        or facts.facts_hash != binding.get("facts_hash")
    ):
        raise HTTPException(
            status_code=409, detail="Confirmed facts changed; regenerate the SEO brief"
        )
    if not project.domain:
        raise HTTPException(status_code=409, detail="Set a project domain before creating a draft")
    brief = _validate_seo_brief(
        run.output.get("brief") or {},
        plan_slug=plan.slug,
        keyword_ids={item["keyword_id"] for item in (plan.keyword_snapshot or {}).get("items", [])},
        fact_keys={row["fact_key"] for row in public_fact_rows(facts.facts or {})},
    )
    manifest, input_snapshot, _ = create_page_draft(project=project, plan=plan, facts=facts)
    manifest = _apply_approved_seo_brief(manifest, brief)
    latest = (
        await db.execute(
            select(PageDraft)
            .where(PageDraft.page_plan_id == plan.id)
            .order_by(PageDraft.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    generator_meta = {
        **input_snapshot["generator_meta"],
        "seo_brief_run_id": str(run.id),
        "seo_brief_prompt_id": run.prompt_id,
        "seo_brief_prompt_version": run.prompt_version,
        "seo_brief_prompt_hash": run.prompt_hash,
        "seo_brief_provider_id": run.provider_id,
        "seo_brief_model_id": run.model_id,
        "seo_brief_cost_usd": run.cost_usd,
        "robots_recommendation": brief["robots"],
    }
    input_snapshot = {
        **input_snapshot,
        "generator_meta": generator_meta,
        "seo_brief": brief,
        "ai_provenance": {
            "provider_id": run.provider_id,
            "model_id": run.model_id,
            "prompt_id": run.prompt_id,
            "prompt_version": run.prompt_version,
            "prompt_hash": run.prompt_hash,
            "fact_keys": brief["fact_keys"],
        },
    }
    content_hash = sha256_hex(
        "\n".join(
            [
                manifest["title_template"],
                manifest["h1_template"],
                manifest["meta_description_template"],
                manifest.get("unique_core") or "",
            ]
        )
    )
    draft = PageDraft(
        page_plan_id=plan.id,
        project_id=project.id,
        tenant_id=project.tenant_id,
        revision=(latest.revision if latest else 0) + 1,
        state="draft",
        input_snapshot=input_snapshot,
        page_manifest=manifest,
        generator_meta=generator_meta,
        content_hash=content_hash,
        requested_by=auth.user.id,
    )
    db.add(draft)
    await db.flush()
    run.output = {**run.output, "page_draft_id": str(draft.id)}
    await append_audit(
        db,
        action="ai.seo.brief.page_draft.create",
        payload={
            "run_id": str(run.id),
            "page_draft_id": str(draft.id),
            "page_plan_id": str(plan.id),
            "project_id": str(project.id),
            "index_state": "noindex",
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_draft(draft)


@router.post(
    "/{project_id}/page-plans/{plan_id}/drafts/ai/quote",
    response_model=ArchitectureQuoteOut,
)
async def quote_ai_page_draft(
    project_id: UUID,
    plan_id: UUID,
    body: AIDraftGenerationRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> ArchitectureQuoteOut:
    context = await _prepare_draft_context(project_id, plan_id, body, auth, db)
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
    "/{project_id}/page-plans/{plan_id}/drafts/ai",
    status_code=201,
)
async def generate_ai_page_draft(
    project_id: UUID,
    plan_id: UUID,
    body: AIDraftGenerationRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    context = await _prepare_draft_context(project_id, plan_id, body, auth, db)
    if (
        not body.operator_confirmed_external_processing
        or not body.operator_confirmed_provider_budget
    ):
        raise HTTPException(status_code=409, detail={"code": "operator_confirmation_required"})
    if body.confirmed_estimated_cost_usd is None or body.quote_snapshot_hash is None:
        raise HTTPException(status_code=409, detail={"code": "cost_quote_confirmation_required"})
    if (
        abs(body.confirmed_estimated_cost_usd - context["estimated_cost"]) > 1e-8
        or body.quote_snapshot_hash != context["quote_hash"]
    ):
        raise HTTPException(status_code=409, detail={"code": "cost_quote_changed"})

    prompt = context["prompt"]
    pricing = context["pricing"]
    connection = context["connection"]
    snapshot = {
        **context["snapshot"],
        "spend_policy": {
            "estimated_cost_usd": round(context["estimated_cost"], 8),
            "max_cost_usd": body.max_cost_usd,
            "pricing_source": pricing["source"],
            "pricing_observed_at": pricing["observed_at"],
            "provider_budget_confirmed": True,
            "estimate_confirmed_by_operator": body.confirmed_estimated_cost_usd,
        },
    }
    response = None
    try:
        response = await _adapter(
            connection, decrypt_provider_key(connection.encrypted_api_key)
        ).generate_structured(
            StructuredRequest(
                model=body.model,
                system_prompt=prompt.content,
                user_prompt=context["user_prompt"],
                output_schema={
                    "type": "object",
                    "required": ["title", "h1", "meta_description", "unique_core", "fact_keys"],
                },
                temperature=0.2,
                max_tokens=body.max_output_tokens,
            )
        )
        output = _validate_page_copy(
            response.data,
            {row["fact_key"] for row in context["snapshot"]["confirmed_facts"]},
        )
    except ProviderError as exc:
        await _record_failed_run(
            db=db,
            auth=auth,
            project_id=project_id,
            provider_id=connection.provider_id,
            model_id=body.model,
            prompt=prompt,
            snapshot=snapshot,
            error_code=exc.code,
            action="content.page-draft-copy",
        )
        raise HTTPException(status_code=502, detail={"code": exc.code}) from exc
    except (ValueError, TypeError) as exc:
        usage = None
        actual_cost = None
        request_id = None
        if response is not None:
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
            actual_cost = (
                response.usage.input_tokens * float(pricing["input_price_usd_per_million"])
                + response.usage.output_tokens * float(pricing["output_price_usd_per_million"])
            ) / 1_000_000
            request_id = response.request_id
        await _record_failed_run(
            db=db,
            auth=auth,
            project_id=project_id,
            provider_id=connection.provider_id,
            model_id=body.model,
            prompt=prompt,
            snapshot=snapshot,
            error_code="invalid_ai_output",
            action="content.page-draft-copy",
            usage=usage,
            cost_usd=actual_cost,
            request_id=request_id,
        )
        raise HTTPException(status_code=502, detail={"code": "invalid_ai_output"}) from exc

    context["manifest"].update(
        {
            "title_template": output["title"],
            "h1_template": output["h1"],
            "meta_description_template": output["meta_description"],
            "unique_core": output["unique_core"],
        }
    )
    latest = (
        await db.execute(
            select(PageDraft)
            .where(PageDraft.page_plan_id == plan_id)
            .order_by(PageDraft.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    content_text = "\n".join(
        [output["title"], output["h1"], output["meta_description"], output["unique_core"]]
    )
    content_hash = sha256_hex(content_text)
    actual_cost = (
        response.usage.input_tokens * float(pricing["input_price_usd_per_million"])
        + response.usage.output_tokens * float(pricing["output_price_usd_per_million"])
    ) / 1_000_000
    cost_exceeded = actual_cost > body.max_cost_usd
    ai_run = AIRun(
        tenant_id=context["project"].tenant_id,
        project_id=project_id,
        action="content.page-draft-copy",
        status="failed" if cost_exceeded else "completed",
        provider_id=response.provider_id,
        model_id=response.model,
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.version,
        prompt_hash=prompt.content_hash,
        input_snapshot_hash=_hash_json(snapshot),
        request_id=response.request_id,
        input_snapshot=snapshot,
        output={"copy": output},
        usage={
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
        cost_usd=actual_cost,
        error_code="actual_cost_exceeded_limit" if cost_exceeded else None,
    )
    db.add(ai_run)
    await db.flush()
    generator_meta = {
        **(context["deterministic_snapshot"].get("generator_meta") or {}),
        "provider_id": response.provider_id,
        "model_id": response.model,
        "prompt_id": prompt.prompt_id,
        "prompt_version": prompt.version,
        "prompt_hash": prompt.content_hash,
        "ai_run_id": str(ai_run.id),
        "tokens_in": response.usage.input_tokens,
        "tokens_out": response.usage.output_tokens,
        "cost_usd": actual_cost,
        "fact_keys": output["fact_keys"],
    }
    input_snapshot = {
        **context["deterministic_snapshot"],
        "ai_provenance": generator_meta,
    }
    draft = PageDraft(
        page_plan_id=plan_id,
        project_id=project_id,
        tenant_id=context["project"].tenant_id,
        revision=(latest.revision if latest else 0) + 1,
        state="failed" if cost_exceeded else "draft",
        input_snapshot=input_snapshot,
        page_manifest=context["manifest"],
        generator_meta=generator_meta,
        content_hash=content_hash,
        requested_by=auth.user.id,
        failure_message="Provider-reported usage exceeded the confirmed estimate limit"
        if cost_exceeded
        else None,
    )
    db.add(draft)
    await db.flush()
    await append_audit(
        db,
        action="page_draft.ai_generate",
        payload={
            "project_id": str(project_id),
            "page_plan_id": str(plan_id),
            "page_draft_id": str(draft.id),
            "ai_run_id": str(ai_run.id),
            "provider_id": response.provider_id,
            "model_id": response.model,
            "prompt_hash": prompt.content_hash,
            "content_hash": content_hash,
            "estimated_cost_usd": round(context["estimated_cost"], 8),
            "actual_cost_usd": round(actual_cost, 8),
            "cost_exceeded": cost_exceeded,
            "requires_operator_review": True,
        },
        tenant_id=context["project"].tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_draft(draft)


async def _prepare_block_slot_context(
    project_id: UUID,
    plan_id: UUID,
    body: AIBlockSlotCopyRequest,
    auth: AuthContext,
    db: AsyncSession,
) -> dict[str, Any]:
    context = await _prepare_draft_context(project_id, plan_id, body, auth, db)
    selected_blocks = (context["plan"].block_selection or {}).get("blocks", [])
    if body.block_id not in selected_blocks:
        raise HTTPException(
            status_code=422, detail="Block is not selected by the approved PagePlan"
        )
    try:
        slot_schema = block_slot_schema(context["plan"].kit_key, body.block_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail="Curated block catalog changed") from exc
    if not slot_schema:
        raise HTTPException(
            status_code=409, detail="This curated block has no AI-writable text slots"
        )
    snapshot = {
        "approved_page_plan": context["snapshot"]["approved_page_plan"],
        "block": {"id": body.block_id, "slot_schema": slot_schema},
        "confirmed_facts": context["snapshot"]["confirmed_facts"],
        "selected_keywords": context["snapshot"]["selected_keywords"],
        "validated_geo": context["snapshot"]["validated_geo"],
    }
    prompt = load_prompt("content/block-slot-copy.md")
    user_prompt = json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
    pricing = context["pricing"]
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
            **snapshot,
            "provider_connection_id": str(context["connection"].id),
            "model": body.model,
            "max_cost_usd": body.max_cost_usd,
            "max_output_tokens": body.max_output_tokens,
            "pricing": pricing,
            "prompt_id": prompt.prompt_id,
            "prompt_version": prompt.version,
            "prompt_hash": prompt.content_hash,
            "fact_revision_id": str(context["facts"].id),
            "facts_hash": context["facts"].facts_hash,
            "plan_version": context["plan"].version,
        }
    )
    context.update(
        slot_schema=slot_schema,
        snapshot=snapshot,
        prompt=prompt,
        user_prompt=user_prompt,
        estimated_cost=estimated_cost,
        quote_hash=quote_hash,
    )
    return context


@router.get("/{project_id}/block-slot-proposals", response_model=list[AIRunOut])
async def list_block_slot_proposals(
    project_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> list[AIRunOut]:
    project = await _project_or_404(db, project_id, auth)
    runs = (
        (
            await db.execute(
                select(AIRun)
                .where(
                    AIRun.project_id == project.id,
                    AIRun.tenant_id == project.tenant_id,
                    AIRun.action == "content.block-slot-copy",
                )
                .order_by(AIRun.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return [_run_out(run) for run in runs]


@router.get("/{project_id}/page-plans/{plan_id}/blocks/{block_id}/slot-schema")
async def get_block_slot_schema(
    project_id: UUID,
    plan_id: UUID,
    block_id: str,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    project = await _project_or_404(db, project_id, auth)
    plan = await _plan_or_404(db, project, plan_id)
    if plan.state != "approved" or block_id not in (plan.block_selection or {}).get("blocks", []):
        raise HTTPException(status_code=404, detail="Approved selected block not found")
    try:
        return {"block_id": block_id, "slots": block_slot_schema(plan.kit_key, block_id)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail="Curated block catalog changed") from exc


@router.post(
    "/{project_id}/page-plans/{plan_id}/block-slot-copy/quote",
    response_model=ArchitectureQuoteOut,
)
async def quote_block_slot_copy(
    project_id: UUID,
    plan_id: UUID,
    body: AIBlockSlotCopyRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> ArchitectureQuoteOut:
    context = await _prepare_block_slot_context(project_id, plan_id, body, auth, db)
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
    "/{project_id}/page-plans/{plan_id}/block-slot-copy",
    response_model=AIRunOut,
    status_code=201,
)
async def generate_block_slot_copy(
    project_id: UUID,
    plan_id: UUID,
    body: AIBlockSlotCopyRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> AIRunOut:
    context = await _prepare_block_slot_context(project_id, plan_id, body, auth, db)
    if (
        not body.operator_confirmed_external_processing
        or not body.operator_confirmed_provider_budget
    ):
        raise HTTPException(status_code=409, detail={"code": "operator_confirmation_required"})
    if body.confirmed_estimated_cost_usd is None or body.quote_snapshot_hash is None:
        raise HTTPException(status_code=409, detail={"code": "cost_quote_confirmation_required"})
    if (
        abs(body.confirmed_estimated_cost_usd - context["estimated_cost"]) > 1e-8
        or body.quote_snapshot_hash != context["quote_hash"]
    ):
        raise HTTPException(status_code=409, detail={"code": "cost_quote_changed"})

    connection = context["connection"]
    pricing = context["pricing"]
    prompt = context["prompt"]
    snapshot = {
        **context["snapshot"],
        "source_binding": {
            "page_plan_id": str(context["plan"].id),
            "plan_version": context["plan"].version,
            "fact_revision_id": str(context["facts"].id),
            "facts_hash": context["facts"].facts_hash,
        },
        "spend_policy": {
            "estimated_cost_usd": round(context["estimated_cost"], 8),
            "max_cost_usd": body.max_cost_usd,
            "pricing_source": pricing["source"],
            "pricing_observed_at": pricing["observed_at"],
            "provider_budget_confirmed": True,
        },
    }
    response = None
    try:
        response = await _adapter(
            connection, decrypt_provider_key(connection.encrypted_api_key)
        ).generate_structured(
            StructuredRequest(
                model=body.model,
                system_prompt=prompt.content,
                user_prompt=context["user_prompt"],
                output_schema={
                    "type": "object",
                    "required": ["block_id", "slots", "fact_keys", "warnings"],
                },
                temperature=0.2,
                max_tokens=body.max_output_tokens,
            )
        )
        output = _validate_block_slot_copy(
            response.data,
            block_id=body.block_id,
            slot_schema=context["slot_schema"],
            fact_keys={row["fact_key"] for row in context["snapshot"]["confirmed_facts"]},
        )
    except ProviderError as exc:
        await _record_failed_run(
            db=db,
            auth=auth,
            project_id=project_id,
            provider_id=connection.provider_id,
            model_id=body.model,
            prompt=prompt,
            snapshot=snapshot,
            error_code=exc.code,
            action="content.block-slot-copy",
        )
        raise HTTPException(status_code=502, detail={"code": exc.code}) from exc
    except (ValueError, TypeError) as exc:
        usage = None
        actual_cost = None
        request_id = None
        if response is not None:
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
            actual_cost = (
                response.usage.input_tokens * float(pricing["input_price_usd_per_million"])
                + response.usage.output_tokens * float(pricing["output_price_usd_per_million"])
            ) / 1_000_000
            request_id = response.request_id
        await _record_failed_run(
            db=db,
            auth=auth,
            project_id=project_id,
            provider_id=connection.provider_id,
            model_id=body.model,
            prompt=prompt,
            snapshot=snapshot,
            error_code="invalid_ai_output",
            action="content.block-slot-copy",
            usage=usage,
            cost_usd=actual_cost,
            request_id=request_id,
        )
        raise HTTPException(status_code=502, detail={"code": "invalid_ai_output"}) from exc

    actual_cost = (
        response.usage.input_tokens * float(pricing["input_price_usd_per_million"])
        + response.usage.output_tokens * float(pricing["output_price_usd_per_million"])
    ) / 1_000_000
    cost_exceeded = actual_cost > body.max_cost_usd
    run = AIRun(
        tenant_id=context["project"].tenant_id,
        project_id=project_id,
        action="content.block-slot-copy",
        status="failed" if cost_exceeded else "pending_approval",
        provider_id=response.provider_id,
        model_id=response.model,
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.version,
        prompt_hash=prompt.content_hash,
        input_snapshot_hash=_hash_json(snapshot),
        request_id=response.request_id,
        input_snapshot=snapshot,
        output={"slot_copy": output},
        usage={
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
        cost_usd=actual_cost,
        error_code="actual_cost_exceeded_limit" if cost_exceeded else None,
    )
    db.add(run)
    await db.flush()
    await append_audit(
        db,
        action="ai.block_slot_copy.created"
        if not cost_exceeded
        else "ai.block_slot_copy.cost_limit_exceeded",
        payload={
            "run_id": str(run.id),
            "project_id": str(project_id),
            "page_plan_id": str(plan_id),
            "block_id": body.block_id,
            "actual_cost_usd": round(actual_cost, 8),
            "requires_operator_approval": True,
        },
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(run)
    return _run_out(run)


@router.post("/{project_id}/block-slot-proposals/{run_id}/drafts", status_code=201)
async def create_draft_from_block_slot_proposal(
    project_id: UUID,
    run_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    project = await _project_or_404(db, project_id, auth)
    run = (
        await db.execute(
            select(AIRun)
            .where(
                AIRun.id == run_id,
                AIRun.project_id == project.id,
                AIRun.tenant_id == project.tenant_id,
                AIRun.action == "content.block-slot-copy",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Block slot proposal not found")
    if run.status != "approved" or run.operator_decision != "approve":
        raise HTTPException(status_code=409, detail="Approve the block slot proposal first")
    if run.output.get("page_draft_id"):
        raise HTTPException(
            status_code=409, detail="Block slot proposal already created a PageDraft"
        )
    binding = (run.input_snapshot or {}).get("source_binding") or {}
    try:
        plan_id = UUID(binding["page_plan_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409, detail="Block slot proposal source snapshot is unavailable"
        ) from exc
    plan = await _plan_or_404(db, project, plan_id)
    if plan.state != "approved" or plan.version != binding.get("plan_version"):
        raise HTTPException(
            status_code=409, detail="PagePlan changed; regenerate the block slot proposal"
        )
    facts = (
        await db.execute(
            select(ProjectFactRevision).where(
                ProjectFactRevision.id == plan.fact_revision_id,
                ProjectFactRevision.project_id == project.id,
                ProjectFactRevision.state == "confirmed",
            )
        )
    ).scalar_one_or_none()
    if (
        not facts
        or str(facts.id) != binding.get("fact_revision_id")
        or facts.facts_hash != binding.get("facts_hash")
    ):
        raise HTTPException(
            status_code=409, detail="Confirmed facts changed; regenerate the block slot proposal"
        )
    if not project.domain:
        raise HTTPException(status_code=409, detail="Set a project domain before creating a draft")
    proposal = run.output.get("slot_copy") or {}
    block_id = proposal.get("block_id")
    if not isinstance(block_id, str) or block_id not in (plan.block_selection or {}).get(
        "blocks", []
    ):
        raise HTTPException(
            status_code=409, detail="Selected block changed; regenerate the block slot proposal"
        )
    try:
        slot_schema = block_slot_schema(plan.kit_key, block_id)
        output = _validate_block_slot_copy(
            proposal,
            block_id=block_id,
            slot_schema=slot_schema,
            fact_keys={row["fact_key"] for row in public_fact_rows(facts.facts or {})},
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=409, detail="Block slot proposal is no longer valid"
        ) from exc
    manifest, input_snapshot, _ = create_page_draft(project=project, plan=plan, facts=facts)
    if set(output["slots"]) != {"unique_core"}:
        raise HTTPException(status_code=409, detail="Unsupported block slot contract")
    manifest["unique_core"] = output["slots"]["unique_core"] or ""
    manifest["index_state"] = "noindex"
    latest = (
        await db.execute(
            select(PageDraft)
            .where(PageDraft.page_plan_id == plan.id)
            .order_by(PageDraft.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    generator_meta = {
        **input_snapshot["generator_meta"],
        "block_slot_copy_run_id": str(run.id),
        "block_id": block_id,
        "provider_id": run.provider_id,
        "model_id": run.model_id,
        "prompt_id": run.prompt_id,
        "prompt_version": run.prompt_version,
        "prompt_hash": run.prompt_hash,
        "cost_usd": run.cost_usd,
        "fact_keys": output["fact_keys"],
    }
    input_snapshot = {
        **input_snapshot,
        "generator_meta": generator_meta,
        "ai_provenance": {"fact_keys": output["fact_keys"], "block_slot_copy": generator_meta},
    }
    content_hash = sha256_hex(
        "\n".join(
            [
                manifest["title_template"],
                manifest["h1_template"],
                manifest["meta_description_template"],
                manifest.get("unique_core") or "",
            ]
        )
    )
    draft = PageDraft(
        page_plan_id=plan.id,
        project_id=project.id,
        tenant_id=project.tenant_id,
        revision=(latest.revision if latest else 0) + 1,
        state="draft",
        input_snapshot=input_snapshot,
        page_manifest=manifest,
        generator_meta=generator_meta,
        content_hash=content_hash,
        requested_by=auth.user.id,
    )
    db.add(draft)
    await db.flush()
    run.output = {**run.output, "page_draft_id": str(draft.id)}
    await append_audit(
        db,
        action="ai.block_slot_copy.page_draft.create",
        payload={
            "run_id": str(run.id),
            "page_draft_id": str(draft.id),
            "page_plan_id": str(plan.id),
            "project_id": str(project.id),
            "block_id": block_id,
        },
        tenant_id=project.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_draft(draft)


__all__ = ["router"]
