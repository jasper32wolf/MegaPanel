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
from app.services.webhook_delivery import create_smtp_delivery
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
