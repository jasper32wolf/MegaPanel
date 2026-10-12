from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.main import app
from app.models.leads import CentralLeadSmtpTransport
from app.services import lead_transport
from app.services.leads import get_encryptor


def test_panel_lead_transport_routes_are_registered():
    paths = app.openapi()["paths"]
    assert {"get", "put", "delete"} <= set(paths["/api/v1/panel/lead-smtp-transport"])
    assert "post" in paths["/api/v1/panel/lead-smtp-transport/test"]


class TransportDatabase:
    def __init__(self, transport: CentralLeadSmtpTransport | None = None) -> None:
        self.transport = transport

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self.transport)

    def add(self, value: CentralLeadSmtpTransport) -> None:
        self.transport = value


def test_panel_lead_transport_encrypts_credentials_and_masks_status():
    tenant_id = uuid4()
    db = TransportDatabase()

    transport = asyncio.run(
        lead_transport.configure_lead_transport(
            db,
            tenant_id=tenant_id,
            host="smtp.example.test",
            port=587,
            tls_mode="starttls",
            sender_email="sender@example.test",
            recipient="leads@example.test",
            username="smtp-user",
            password="smtp-password",
        )
    )
    status = asyncio.run(lead_transport.lead_transport_status(db, tenant_id))

    assert transport.recipient_enc != "leads@example.test"
    assert transport.smtp_username_enc != "smtp-user"
    assert transport.smtp_password_enc != "smtp-password"
    assert get_encryptor().decrypt(transport.recipient_enc) == "leads@example.test"
    assert status == {
        "configured": True,
        "enabled": True,
        "sender": "s***@example.test",
        "recipient": "l***@example.test",
        "host": "smtp.example.test",
        "port": 587,
        "tls_mode": "starttls",
        "revision": 1,
        "updated_at": None,
    }
    assert "smtp-password" not in str(status)
    assert "leads@example.test" not in str(status)


def test_lead_transport_update_and_disable_invalidate_old_delivery_revision():
    tenant_id = uuid4()
    db = TransportDatabase()
    asyncio.run(
        lead_transport.configure_lead_transport(
            db,
            tenant_id=tenant_id,
            host="smtp.example.test",
            port=465,
            tls_mode="implicit_tls",
            sender_email="sender@example.test",
            recipient="leads@example.test",
            username=None,
            password=None,
        )
    )
    delivery = SimpleNamespace(
        tenant_id=tenant_id,
        target_recipient_enc=None,
        lead_transport_revision=None,
    )

    assert asyncio.run(
        lead_transport.bind_delivery_transport(db, delivery, rebind_recipient=True)
    )
    assert delivery.lead_transport_revision == 1
    assert get_encryptor().decrypt(delivery.target_recipient_enc) == "leads@example.test"

    asyncio.run(
        lead_transport.configure_lead_transport(
            db,
            tenant_id=tenant_id,
            host="smtp.example.test",
            port=465,
            tls_mode="implicit_tls",
            sender_email="sender@example.test",
            recipient="updated@example.test",
            username=None,
            password=None,
        )
    )
    assert db.transport.revision == 2
    assert asyncio.run(
        lead_transport.dispatch_lead_smtp(
            db,
            delivery=delivery,
            recipient="leads@example.test",
            payload={},
        )
    ) == {
        "ok": False,
        "error": "Lead SMTP transport was reconfigured",
        "retryable": False,
    }
    asyncio.run(lead_transport.disable_lead_transport(db, tenant_id))
    assert db.transport.enabled is False
    assert db.transport.revision == 3


def test_lead_transport_rejects_unsafe_host_and_unpaired_credentials():
    db = TransportDatabase()
    with pytest.raises(ValueError, match="SMTP host"):
        asyncio.run(
            lead_transport.configure_lead_transport(
                db,
                tenant_id=uuid4(),
                host="smtp.example.test/path",
                port=587,
                tls_mode="starttls",
                sender_email="sender@example.test",
                recipient="leads@example.test",
                username=None,
                password=None,
            )
        )
    with pytest.raises(ValueError, match="username and password"):
        asyncio.run(
            lead_transport.configure_lead_transport(
                db,
                tenant_id=uuid4(),
                host="smtp.example.test",
                port=587,
                tls_mode="starttls",
                sender_email="sender@example.test",
                recipient="leads@example.test",
                username="smtp-user",
                password=None,
            )
        )


def test_panel_lead_transport_migration_creates_isolated_encrypted_storage(monkeypatch):
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0069_panel_lead_smtp_transport.py"
    spec = importlib.util.spec_from_file_location("lead_transport_migration", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    calls: list[tuple[str, tuple[object, ...]]] = []

    monkeypatch.setattr(migration.op, "create_table", lambda *args: calls.append(("table", args)))
    monkeypatch.setattr(migration.op, "create_index", lambda *args: calls.append(("index", args)))
    monkeypatch.setattr(migration.op, "execute", lambda *args: calls.append(("execute", args)))
    monkeypatch.setattr(migration.op, "add_column", lambda *args: calls.append(("column", args)))

    migration.upgrade()

    assert migration.down_revision == "0068_central_lead_delivery_defaults"
    table = next(args for name, args in calls if name == "table")
    assert table[0] == "central_lead_smtp_transports"
    assert {column.name for column in table[1:] if hasattr(column, "name")} >= {
        "recipient_enc",
        "smtp_username_enc",
        "smtp_password_enc",
        "revision",
    }
    assert any("FORCE ROW LEVEL SECURITY" in args[0] for name, args in calls if name == "execute")
    assert any(
        name == "column" and args[1].name == "lead_transport_revision" for name, args in calls
    )
