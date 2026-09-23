from __future__ import annotations

from uuid import UUID

from app.api.deps import AuthContext
from app.api.v1.system import require_system_operator
from app.db.session import get_db
from app.models import AIProviderConnection
from app.providers import (
    OpenAICompatibleAdapter,
    ProviderCapabilities,
    ProviderModel,
    registry,
)
from app.schemas.ai import (
    ProviderConnectionCreate,
    ProviderConnectionOut,
    ProviderConnectionUpdate,
    ProviderModelOut,
    ProviderTestOut,
)
from app.services.ai_secrets import decrypt_provider_key, encrypt_provider_key
from app.services.audit import append_audit
from app.services.hardening import egress
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


def _out(connection: AIProviderConnection) -> ProviderConnectionOut:
    return ProviderConnectionOut(
        id=connection.id,
        provider_id=connection.provider_id,
        label=connection.label,
        kind=connection.kind,
        base_url=connection.base_url,
        credential_last4=connection.credential_last4,
        enabled=connection.enabled,
        created_at=connection.created_at.isoformat() if connection.created_at else None,
        updated_at=connection.updated_at.isoformat() if connection.updated_at else None,
    )


def _adapter(connection: AIProviderConnection, api_key: str):
    if connection.kind == "native":
        try:
            return registry.create(connection.provider_id, api_key=api_key)
        except KeyError as exc:
            raise HTTPException(
                status_code=400, detail="Native provider adapter is not available"
            ) from exc
    if not connection.base_url:
        raise HTTPException(status_code=400, detail="Provider endpoint is missing")
    models = tuple(
        ProviderModel(
            provider_id=connection.provider_id,
            model_id=model_id,
            display_name=model_id,
            capabilities=ProviderCapabilities(structured_output=True),
        )
        for model_id in connection.model_ids
    )
    return OpenAICompatibleAdapter(
        provider_id=connection.provider_id,
        base_url=connection.base_url,
        api_key=api_key,
        default_models=models,
    )


@router.patch("/{connection_id}", response_model=ProviderConnectionOut)
async def update_provider_connection(
    connection_id: UUID,
    body: ProviderConnectionUpdate,
    auth: AuthContext = Depends(require_system_operator),
    db: AsyncSession = Depends(get_db),
) -> ProviderConnectionOut:
    connection = await db.get(AIProviderConnection, connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Provider connection not found")
    changes = body.model_dump(exclude_unset=True)
    model_ids = set(changes.get("models", connection.model_ids))
    if "model_pricing" in changes and not set(changes["model_pricing"]).issubset(model_ids):
        raise HTTPException(
            status_code=422, detail="Pricing metadata must match registered model IDs"
        )
    if changes.get("api_key") is not None:
        encrypted = encrypt_provider_key(changes.pop("api_key"))
        connection.encrypted_api_key = encrypted.ciphertext
        connection.credential_last4 = encrypted.last4
    else:
        changes.pop("api_key", None)
    if "label" in changes:
        connection.label = changes["label"]
    if "model_pricing" in changes:
        connection.metadata_json = {
            **(connection.metadata_json or {}),
            "model_pricing": {
                model_id: pricing.model_dump(mode="json")
                for model_id, pricing in changes["model_pricing"].items()
            },
        }
    if "models" in changes:
        connection.model_ids = changes["models"]
    if body.model_fields_set:
        await append_audit(
            db,
            action="ai.provider.update",
            payload={
                "provider_id": connection.provider_id,
                "connection_id": str(connection.id),
                "credential_replaced": "api_key" in body.model_fields_set,
                "models_updated": "models" in body.model_fields_set,
                "label_updated": "label" in body.model_fields_set,
            },
            tenant_id=auth.tenant_id,
            actor_id=auth.user.id,
        )
        await db.commit()
        await db.refresh(connection)
    return _out(connection)


@router.delete("/{connection_id}", status_code=204)
async def delete_provider_connection(
    connection_id: UUID,
    auth: AuthContext = Depends(require_system_operator),
    db: AsyncSession = Depends(get_db),
) -> None:
    connection = await db.get(AIProviderConnection, connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Provider connection not found")
    await append_audit(
        db,
        action="ai.provider.delete",
        payload={"provider_id": connection.provider_id, "connection_id": str(connection.id)},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.delete(connection)
    await db.commit()


@router.get("", response_model=list[ProviderConnectionOut])
async def list_provider_connections(
    _auth: AuthContext = Depends(require_system_operator),
    db: AsyncSession = Depends(get_db),
) -> list[ProviderConnectionOut]:
    rows = list(
        (
            await db.execute(
                select(AIProviderConnection).order_by(AIProviderConnection.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_out(row) for row in rows]


@router.post("", response_model=ProviderConnectionOut, status_code=201)
async def create_provider_connection(
    body: ProviderConnectionCreate,
    auth: AuthContext = Depends(require_system_operator),
    db: AsyncSession = Depends(get_db),
) -> ProviderConnectionOut:
    if not set(body.model_pricing).issubset(body.models):
        raise HTTPException(
            status_code=422, detail="Pricing metadata must match registered model IDs"
        )
    if body.kind == "openai_compatible":
        if body.base_url is None:
            raise HTTPException(
                status_code=422, detail="base_url is required for compatible providers"
            )
        try:
            egress.assert_allowed(str(body.base_url))
        except PermissionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif body.provider_id not in registry.ids():
        raise HTTPException(status_code=422, detail="Native provider adapter is not available")

    encrypted = encrypt_provider_key(body.api_key)
    connection = AIProviderConnection(
        provider_id=body.provider_id,
        label=body.label,
        kind=body.kind,
        base_url=str(body.base_url) if body.base_url else None,
        encrypted_api_key=encrypted.ciphertext,
        credential_last4=encrypted.last4,
        model_ids=body.models,
        enabled=False,
        metadata_json={
            "model_pricing": {
                model_id: pricing.model_dump(mode="json")
                for model_id, pricing in body.model_pricing.items()
            }
        },
    )
    db.add(connection)
    await db.flush()
    await append_audit(
        db,
        action="ai.provider.create",
        payload={
            "provider_id": body.provider_id,
            "connection_id": str(connection.id),
            "enabled": False,
        },
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(connection)
    return _out(connection)


@router.post("/{connection_id}/test", response_model=ProviderTestOut)
async def test_provider_connection(
    connection_id: UUID,
    auth: AuthContext = Depends(require_system_operator),
    db: AsyncSession = Depends(get_db),
) -> ProviderTestOut:
    connection = await db.get(AIProviderConnection, connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Provider connection not found")
    try:
        api_key = decrypt_provider_key(connection.encrypted_api_key)
        adapter = _adapter(connection, api_key)
        if connection.base_url:
            egress.assert_allowed(connection.base_url)
        await append_audit(
            db,
            action="ai.provider.test",
            payload={
                "provider_id": connection.provider_id,
                "connection_id": str(connection.id),
                "network_call": False,
            },
            tenant_id=auth.tenant_id,
            actor_id=auth.user.id,
        )
        await db.commit()
        return ProviderTestOut(
            ok=True, provider_id=adapter.provider_id, message="Configuration is valid"
        )
    except (PermissionError, ValueError, HTTPException) as exc:
        return ProviderTestOut(
            ok=False,
            provider_id=connection.provider_id,
            code="invalid_configuration",
            message=str(exc),
        )


@router.post("/{connection_id}/activate", response_model=ProviderConnectionOut)
async def activate_provider_connection(
    connection_id: UUID,
    auth: AuthContext = Depends(require_system_operator),
    db: AsyncSession = Depends(get_db),
) -> ProviderConnectionOut:
    connection = await db.get(AIProviderConnection, connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Provider connection not found")
    connection.enabled = True
    await append_audit(
        db,
        action="ai.provider.activate",
        payload={"provider_id": connection.provider_id, "connection_id": str(connection.id)},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(connection)
    return _out(connection)


@router.post("/{connection_id}/disable", response_model=ProviderConnectionOut)
async def disable_provider_connection(
    connection_id: UUID,
    auth: AuthContext = Depends(require_system_operator),
    db: AsyncSession = Depends(get_db),
) -> ProviderConnectionOut:
    connection = await db.get(AIProviderConnection, connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Provider connection not found")
    connection.enabled = False
    await append_audit(
        db,
        action="ai.provider.disable",
        payload={"provider_id": connection.provider_id, "connection_id": str(connection.id)},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(connection)
    return _out(connection)


@router.get("/{connection_id}/models", response_model=list[ProviderModelOut])
async def list_provider_models(
    connection_id: UUID,
    _auth: AuthContext = Depends(require_system_operator),
    db: AsyncSession = Depends(get_db),
) -> list[ProviderModelOut]:
    connection = await db.get(AIProviderConnection, connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Provider connection not found")
    adapter = _adapter(connection, decrypt_provider_key(connection.encrypted_api_key))
    listed = {model.model_id: model for model in await adapter.list_models()}
    for model_id in connection.model_ids:
        listed.setdefault(
            model_id,
            ProviderModel(
                provider_id=connection.provider_id,
                model_id=model_id,
                display_name=model_id,
                capabilities=adapter.model_capabilities(model_id),
            ),
        )
    pricing_by_model = (connection.metadata_json or {}).get("model_pricing", {})
    return [
        ProviderModelOut(
            provider_id=model.provider_id,
            model_id=model.model_id,
            display_name=model.display_name,
            capabilities=model.capabilities.__dict__,
            input_price_usd_per_million=pricing_by_model.get(model.model_id, {}).get(
                "input_price_usd_per_million", model.input_price_usd_per_million
            ),
            output_price_usd_per_million=pricing_by_model.get(model.model_id, {}).get(
                "output_price_usd_per_million", model.output_price_usd_per_million
            ),
            is_free=pricing_by_model.get(model.model_id, {}).get("is_free", model.is_free),
            metadata_source=pricing_by_model.get(model.model_id, {}).get(
                "source", model.metadata_source
            ),
            metadata_observed_at=pricing_by_model.get(model.model_id, {}).get(
                "observed_at", model.metadata_observed_at
            ),
        )
        for model in listed.values()
    ]
