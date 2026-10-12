from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1.leads import _serialize_delivery
from app.core.config import Settings
from app.services import webhook_delivery
from app.services.leads import get_encryptor
from app.services.webhook_delivery import (
    create_global_smtp_delivery,
    create_lead_deliveries,
    create_smtp_delivery,
)
from pydantic import ValidationError


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


def test_email_delivery_snapshots_an_encrypted_private_recipient(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        webhook_delivery, "get_settings", lambda: SimpleNamespace(smtp_configured=True)
    )
    lead = _lead()
    recipient = "private-leads@example.test"
    db = FakeDatabase()

    delivery = create_smtp_delivery(
        db,
        lead=lead,
        site=_site(),
        recipient_enc=get_encryptor().encrypt(recipient),
    )

    assert delivery is db.added[0]
    assert delivery.channel == "email"
    assert delivery.target_key == "private_email"
    assert delivery.target_url is None
    assert delivery.target_recipient_enc != recipient
    assert delivery.payload["lead_id"] == str(lead.id)
    assert recipient not in str(delivery.payload)
    assert delivery.status == "queued"


def test_central_smtp_delivery_readiness_uses_the_configured_server_route(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        webhook_delivery,
        "get_settings",
        lambda: SimpleNamespace(smtp_lead_delivery_ready=True),
    )

    assert webhook_delivery.central_smtp_delivery_configured() is True


def test_central_smtp_delivery_snapshots_server_recipient(monkeypatch: pytest.MonkeyPatch):
    recipient = "central-leads@example.test"
    monkeypatch.setattr(
        webhook_delivery,
        "get_settings",
        lambda: SimpleNamespace(
            smtp_lead_delivery_ready=True,
            smtp_lead_recipient=recipient,
        ),
    )
    lead = _lead()
    db = FakeDatabase()

    delivery = create_global_smtp_delivery(db, lead=lead, site=_site())

    assert delivery is db.added[0]
    assert delivery.channel == "email"
    assert delivery.target_key == "global_smtp_default"
    assert delivery.routing_source == "global_default"
    assert delivery.required is True
    assert delivery.target_recipient_enc != recipient
    assert recipient not in str(delivery.payload)
    assert delivery.status == "queued"


def test_central_smtp_delivery_preserves_lead_when_server_route_is_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        webhook_delivery,
        "get_settings",
        lambda: SimpleNamespace(
            smtp_lead_delivery_ready=False,
            smtp_lead_recipient="",
        ),
    )
    db = FakeDatabase()

    delivery = create_global_smtp_delivery(db, lead=_lead(), site=_site())

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
    monkeypatch.setattr(
        webhook_delivery,
        "get_settings",
        lambda: SimpleNamespace(
            smtp_configured=True,
            smtp_lead_delivery_ready=True,
            smtp_lead_recipient=recipient,
        ),
    )

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

    async def fake_dispatch(actual_recipient: str, payload: dict) -> dict:
        calls.append((actual_recipient, payload))
        return {"ok": True}

    monkeypatch.setattr(webhook_delivery, "dispatch_smtp", fake_dispatch)

    result = asyncio.run(webhook_delivery._dispatch_delivery(delivery, lead))

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


def test_smtp_setting_contract_rejects_half_configured_or_double_tls():
    with pytest.raises(ValidationError, match="SMTP_HOST and SMTP_FROM_EMAIL"):
        Settings(smtp_host="smtp.example.test")
    with pytest.raises(ValidationError, match="SMTP_USERNAME and SMTP_PASSWORD"):
        Settings(
            smtp_host="smtp.example.test", smtp_from_email="sender@example.test", smtp_username="u"
        )
    with pytest.raises(ValidationError, match="cannot both be enabled"):
        Settings(
            smtp_host="smtp.example.test", smtp_from_email="sender@example.test", smtp_use_ssl=True
        )


def test_smtp_lead_recipient_requires_safe_complete_smtp_configuration():
    assert Settings().smtp_lead_delivery_ready is False
    assert (
        Settings(smtp_host="smtp.example.test", smtp_from_email="sender@example.test")
        .smtp_lead_delivery_ready
        is False
    )

    settings = Settings(
        smtp_host="smtp.example.test",
        smtp_from_email="sender@example.test",
        smtp_lead_recipient="private-leads@example.test",
    )
    assert settings.smtp_lead_delivery_ready is True

    with pytest.raises(ValidationError, match="SMTP_HOST and SMTP_FROM_EMAIL"):
        Settings(smtp_lead_recipient="private-leads@example.test")
    with pytest.raises(ValidationError, match="SMTP_LEAD_RECIPIENT must be a valid email address"):
        Settings(
            smtp_host="smtp.example.test",
            smtp_from_email="sender@example.test",
            smtp_lead_recipient="private-leads@example.test\\nBcc: attacker@example.test",
        )


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
