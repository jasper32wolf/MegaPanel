from __future__ import annotations

import asyncio
import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1 import lead_routing
from app.api.v1.lead_routing import _central_delivery_status, _serialize_policy
from app.main import app
from app.models.leads import LeadRoutingPolicy, WebhookDelivery
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


def test_routing_policy_schema_reserves_the_central_delivery_target_and_confirms_replacement():
    with pytest.raises(ValidationError, match="reserved"):
        LeadRoutingPolicyCreate(
            destinations=[
                {
                    "target_key": "global_smtp_default",
                    "channel": "email",
                    "recipient": "lead@example.com",
                }
            ]
        )
    with pytest.raises(ValidationError, match="Explicit confirmation"):
        LeadRoutingPolicyCreate(
            delivery_mode="replace_global",
            destinations=[
                {
                    "target_key": "project_email",
                    "channel": "email",
                    "recipient": "lead@example.com",
                }
            ],
        )


def test_central_delivery_status_exposes_only_safe_effective_route(monkeypatch):
    site = _site()

    async def no_policy(_db, *, site):
        return None

    monkeypatch.setattr(lead_routing, "active_routing_policy", no_policy)
    monkeypatch.setattr(lead_routing, "central_smtp_delivery_configured", lambda: True)

    assert asyncio.run(_central_delivery_status(object(), site)) == {
        "configured": True,
        "effective_mode": "central_default",
        "uses_central_delivery": True,
    }

    async def replacement_policy(_db, *, site):
        return (SimpleNamespace(delivery_mode="replace_global"), [])

    monkeypatch.setattr(lead_routing, "active_routing_policy", replacement_policy)

    assert asyncio.run(_central_delivery_status(object(), site)) == {
        "configured": True,
        "effective_mode": "replace_global",
        "uses_central_delivery": False,
    }


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
        delivery_mode="append_global",
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
    destinations = [{"target_key": "email", "channel": "email"}]
    assert policy_hash(destinations, delivery_mode="append_global") == policy_hash(
        [{"channel": "email", "target_key": "email"}], delivery_mode="append_global"
    )
    assert policy_hash(destinations, delivery_mode="append_global") != policy_hash(
        destinations, delivery_mode="replace_global"
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


def test_central_lead_delivery_models_constrain_routing_metadata():
    policy_delivery_mode = LeadRoutingPolicy.__table__.c.delivery_mode
    delivery_routing_source = WebhookDelivery.__table__.c.routing_source
    policy_constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in LeadRoutingPolicy.__table__.constraints
        if constraint.name and constraint.name.startswith("ck_")
    }
    delivery_constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in WebhookDelivery.__table__.constraints
        if constraint.name and constraint.name.startswith("ck_")
    }

    assert policy_delivery_mode.default and policy_delivery_mode.default.arg == "append_global"
    assert delivery_routing_source.default and delivery_routing_source.default.arg == "legacy"
    assert policy_constraints["ck_lead_routing_policies_delivery_mode"] == (
        "delivery_mode IN ('append_global', 'replace_global')"
    )
    assert delivery_constraints["ck_webhook_deliveries_routing_source"] == (
        "routing_source IN ('global_default', 'project_policy', 'legacy')"
    )


def test_central_lead_delivery_migration_backfills_and_constrains_routes(monkeypatch):
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0068_central_lead_delivery_defaults.py"
    )
    spec = importlib.util.spec_from_file_location("central_lead_delivery_migration", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    calls: list[tuple[str, tuple[object, ...]]] = []
    monkeypatch.setattr(
        migration.op, "add_column", lambda *args: calls.append(("add_column", args))
    )
    monkeypatch.setattr(migration.op, "execute", lambda *args: calls.append(("execute", args)))
    monkeypatch.setattr(
        migration.op, "alter_column", lambda *args, **kwargs: calls.append(("alter_column", args))
    )
    monkeypatch.setattr(
        migration.op,
        "create_check_constraint",
        lambda *args: calls.append(("create_check_constraint", args)),
    )

    migration.upgrade()

    assert migration.revision == "0068_central_lead_delivery_defaults"
    assert migration.down_revision == "0067_project_branding_assets"
    assert [(args[0], args[1].name) for name, args in calls if name == "add_column"] == [
        ("lead_routing_policies", "delivery_mode"),
        ("webhook_deliveries", "routing_source"),
    ]
    backfills = [args[0] for name, args in calls if name == "execute"]
    assert "delivery_mode = 'replace_global'" in backfills[0]
    assert "WHEN routing_policy_id IS NULL THEN 'legacy'" in backfills[1]
    constraints = [args for name, args in calls if name == "create_check_constraint"]
    assert constraints == [
        (
            "ck_lead_routing_policies_delivery_mode",
            "lead_routing_policies",
            "delivery_mode IN ('append_global', 'replace_global')",
        ),
        (
            "ck_webhook_deliveries_routing_source",
            "webhook_deliveries",
            "routing_source IN ('global_default', 'project_policy', 'legacy')",
        ),
    ]
