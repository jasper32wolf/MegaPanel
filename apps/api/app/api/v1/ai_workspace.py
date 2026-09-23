from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.api.v1.ai_providers import _adapter
from app.api.v1.projects import _confirmed_facts, _project_or_404, _selection_snapshots
from app.db.session import get_db
from app.models import AIProviderConnection, AIRun, PagePlan, Project
from app.providers import ProviderError, StructuredRequest
from app.schemas.ai import (
    ArchitectureProposalOut,
    ArchitectureProposalRequest,
    ArchitectureQuoteOut,
    PageProposal,
)
from app.services.ai_secrets import decrypt_provider_key
from app.services.audit import append_audit
from app.services.prompt_catalog import list_prompts, load_prompt
from fastapi import APIRouter, Depends, HTTPException
from site_panel_blocks import list_kits
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


def _hash_snapshot(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _proposal_out(run: AIRun) -> ArchitectureProposalOut:
    return ArchitectureProposalOut(
        run_id=run.id,
        status=run.status,
        pages=[PageProposal.model_validate(item) for item in run.output.get("pages", [])],
        prompt_id=run.prompt_id,
        prompt_version=run.prompt_version,
        prompt_hash=run.prompt_hash,
        input_snapshot_hash=run.input_snapshot_hash,
        estimated_cost_usd=run.input_snapshot.get("spend_policy", {}).get("estimated_cost_usd"),
        max_cost_usd=run.input_snapshot.get("spend_policy", {}).get("max_cost_usd"),
        page_plan_ids=[UUID(item) for item in run.output.get("page_plan_ids", [])],
        page_plans_imported=run.output.get("page_plans_imported", False),
        error_code=run.error_code,
    )


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
    usage: dict[str, int] | None = None,
    cost_usd: float | None = None,
    request_id: str | None = None,
) -> None:
    run = AIRun(
        tenant_id=auth.tenant_id,
        project_id=project_id,
        action="architecture.site-map",
        status="failed",
        provider_id=provider_id,
        model_id=model_id,
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.version,
        prompt_hash=prompt.content_hash,
        input_snapshot_hash=_hash_snapshot(snapshot),
        request_id=request_id,
        input_snapshot=snapshot,
        output={"pages": []},
        usage=usage or {},
        cost_usd=cost_usd,
        error_code=error_code,
    )
    db.add(run)
    await db.flush()
    await append_audit(
        db,
        action="ai.architecture.proposal.failed",
        payload={
            "run_id": str(run.id),
            "project_id": str(project_id),
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
        if (
            not page.slug.startswith("/")
            or ".." in page.slug.split("/")
            or "\\" in page.slug
            or "?" in page.slug
            or "#" in page.slug
            or "//" in page.slug
            or any(ord(char) < 32 for char in page.slug)
        ):
            raise ValueError("Architecture output contains an invalid slug")
        result.append(page.model_dump(mode="json"))
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

    prompt = load_prompt("architecture/propose-site-map.md")
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
    kits = list_kits()
    catalogs = {item["key"]: set(item["blocks"]) for item in kits}
    facts = facts_revision.facts or {}
    fact_rows = (
        [{"fact_key": str(key), "value": value} for key, value in facts.items()]
        if isinstance(facts, dict)
        else []
    )
    snapshot = {
        "project": {"name": project.name, "locale": project.locale, "niche": project.niche},
        "confirmed_facts": fact_rows,
        "selected_keywords": keyword_snapshot["items"],
        "validated_geo": geo_snapshot["items"],
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
    if len(json.dumps(snapshot, ensure_ascii=False)) > 64_000:
        raise HTTPException(status_code=413, detail="Project context exceeds the AI request limit")
    prompt = load_prompt("architecture/propose-site-map.md")
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
        "user_prompt": user_prompt,
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


@router.get("/prompts")
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
    facts = context["facts"]
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
    adapter = _adapter(connection, decrypt_provider_key(connection.encrypted_api_key))
    response = None
    try:
        response = await adapter.generate_structured(
            StructuredRequest(
                model=body.model,
                system_prompt=prompt.content,
                user_prompt=user_prompt,
                output_schema={"type": "object", "required": ["pages"]},
                temperature=0.2,
                max_tokens=body.max_output_tokens,
            )
        )
        page_proposals = _validate_proposal(
            response.data,
            keyword_ids={item["keyword_id"] for item in keyword_snapshot["items"]},
            geo_ids={item["geo_id"] for item in geo_snapshot["items"]},
            fact_keys={str(key) for key in facts} if isinstance(facts, dict) else set(),
            catalogs=catalogs,
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
            usage=usage,
            cost_usd=actual_cost,
            request_id=request_id,
        )
        raise HTTPException(status_code=502, detail={"code": "invalid_ai_output"}) from exc

    snapshot_hash = _hash_snapshot(snapshot)
    actual_cost = (
        response.usage.input_tokens * float(pricing["input_price_usd_per_million"])
        + response.usage.output_tokens * float(pricing["output_price_usd_per_million"])
    ) / 1_000_000
    cost_exceeded = actual_cost > body.max_cost_usd
    run = AIRun(
        tenant_id=auth.tenant_id,
        project_id=project_id,
        action="architecture.site-map",
        status="failed" if cost_exceeded else "pending_approval",
        provider_id=response.provider_id,
        model_id=response.model,
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.version,
        prompt_hash=prompt.content_hash,
        input_snapshot_hash=snapshot_hash,
        request_id=response.request_id,
        input_snapshot=snapshot,
        output={"pages": page_proposals},
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
        action=(
            "ai.architecture.proposal.cost_limit_exceeded"
            if cost_exceeded
            else "ai.architecture.proposal.created"
        ),
        payload={
            "run_id": str(run.id),
            "project_id": str(project_id),
            "provider_id": response.provider_id,
            "model_id": response.model,
            "prompt_id": prompt.prompt_id,
            "prompt_version": prompt.version,
            "prompt_hash": prompt.content_hash,
            "estimated_cost_usd": round(estimated_cost, 8),
            "actual_cost_usd": round(actual_cost, 8),
            "requires_operator_approval": True,
        },
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(run)
    return _proposal_out(run)


@router.get("/runs/{run_id}", response_model=ArchitectureProposalOut)
async def get_ai_run(
    run_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> ArchitectureProposalOut:
    run = (
        await db.execute(select(AIRun).where(AIRun.id == run_id, AIRun.tenant_id == auth.tenant_id))
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="AI run not found")
    return _proposal_out(run)


@router.post("/runs/{run_id}/decision", response_model=ArchitectureProposalOut)
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
    return _proposal_out(run)


@router.post("/runs/{run_id}/page-plans", response_model=ArchitectureProposalOut)
async def create_page_plans_from_proposal(
    run_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> ArchitectureProposalOut:
    run = (
        await db.execute(
            select(AIRun)
            .where(AIRun.id == run_id, AIRun.tenant_id == auth.tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="AI run not found")
    if run.status != "approved" or run.operator_decision != "approve":
        raise HTTPException(status_code=409, detail="Approve the architecture proposal first")
    if run.output.get("page_plans_imported"):
        raise HTTPException(
            status_code=409, detail="PagePlans were already created from this proposal"
        )
    if run.project_id is None:
        raise HTTPException(status_code=409, detail="AI run is not associated with a project")
    project = await _project_or_404(db, run.project_id, auth)
    pages = [PageProposal.model_validate(item) for item in run.output.get("pages", [])]
    kit_map = {item["key"]: set(item["blocks"]) for item in list_kits()}
    existing = list(
        (
            await db.execute(
                select(PagePlan).where(PagePlan.project_id == project.id).order_by(PagePlan.version)
            )
        )
        .scalars()
        .all()
    )
    latest_by_slug: dict[str, PagePlan] = {}
    for plan in existing:
        latest = latest_by_slug.get(plan.slug)
        if latest is None or plan.version > latest.version:
            latest_by_slug[plan.slug] = plan
    for page in pages:
        if page.kit_key not in kit_map or any(
            block_id not in kit_map[page.kit_key] for block_id in page.block_ids
        ):
            raise HTTPException(
                status_code=409,
                detail="Curated kit catalog changed; regenerate the proposal",
            )
        latest = latest_by_slug.get(page.slug)
        if latest and latest.state in {"draft", "review"}:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "page_plan_conflict",
                    "slug": page.slug,
                    "page_plan_id": str(latest.id),
                },
            )
    created: list[PagePlan] = []
    for page in pages:
        latest = latest_by_slug.get(page.slug)
        plan = PagePlan(
            project_id=project.id,
            tenant_id=project.tenant_id,
            supersedes_id=latest.id if latest else None,
            version=latest.version + 1 if latest else 1,
            slug=page.slug,
            objective=page.title,
            intent=page.purpose[:128],
            risk_notes="\n".join(page.uncertainty_notes) or None,
            kit_key=page.kit_key,
            block_selection={"blocks": page.block_ids},
            source_refs={
                "ai_run_id": str(run.id),
                "prompt_id": run.prompt_id,
                "prompt_version": run.prompt_version,
                "prompt_hash": run.prompt_hash,
                "keyword_ids": [str(item) for item in page.keyword_ids],
                "geo_ids": [str(item) for item in page.geo_ids],
                "fact_keys": page.fact_keys,
            },
        )
        db.add(plan)
        created.append(plan)
        latest_by_slug[page.slug] = plan
    await db.flush()
    run.output = {
        **run.output,
        "page_plan_ids": [str(plan.id) for plan in created],
        "page_plans_imported": True,
    }
    await append_audit(
        db,
        action="ai.architecture.page_plans.created",
        payload={
            "run_id": str(run.id),
            "project_id": str(project.id),
            "page_plan_ids": [str(plan.id) for plan in created],
        },
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(run)
    return _proposal_out(run)
