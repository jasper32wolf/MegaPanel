from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.api.v1.ai_providers import _adapter
from app.api.v1.ai_workspace import _record_failed_run
from app.api.v1.projects import _plan_or_404, _project_or_404, _serialize_draft
from app.core.security import sha256_hex
from app.db.session import get_db
from app.models import AIProviderConnection, AIRun, PageDraft, ProjectFactRevision
from app.providers import ProviderError, StructuredRequest
from app.schemas.ai import AIDraftGenerationRequest, AIDraftTextOut, ArchitectureQuoteOut
from app.services.ai_secrets import decrypt_provider_key
from app.services.audit import append_audit
from app.services.generation import create_page_draft
from app.services.prompt_catalog import load_prompt
from fastapi import APIRouter, Depends, HTTPException
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
    fact_values = facts.facts or {}
    fact_rows = (
        [{"fact_key": str(key), "value": value} for key, value in fact_values.items()]
        if isinstance(fact_values, dict)
        else []
    )
    snapshot = {
        "project": {"name": project.name, "locale": project.locale, "niche": project.niche},
        "approved_page_plan": {
            "id": str(plan.id),
            "slug": plan.slug,
            "objective": plan.objective,
            "intent": plan.intent,
            "kit_key": plan.kit_key,
            "block_selection": plan.block_selection or {},
        },
        "confirmed_facts": fact_rows,
        "selected_keywords": (plan.keyword_snapshot or {}).get("items", []),
        "validated_geo": (plan.geo_snapshot or {}).get("items", []),
        "allowed_blocks": list((plan.block_selection or {}).get("blocks", [])),
        "fact_revision_id": str(facts.id),
        "facts_hash": facts.facts_hash,
        "deterministic_input": deterministic_snapshot,
    }
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


__all__ = ["router"]
