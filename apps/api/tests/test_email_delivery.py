from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1.leads import _serialize_delivery
from app.services import webhook_delivery
from app.services.leads import get_encryptor
from app.services.webhook_delivery import create_global_smtp_delivery, create_lead_deliveries


class FakeDatabase:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, value: object) -> None:
        self.added.append(value)


def _email_delivery_migration():
    migration_path = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0024_email_delivery_targets.py"
    )
    spec = importlib.util.spec_from_file_location("email_delivery_migration", migration_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lead() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        page_slug="/repair",
        qualification="qualified",
        idempotency_key="lead-1",
        crm_status=None,
    )


def _site() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), domain="example.test")


def test_central_smtp_delivery_snapshots_panel_transport_revision(
    monkeypatch: pytest.MonkeyPatch,
):
    recipient = "central-leads@example.test"

    async def bind(_db, delivery, *, rebind_recipient):
        assert rebind_recipient is True
        delivery.target_recipient_enc = get_encryptor().encrypt(recipient)
        delivery.lead_transport_revision = 7
        return True

    monkeypatch.setattr(webhook_delivery, "bind_delivery_transport", bind)
    lead = _lead()
    db = FakeDatabase()

    delivery = asyncio.run(create_global_smtp_delivery(db, lead=lead, site=_site()))

    assert delivery is db.added[0]
    assert delivery.channel == "email"
    assert delivery.target_key == "global_smtp_default"
    assert delivery.routing_source == "global_default"
    assert delivery.required is True
    assert delivery.target_recipient_enc != recipient
    assert delivery.lead_transport_revision == 7
    assert recipient not in str(delivery.payload)
    assert delivery.status == "queued"


def test_central_smtp_delivery_preserves_lead_when_panel_route_is_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    async def bind(_db, _delivery, *, rebind_recipient):
        assert rebind_recipient is True
        return False

    monkeypatch.setattr(webhook_delivery, "bind_delivery_transport", bind)
    delivery = asyncio.run(create_global_smtp_delivery(FakeDatabase(), lead=_lead(), site=_site()))

    assert delivery.status == "dead_letter"
    assert delivery.required is True
    assert delivery.target_recipient_enc is None
    assert delivery.last_error == "Central SMTP delivery is not configured"


@pytest.mark.parametrize(
    ("delivery_mode", "expected_sources"),
    [
        (None, ["global_default"]),
        ("append_global", ["global_default", "project_policy"]),
        ("replace_global", ["project_policy"]),
    ],
)
def test_new_leads_use_central_delivery_unless_policy_replaces_it(
    monkeypatch: pytest.MonkeyPatch, delivery_mode: str | None, expected_sources: list[str]
):
    recipient = "central-leads@example.test"

    async def bind(_db, delivery, *, rebind_recipient):
        if rebind_recipient:
            delivery.target_recipient_enc = get_encryptor().encrypt(recipient)
        delivery.lead_transport_revision = 1
        return True

    monkeypatch.setattr(webhook_delivery, "bind_delivery_transport", bind)

    async def active_policy(_db, *, site):
        if delivery_mode is None:
            return None
        return (
            SimpleNamespace(
                id=uuid4(),
                version=3,
                policy_hash="a" * 64,
                delivery_mode=delivery_mode,
            ),
            [
                SimpleNamespace(
                    target_key="custom_email",
                    channel="email",
                    required=True,
                    target_recipient_enc=get_encryptor().encrypt("custom@example.test"),
                    target_url=None,
                    target_secret_enc=None,
                )
            ],
        )

    monkeypatch.setattr(webhook_delivery, "active_routing_policy", active_policy)
    db = FakeDatabase()

    deliveries = asyncio.run(create_lead_deliveries(db, lead=_lead(), site=_site()))

    assert [delivery.routing_source for delivery in deliveries] == expected_sources
    assert all(recipient not in str(delivery.payload) for delivery in deliveries)
    assert all(delivery.target_key != "private_email" for delivery in deliveries)


def test_email_delivery_never_serializes_the_recipient():
    recipient = "private-leads@example.test"
    delivery = SimpleNamespace(
        id=uuid4(),
        channel="email",
        target_url=None,
        target_recipient_enc=get_encryptor().encrypt(recipient),
        status="queued",
        attempt_count=0,
        max_attempts=5,
        next_attempt_at=None,
        last_error=None,
        last_http_status=None,
    )

    payload = _serialize_delivery(delivery)

    assert payload["target"] == "private_email"
    assert recipient not in str(payload)
    assert "recipient" not in payload


def test_central_delivery_serialization_never_exposes_the_recipient():
    recipient = "central-leads@example.test"
    delivery = SimpleNamespace(
        id=uuid4(),
        channel="email",
        routing_source="global_default",
        target_url=None,
        target_recipient_enc=get_encryptor().encrypt(recipient),
        status="queued",
        attempt_count=0,
        max_attempts=5,
        next_attempt_at=None,
        last_error=None,
        last_http_status=None,
    )

    payload = _serialize_delivery(delivery)

    assert payload["target"] == "central_smtp"
    assert payload["routing_source"] == "global_default"
    assert recipient not in str(payload)


def test_email_delivery_dispatches_only_through_fake_transport(monkeypatch: pytest.MonkeyPatch):
    recipient = "private-leads@example.test"
    encryptor = get_encryptor()
    delivery = SimpleNamespace(
        channel="email",
        target_recipient_enc=encryptor.encrypt(recipient),
        payload={"lead_id": str(uuid4()), "domain": "example.test"},
    )
    lead = SimpleNamespace(
        phone_enc=encryptor.encrypt("+79990000000"),
        email_enc=None,
        name_enc=None,
        message_enc=encryptor.encrypt("Нужна консультация"),
    )
    calls: list[tuple[str, dict]] = []

    async def fake_dispatch(_db, *, delivery, recipient: str, payload: dict) -> dict:
        calls.append((recipient, payload))
        return {"ok": True}

    monkeypatch.setattr(webhook_delivery, "dispatch_lead_smtp", fake_dispatch)

    result = asyncio.run(webhook_delivery._dispatch_delivery(object(), delivery, lead))

    assert result == {"ok": True}
    assert calls == [
        (
            recipient,
            {
                "lead_id": delivery.payload["lead_id"],
                "domain": "example.test",
                "phone": "+79990000000",
                "email": None,
                "name": None,
                "message": "Нужна консультация",
            },
        )
    ]


def test_email_delivery_migration_adds_encrypted_target_fields(monkeypatch: pytest.MonkeyPatch):
    migration = _email_delivery_migration()
    calls: list[tuple[str, tuple[object, ...]]] = []

    monkeypatch.setattr(migration.op, "add_column", lambda *args: calls.append(("add", args)))
    monkeypatch.setattr(
        migration.op, "alter_column", lambda *args, **_: calls.append(("alter", args))
    )
    monkeypatch.setattr(migration.op, "create_index", lambda *args: calls.append(("index", args)))

    migration.upgrade()

    assert migration.down_revision == "0023_prompt_registry_rls"
    assert [call[1][1].name for call in calls if call[0] == "add"] == [
        "channel",
        "target_recipient_enc",
    ]
    assert any(call[0] == "alter" and call[1][1] == "target_url" for call in calls)
