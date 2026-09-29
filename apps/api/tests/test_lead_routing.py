from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1.lead_routing import _serialize_policy
from app.main import app
from app.schemas.lead_routing import LeadRoutingPolicyCreate
from app.services.leads import get_encryptor
from app.services.webhook_delivery import (
    create_policy_delivery,
    policy_hash,
    recompute_lead_delivery_aggregate,
)
from pydantic import ValidationError


class Result:
    def __init__(self, values: list[object]):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


class AggregateDatabase:
    def __init__(self, deliveries: list[object]):
        self.deliveries = deliveries
        self.aggregate = None
        self.added: list[object] = []

    async def execute(self, _statement):
        return Result(self.deliveries)

    async def get(self, _model, _key):
        return self.aggregate

    def add(self, value: object):
        self.added.append(value)
        self.aggregate = value


class DeliveryDatabase:
    def __init__(self):
        self.added: list[object] = []

    def add(self, value: object):
        self.added.append(value)


def _lead():
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        page_slug="/",
        qualification="qualified",
        idempotency_key="lead-1",
    )


def _site():
    return SimpleNamespace(id=uuid4(), domain="example.test")


def test_routing_policy_schema_requires_exactly_one_safe_channel_target():
    policy = LeadRoutingPolicyCreate(
        destinations=[
            {"target_key": "private_email", "channel": "email", "recipient": "lead@example.com"},
            {
                "target_key": "crm_webhook",
                "channel": "webhook",
                "webhook_url": "https://hooks.example.test/lead",
                "webhook_secret": "a" * 32,
                "required": False,
            },
        ]
    )

    assert [destination.target_key for destination in policy.destinations] == [
        "private_email",
        "crm_webhook",
    ]
    with pytest.raises(ValidationError, match="Email destinations"):
        LeadRoutingPolicyCreate(
            destinations=[
                {
                    "target_key": "email",
                    "channel": "email",
                    "recipient": "lead@example.com",
                    "webhook_url": "https://hooks.example.test/lead",
                }
            ]
        )
    with pytest.raises(ValidationError, match="unique"):
        LeadRoutingPolicyCreate(
            destinations=[
                {"target_key": "email", "channel": "email", "recipient": "one@example.com"},
                {"target_key": "email", "channel": "email", "recipient": "two@example.com"},
            ]
        )


def test_policy_delivery_snapshots_encrypted_destination_without_pii_payload(monkeypatch):
    monkeypatch.setattr(
        "app.services.webhook_delivery.get_settings", lambda: SimpleNamespace(smtp_configured=True)
    )
    recipient = "private-leads@example.com"
    policy = SimpleNamespace(id=uuid4(), version=3, policy_hash="a" * 64)
    destination = SimpleNamespace(
        target_key="private_email",
        channel="email",
        required=True,
        target_url=None,
        target_secret_enc=None,
        target_recipient_enc=get_encryptor().encrypt(recipient),
    )
    db = DeliveryDatabase()
    lead = _lead()

    delivery = create_policy_delivery(
        db, lead=lead, site=_site(), policy=policy, destination=destination
    )

    assert delivery is db.added[0]
    assert delivery.routing_policy_id == policy.id
    assert delivery.routing_policy_version == 3
    assert delivery.required is True
    assert recipient not in str(delivery.payload)
    assert recipient not in str(delivery.target_recipient_enc)


def test_delivery_aggregate_ignores_optional_dead_letter_for_required_success():
    lead = _lead()
    now = datetime.now(UTC)
    db = AggregateDatabase(
        [
            SimpleNamespace(status="delivered", required=True, created_at=now),
            SimpleNamespace(status="dead_letter", required=False, created_at=now),
        ]
    )

    aggregate = asyncio.run(recompute_lead_delivery_aggregate(db, lead=lead))

    assert aggregate.status == "delivered"
    assert aggregate.expected_count == 1
    assert aggregate.attention_count == 0
    assert lead.crm_status == "delivered"


def test_delivery_aggregate_marks_required_dead_letter_as_attention():
    lead = _lead()
    db = AggregateDatabase(
        [SimpleNamespace(status="dead_letter", required=True, created_at=datetime.now(UTC))]
    )

    aggregate = asyncio.run(recompute_lead_delivery_aggregate(db, lead=lead))

    assert aggregate.status == "attention"
    assert aggregate.attention_count == 1
    assert lead.crm_status == "attention"


def test_policy_serialization_redacts_destinations_and_hash_is_stable():
    recipient = "private-leads@example.com"
    policy = SimpleNamespace(
        id=uuid4(),
        version=1,
        state="draft",
        submitted_at=None,
        reviewed_at=None,
        decision_reason=None,
        created_at=None,
    )
    destination = SimpleNamespace(
        id=uuid4(),
        target_key="private_email",
        channel="email",
        required=True,
        target_recipient_enc=get_encryptor().encrypt(recipient),
        target_url=None,
        target_secret_enc=None,
    )

    payload = _serialize_policy(policy, [destination])

    assert payload["destinations"] == [
        {
            "id": str(destination.id),
            "target_key": "private_email",
            "channel": "email",
            "required": True,
            "configured": True,
        }
    ]
    assert recipient not in str(payload)
    assert policy_hash([{"target_key": "email", "channel": "email"}]) == policy_hash(
        [{"channel": "email", "target_key": "email"}]
    )


def test_routing_migration_and_routes_are_registered():
    source = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0032_lead_routing_policy.py"
    ).read_text(encoding="utf-8")
    paths = app.openapi()["paths"]

    assert 'down_revision: str | None = "0031_site_build_page_metadata_snapshot"' in source
    assert "lead_routing_policies" in source
    assert "lead_delivery_aggregates" in source
    assert "uq_lead_routing_policy_active_site" in source
    assert "get" in paths["/api/v1/projects/{project_id}/lead-routing"]
    assert "post" in paths["/api/v1/projects/{project_id}/lead-routing/{policy_id}/activate"]
    assert "post" in paths["/api/v1/projects/{project_id}/lead-routing/{policy_id}/reject"]
