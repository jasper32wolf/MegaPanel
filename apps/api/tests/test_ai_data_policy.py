from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1 import ai_content
from app.schemas.ai import AIDraftGenerationRequest
from app.services.ai_data_policy import public_fact_rows, safe_provider_context


def test_external_ai_context_excludes_private_business_fields() -> None:
    facts = {
        "service": "Ремонт техники",
        "services": ["Диагностика", "Выезд мастера"],
        "contacts": {"phone": "+79991234567", "email": "lead@example.test"},
        "legal": {"operator": "Private operator"},
        "api_key": "private-value",
        "allowed_claims": ["Гарантия без подтверждения"],
    }

    rows = public_fact_rows(facts)
    serialized = json.dumps(rows, ensure_ascii=False)

    assert {row["fact_key"] for row in rows} == {"service", "services"}
    assert "Ремонт техники" in serialized
    assert all(
        value not in serialized for value in ("+79991234567", "lead@example.test", "private-value")
    )


def test_external_ai_context_redacts_contact_data_inside_public_text() -> None:
    context = {
        "confirmed_facts": public_fact_rows(
            {"service": "Ремонт техники: +79991234567, lead@example.test"}
        ),
        "selected_keywords": [{"phrase": "Связаться: lead@example.test"}],
    }

    safe = safe_provider_context(context)
    serialized = json.dumps(safe, ensure_ascii=False)

    assert "+79991234567" not in serialized
    assert "lead@example.test" not in serialized
    assert "[phone]" in serialized
    assert "[email]" in serialized


@pytest.mark.parametrize("value", ["Bearer abc123", "api_key=private-value", "sk-1234567890123456"])
def test_external_ai_context_rejects_credential_like_strings(value: str) -> None:
    with pytest.raises(ValueError, match="credential-like"):
        safe_provider_context({"operator_constraints": [value]})


def test_external_ai_context_rejects_sensitive_nested_keys() -> None:
    with pytest.raises(ValueError, match="sensitive field names"):
        safe_provider_context({"validated_geo": [{"forms": {"api_key": "private"}}]})


def test_draft_provider_request_omits_full_internal_fact_snapshot(monkeypatch) -> None:
    project_id, plan_id, fact_id, tenant_id, connection_id = (uuid4() for _ in range(5))
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        domain="example.test",
        name="Сервис",
        locale="ru",
        niche="ремонт",
    )
    plan = SimpleNamespace(
        id=plan_id,
        project_id=project_id,
        state="approved",
        fact_revision_id=fact_id,
        slug="/repair",
        objective="Ремонт техники",
        intent="service",
        kit_key="service-local-v1",
        block_selection={"blocks": ["hero"]},
        keyword_snapshot={"items": []},
        geo_snapshot={"items": [{"geo_id": str(uuid4()), "name": "Казань", "role": "primary"}]},
        version=1,
    )
    facts = SimpleNamespace(
        id=fact_id,
        facts_hash="a" * 64,
        state="confirmed",
        facts={
            "service": "Ремонт техники",
            "contacts": {"phone": "+79991234567"},
            "legal": {"operator": "Personal Name"},
            "api_key": "private-key-material",
        },
    )
    connection = SimpleNamespace(
        id=connection_id,
        enabled=True,
        provider_id="gateway",
        model_ids=["test-model"],
        metadata_json={
            "model_pricing": {
                "test-model": {
                    "observed_at": datetime.now(UTC).isoformat(),
                    "input_price_usd_per_million": 1.0,
                    "output_price_usd_per_million": 1.0,
                    "source": "operator",
                }
            }
        },
    )

    async def project_lookup(*_args):
        return project

    async def plan_lookup(*_args):
        return plan

    class Session:
        async def get(self, _model, _id):
            return connection

        async def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: facts)

    monkeypatch.setattr(ai_content, "_project_or_404", project_lookup)
    monkeypatch.setattr(ai_content, "_plan_or_404", plan_lookup)
    body = AIDraftGenerationRequest(
        provider_connection_id=connection_id, model="test-model", max_cost_usd=1.0
    )
    context = asyncio.run(
        ai_content._prepare_draft_context(
            project_id, plan_id, body, SimpleNamespace(tenant_id=tenant_id), Session()
        )
    )
    provider_payload = context["user_prompt"]

    assert "Ремонт техники" in provider_payload
    assert all(
        value not in provider_payload
        for value in (
            "contacts",
            "+79991234567",
            "legal",
            "Personal Name",
            "api_key",
            "private-key-material",
            "deterministic_input",
        )
    )
    assert context["deterministic_snapshot"]["facts"] == facts.facts
