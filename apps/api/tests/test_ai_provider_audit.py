from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.api.v1 import ai_providers
from app.schemas.ai import ProviderConnectionUpdate, ProviderPricingIn


class Database:
    def __init__(self, connection: object) -> None:
        self.connection = connection
        self.commits = 0

    async def get(self, _model: object, _connection_id: object) -> object:
        return self.connection

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _connection: object) -> None:
        return None


def connection(pricing: dict) -> object:
    return SimpleNamespace(
        id=uuid4(),
        provider_id="gateway",
        label="Gateway",
        kind="openai_compatible",
        base_url="https://provider.test/v1",
        credential_last4="1234",
        encrypted_api_key="encrypted",
        model_ids=["model-a"],
        metadata_json={"model_pricing": pricing},
        enabled=True,
        created_at=None,
        updated_at=None,
    )


def auth_context() -> object:
    return SimpleNamespace(tenant_id=uuid4(), user=SimpleNamespace(id=uuid4()))


def pricing(input_price: float) -> ProviderPricingIn:
    return ProviderPricingIn(
        input_price_usd_per_million=input_price,
        output_price_usd_per_million=2.0,
        source="operator invoice",
        observed_at=datetime(2026, 9, 25, tzinfo=UTC),
    )


def test_provider_pricing_update_is_audited_by_old_and_new_hash(monkeypatch):
    db = Database(connection({"model-a": pricing(1.0).model_dump(mode="json")}))
    captured = {}

    async def append_audit(*_args, **kwargs) -> None:
        captured.update(kwargs["payload"])

    monkeypatch.setattr(ai_providers, "append_audit", append_audit)

    asyncio.run(
        ai_providers.update_provider_connection(
            db.connection.id,
            ProviderConnectionUpdate(model_pricing={"model-a": pricing(3.0)}),
            auth_context(),
            db,
        )
    )

    assert captured["pricing_updated"] is True
    assert captured["pricing_model_ids"] == ["model-a"]
    assert captured["previous_pricing_hash"] != captured["updated_pricing_hash"]
    assert db.commits == 1


def test_provider_label_update_records_unchanged_pricing_hash(monkeypatch):
    db = Database(connection({"model-a": pricing(1.0).model_dump(mode="json")}))
    captured = {}

    async def append_audit(*_args, **kwargs) -> None:
        captured.update(kwargs["payload"])

    monkeypatch.setattr(ai_providers, "append_audit", append_audit)

    asyncio.run(
        ai_providers.update_provider_connection(
            db.connection.id,
            ProviderConnectionUpdate(label="Renamed gateway"),
            auth_context(),
            db,
        )
    )

    assert captured["pricing_updated"] is False
    assert captured["pricing_model_ids"] == []
    assert captured["previous_pricing_hash"] == captured["updated_pricing_hash"]
