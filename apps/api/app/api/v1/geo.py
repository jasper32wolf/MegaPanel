from __future__ import annotations

import csv
import io
import json
import math
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.api.v1.ai_providers import _adapter
from app.api.v1.ai_workspace import _actual_cost, _reserve_ai_run, _run_out, _usage_payload
from app.core.security import sha256_hex
from app.db.session import get_db
from app.models import AIProviderConnection, AIRun, GeoPlace
from app.providers import ProviderError, StructuredRequest
from app.schemas.ai import (
    ArchitectureQuoteOut,
    GeoAIProposalApply,
    GeoAIProposalNode,
    GeoAIProposalOut,
    GeoAIProposalRequest,
)
from app.schemas.phase2 import (
    GeoCreate,
    GeoOut,
    GeoUpdate,
    MorphOut,
    MorphRequest,
    ToponymValidateRequest,
)
from app.services.ai_data_policy import safe_provider_context
from app.services.ai_secrets import decrypt_provider_key
from app.services.audit import append_audit
from app.services.geo import place_context, update_place, upsert_place, validate_toponym
from app.services.managed_prompts import active_prompt
from app.services.morph import city_placeholders, inflect_cases
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()

GEO_KINDS = {"country", "region", "city", "district", "street", "metro", "landmark"}
MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_IMPORT_ROWS = 50_000
TEMPLATE = (
    "kind,external_id,name,parent_external_id,lat,lon,timezone,population\n"
    "city,manual-moscow,Москва,,55.7558,37.6173,Europe/Moscow,12600000\n"
    "district,manual-moscow-sokol,Сокол,manual-moscow,,,,\n"
)


def parse_geo_csv(raw: bytes, *, delimiter: str) -> tuple[list[dict], list[dict]]:
    if len(raw) > MAX_IMPORT_BYTES:
        raise ValueError("File exceeds 10 MB")
    if delimiter not in {",", ";", "\t"}:
        raise ValueError("Unsupported CSV delimiter")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV must be UTF-8 encoded") from exc

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    required = {"kind", "external_id", "name"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError("CSV must include kind, external_id, and name columns")

    rows: list[dict] = []
    errors: list[dict] = []
    for line, source in enumerate(reader, start=2):
        if line > MAX_IMPORT_ROWS + 1:
            raise ValueError(f"CSV exceeds {MAX_IMPORT_ROWS} rows")
        kind = (source.get("kind") or "").strip().lower()
        external_id = (source.get("external_id") or "").strip()
        name = (source.get("name") or "").strip()
        if kind not in GEO_KINDS or not external_id or not name:
            if len(errors) < 100:
                errors.append({"line": line, "error": "kind, external_id, and name are required"})
            continue
        try:
            rows.append(
                {
                    "kind": kind,
                    "line": line,
                    "external_id": external_id,
                    "name": name,
                    "parent_external_id": (source.get("parent_external_id") or "").strip() or None,
                    "lat": float(source["lat"]) if (source.get("lat") or "").strip() else None,
                    "lon": float(source["lon"]) if (source.get("lon") or "").strip() else None,
                    "timezone": (source.get("timezone") or "").strip() or None,
                    "population": int(source["population"])
                    if (source.get("population") or "").strip()
                    else None,
                }
            )
        except ValueError:
            if len(errors) < 100:
                errors.append({"line": line, "error": "Invalid coordinate or population"})
    return rows, errors


def _geo_snapshot_hash(value: dict[str, Any]) -> str:
    return sha256_hex(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _validate_geo_proposal(value: dict[str, Any], *, max_places: int) -> dict[str, Any]:
    proposal = GeoAIProposalOut.model_validate(value)
    if len(proposal.nodes) > max_places:
        raise ValueError("Geo proposal exceeds the requested place limit")
    nodes = {node.key: node for node in proposal.nodes}
    if len(nodes) != len(proposal.nodes):
        raise ValueError("Geo proposal contains duplicate keys")
    seen_names: set[tuple[str, str, str]] = set()
    for node in proposal.nodes:
        if any(char in node.name for char in "<>{}") or any(ord(char) < 32 for char in node.name):
            raise ValueError("Geo proposal name must be plain text")
        if node.kind in {"district", "metro"} and node.parent_key != "city":
            raise ValueError("District and metro proposals must be direct city children")
        if node.kind == "landmark" and node.parent_key != "city":
            parent = nodes.get(node.parent_key)
            if parent is None or parent.kind not in {"district", "metro"}:
                raise ValueError("Landmark proposal has an invalid parent key")
        identity = (node.kind, node.name.casefold(), node.parent_key)
        if identity in seen_names:
            raise ValueError("Geo proposal contains duplicate places")
        seen_names.add(identity)
    return proposal.model_dump(mode="json")


async def _prepare_geo_proposal_context(
    body: GeoAIProposalRequest,
    auth: AuthContext,
    db: AsyncSession,
) -> dict[str, Any]:
    if auth.tenant_id is None:
        raise HTTPException(status_code=403, detail="Tenant required")
    city = await db.get(GeoPlace, body.city_id)
    if city is None or city.kind != "city":
        raise HTTPException(status_code=404, detail="City not found")
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
    existing = list(
        (
            await db.execute(
                select(GeoPlace)
                .where(GeoPlace.parent_id == city.id)
                .order_by(GeoPlace.kind, GeoPlace.name)
            )
        )
        .scalars()
        .all()
    )
    snapshot = {
        "city": {"id": str(city.id), "name": city.name},
        "existing_children": [
            {"kind": place.kind, "name": place.name, "parent_id": str(place.parent_id)}
            for place in existing
        ],
        "operator_guidance": body.operator_guidance,
        "max_places": body.max_places,
    }
    try:
        snapshot = safe_provider_context(snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "unsafe_ai_context"}) from exc
    prompt = await active_prompt(
        db,
        tenant_id=auth.tenant_id,
        relative_path="geo/propose-city-hierarchy.md",
    )
    user_prompt = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
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
    from app.services.ai_budget import enforce_ai_budget

    await enforce_ai_budget(db, tenant_id=auth.tenant_id, estimated_cost_usd=estimated_cost)
    quote_snapshot_hash = _geo_snapshot_hash(
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
        "city": city,
        "connection": connection,
        "pricing": pricing,
        "prompt": prompt,
        "snapshot": snapshot,
        "user_prompt": user_prompt,
        "estimated_cost": estimated_cost,
        "quote_snapshot_hash": quote_snapshot_hash,
    }


@router.post("/ai-proposals/quote", response_model=ArchitectureQuoteOut)
async def quote_geo_proposal(
    body: GeoAIProposalRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> ArchitectureQuoteOut:
    context = await _prepare_geo_proposal_context(body, auth, db)
    return ArchitectureQuoteOut(
        provider_id=context["connection"].provider_id,
        model_id=body.model,
        estimated_cost_usd=round(context["estimated_cost"], 8),
        max_cost_usd=body.max_cost_usd,
        input_snapshot_hash=context["quote_snapshot_hash"],
        pricing_source=context["pricing"]["source"],
        pricing_observed_at=context["pricing"]["observed_at"],
    )


@router.post("/ai-proposals", response_model=dict, status_code=202)
async def propose_geo_hierarchy(
    body: GeoAIProposalRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    context = await _prepare_geo_proposal_context(body, auth, db)
    if (
        not body.operator_confirmed_external_processing
        or not body.operator_confirmed_provider_budget
    ):
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

    snapshot = {
        **context["snapshot"],
        "spend_policy": {
            "estimated_cost_usd": round(context["estimated_cost"], 8),
            "max_cost_usd": body.max_cost_usd,
            "pricing_source": context["pricing"]["source"],
            "pricing_observed_at": context["pricing"]["observed_at"],
            "provider_budget_confirmed": True,
            "estimate_confirmed_by_operator": body.confirmed_estimated_cost_usd,
        },
    }
    reservation = await _reserve_ai_run(
        db=db,
        auth=auth,
        project_id=None,
        provider_id=context["connection"].provider_id,
        model_id=body.model,
        prompt=context["prompt"],
        snapshot=snapshot,
        estimated_cost_usd=context["estimated_cost"],
        action="geo.city-hierarchy",
    )
    adapter = _adapter(
        context["connection"], decrypt_provider_key(context["connection"].encrypted_api_key)
    )
    response = None
    try:
        response = await adapter.generate_structured(
            StructuredRequest(
                model=body.model,
                system_prompt=context["prompt"].content,
                user_prompt=context["user_prompt"],
                output_schema={"type": "object", "required": ["nodes", "warnings"]},
                temperature=0.1,
                max_tokens=body.max_output_tokens,
            )
        )
        proposal = _validate_geo_proposal(response.data, max_places=body.max_places)
    except ProviderError as exc:
        reservation.status = "failed"
        reservation.error_code = exc.code
        reservation.usage = _usage_payload(exc.usage)
        reservation.cost_usd = _actual_cost(
            exc.usage, context["pricing"], context["estimated_cost"]
        )
        await db.commit()
        raise HTTPException(status_code=502, detail={"code": exc.code}) from exc
    except (ValueError, TypeError) as exc:
        reservation.status = "failed"
        reservation.error_code = "invalid_ai_output"
        reservation.usage = _usage_payload(response.usage if response is not None else None)
        reservation.cost_usd = _actual_cost(
            response.usage if response is not None else None,
            context["pricing"],
            context["estimated_cost"],
        )
        await db.commit()
        raise HTTPException(status_code=502, detail={"code": "invalid_ai_output"}) from exc

    await db.delete(reservation)
    actual_cost = _actual_cost(response.usage, context["pricing"], context["estimated_cost"])
    cost_exceeded = actual_cost > body.max_cost_usd
    run = AIRun(
        tenant_id=auth.tenant_id,
        action="geo.city-hierarchy",
        status="failed" if cost_exceeded else "pending_approval",
        provider_id=response.provider_id,
        model_id=response.model,
        prompt_id=context["prompt"].prompt_id,
        prompt_version=context["prompt"].version,
        prompt_hash=context["prompt"].content_hash,
        input_snapshot_hash=_geo_snapshot_hash(snapshot),
        request_id=response.request_id,
        input_snapshot=snapshot,
        output=proposal,
        usage=_usage_payload(response.usage),
        cost_usd=actual_cost,
        error_code="actual_cost_exceeded_limit" if cost_exceeded else None,
    )
    db.add(run)
    await db.flush()
    await append_audit(
        db,
        action="ai.geo.proposal.created"
        if not cost_exceeded
        else "ai.geo.proposal.cost_limit_exceeded",
        payload={
            "run_id": str(run.id),
            "city_id": str(context["city"].id),
            "provider_id": response.provider_id,
            "model_id": response.model,
            "prompt_hash": context["prompt"].content_hash,
            "requires_operator_approval": True,
        },
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(run)
    return _run_out(run).model_dump(mode="json")


@router.post("/ai-proposals/{run_id}/apply", response_model=dict)
async def apply_geo_proposal(
    run_id: UUID,
    body: GeoAIProposalApply,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    run = (
        await db.execute(
            select(AIRun)
            .where(AIRun.id == run_id, AIRun.tenant_id == auth.tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="AI run not found")
    if run.action != "geo.city-hierarchy":
        raise HTTPException(status_code=409, detail="AI run is not a geography proposal")
    if run.status != "approved" or run.operator_decision != "approve":
        raise HTTPException(status_code=409, detail="Approve the geography proposal first")
    if run.output.get("applied"):
        raise HTTPException(status_code=409, detail="Geography proposal was already applied")
    nodes = body.nodes or [GeoAIProposalNode.model_validate(item) for item in run.output["nodes"]]
    proposal = _validate_geo_proposal({"nodes": nodes, "warnings": []}, max_places=50)
    city_id = UUID(run.input_snapshot["city"]["id"])
    city = await db.get(GeoPlace, city_id)
    if city is None or city.kind != "city":
        raise HTTPException(status_code=409, detail="Proposal city is no longer available")
    parents: dict[str, GeoPlace] = {"city": city}
    created_ids: list[str] = []
    skipped_ids: list[str] = []
    pending = list(proposal["nodes"])
    while pending:
        resolved = False
        for node in tuple(pending):
            parent = parents.get(node["parent_key"])
            if parent is None:
                continue
            existing = (
                await db.execute(
                    select(GeoPlace)
                    .where(
                        GeoPlace.kind == node["kind"],
                        GeoPlace.name.ilike(node["name"]),
                        GeoPlace.parent_id == parent.id,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            place = existing or await upsert_place(
                db,
                kind=node["kind"],
                name=node["name"],
                parent_id=parent.id,
                source="ai_approved",
            )
            parents[node["key"]] = place
            (skipped_ids if existing else created_ids).append(str(place.id))
            pending.remove(node)
            resolved = True
        if not resolved:
            raise HTTPException(status_code=422, detail="Geo proposal has unresolved parent keys")
    run.output = {
        **run.output,
        "applied": True,
        "applied_node_ids": created_ids,
        "skipped_node_ids": skipped_ids,
    }
    await append_audit(
        db,
        action="ai.geo.proposal.applied",
        payload={
            "run_id": str(run.id),
            "city_id": str(city.id),
            "created": len(created_ids),
            "skipped": len(skipped_ids),
        },
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"run_id": str(run.id), "created_node_ids": created_ids, "skipped_node_ids": skipped_ids}


@router.post("", response_model=GeoOut, status_code=201)
async def create_geo(
    body: GeoCreate,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> GeoPlace:
    try:
        place = await upsert_place(
            db,
            kind=body.kind,
            name=body.name,
            parent_id=body.parent_id,
            external_id=body.external_id,
            source="manual",
            lat=body.lat,
            lon=body.lon,
            timezone=body.timezone,
            population=body.population,
            attrs=body.attrs,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await append_audit(
        db,
        action="geo.create",
        payload={"kind": place.kind, "name": place.name},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(place)
    return place


@router.patch("/{place_id}", response_model=GeoOut)
async def update_geo(
    place_id: UUID,
    body: GeoUpdate,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> GeoPlace:
    place = await db.get(GeoPlace, place_id)
    if place is None:
        raise HTTPException(status_code=404, detail="Geography place not found")
    try:
        place = await update_place(
            db,
            place=place,
            name=body.name.strip() if body.name is not None else None,
            parent_id=body.parent_id,
            parent_updated="parent_id" in body.model_fields_set,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await append_audit(
        db,
        action="geo.update",
        payload={
            "place_id": str(place.id),
            "kind": place.kind,
            "parent_updated": "parent_id" in body.model_fields_set,
        },
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(place)
    return place


@router.get("/template.csv", response_class=PlainTextResponse)
async def geo_template(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
) -> PlainTextResponse:
    return PlainTextResponse(
        TEMPLATE,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=site-panel-geo-template.csv"},
    )


@router.post("/import-csv", status_code=201)
async def import_geo_csv(
    file: UploadFile = File(...),
    delimiter: str = Form(","),
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    try:
        rows, errors = parse_geo_csv(raw, delimiter=delimiter)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    known = {
        row.external_id: row.id
        for row in (await db.execute(select(GeoPlace).where(GeoPlace.external_id.is_not(None))))
        .scalars()
        .all()
    }
    created_or_updated = 0
    for row in rows:
        parent_external_id = row["parent_external_id"]
        parent_id = known.get(parent_external_id) if parent_external_id else None
        if parent_external_id and parent_id is None:
            if len(errors) < 100:
                errors.append(
                    {
                        "line": row["line"],
                        "error": f"Unknown parent_external_id: {parent_external_id}",
                    }
                )
            continue
        try:
            place = await upsert_place(
                db,
                kind=row["kind"],
                name=row["name"],
                external_id=row["external_id"],
                parent_id=parent_id,
                source="csv",
                lat=row["lat"],
                lon=row["lon"],
                timezone=row["timezone"],
                population=row["population"],
            )
        except ValueError as exc:
            if len(errors) < 100:
                errors.append({"line": row["line"], "error": str(exc)})
            continue
        known[row["external_id"]] = place.id
        created_or_updated += 1

    await append_audit(
        db,
        action="geo.import_csv",
        payload={"created_or_updated": created_or_updated, "invalid_rows": len(errors)},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"created_or_updated": created_or_updated, "invalid_rows": len(errors), "errors": errors}


@router.get("", response_model=list[GeoOut])
async def list_geo(
    kind: str | None = None,
    q: str | None = Query(None, min_length=1),
    parent_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
    ),
    db: AsyncSession = Depends(get_db),
) -> list[GeoPlace]:
    statement = select(GeoPlace).order_by(GeoPlace.name).limit(limit)
    if kind:
        statement = statement.where(GeoPlace.kind == kind)
    if q:
        statement = statement.where(GeoPlace.name.ilike(f"%{q}%"))
    if parent_id:
        from uuid import UUID

        statement = statement.where(GeoPlace.parent_id == UUID(parent_id))
    return list((await db.execute(statement)).scalars().all())


@router.post("/validate")
async def validate_geo(
    body: ToponymValidateRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    place = await validate_toponym(db, body.name, body.kind)
    if place:
        return {
            "valid": True,
            "id": str(place.id),
            "name": place.name,
            "forms": place.name_forms,
            "placeholders": place_context(place),
            "source": "local",
        }
    return {"valid": False, "name": body.name, "message": "Toponym not in local geo reference"}


@router.post("/morph", response_model=MorphOut)
async def morph_word(
    body: MorphRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
) -> MorphOut:
    forms = inflect_cases(body.word)
    return MorphOut(word=body.word, forms=forms, placeholders=city_placeholders(body.word, forms))
