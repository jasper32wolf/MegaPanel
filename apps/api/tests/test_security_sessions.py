from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1 import security_ops
from app.services.leads import get_encryptor
from fastapi import HTTPException


class QueryResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value

    def scalars(self) -> QueryResult:
        return self

    def all(self) -> list[object]:
        return self.value if isinstance(self.value, list) else [self.value]


class SessionRows:
    def __init__(self, items: list[object]) -> None:
        self.items = items

    def scalars(self) -> SessionRows:
        return self

    def all(self) -> list[object]:
        return self.items


class SessionListDatabase:
    def __init__(self, sessions: list[object]) -> None:
        self.sessions = sessions

    async def execute(self, _statement: object) -> SessionRows:
        return SessionRows(self.sessions)


class SecurityDatabase:
    def __init__(self, result: object | None = None) -> None:
        self.result = result
        self.commits = 0

    async def execute(self, _statement: object) -> QueryResult:
        return QueryResult(self.result if isinstance(self.result, list) else [self.result])

    async def commit(self) -> None:
        self.commits += 1


def auth_context(user: object, session_id=None) -> object:
    return SimpleNamespace(
        user=user,
        tenant_id=getattr(user, "tenant_id", None),
        session_id=session_id,
    )


def test_totp_confirmation_only_enables_a_verified_pending_secret(monkeypatch):
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        totp_pending="pending-secret",
        totp_secret=None,
        mfa_enabled=False,
    )
    database = SecurityDatabase()

    async def append_audit(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(security_ops, "append_audit", append_audit)
    alerts: list[dict] = []
    monkeypatch.setattr(
        security_ops, "create_operator_alert", lambda *_args, **kwargs: alerts.append(kwargs)
    )
    monkeypatch.setattr(
        security_ops,
        "verify_totp",
        lambda secret, code: secret == "pending-secret" and code == "123456",
    )

    result = asyncio.run(
        security_ops.totp_confirm(
            security_ops.TotpConfirm(code="123456"), auth_context(user), database
        )
    )

    assert result == {"ok": True, "mfa_enabled": True}
    assert user.totp_secret == "pending-secret"
    assert user.totp_pending is None
    assert user.mfa_enabled is True
    assert alerts[0]["signal_code"] == "security-sensitive-change"
    assert database.commits == 1


def test_totp_confirmation_rejects_an_invalid_code(monkeypatch):
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        totp_pending="pending-secret",
        totp_secret=None,
        mfa_enabled=False,
    )
    database = SecurityDatabase()
    monkeypatch.setattr(security_ops, "verify_totp", lambda *_args: False)

    with pytest.raises(HTTPException, match="Invalid TOTP code"):
        asyncio.run(
            security_ops.totp_confirm(
                security_ops.TotpConfirm(code="000000"), auth_context(user), database
            )
        )

    assert user.totp_pending == "pending-secret"
    assert user.totp_secret is None
    assert user.mfa_enabled is False
    assert database.commits == 0


def test_session_summary_limits_all_families_to_ten_rows():
    now = datetime.now(UTC)
    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    current = SimpleNamespace(
        id=uuid4(),
        created_at=now,
        expires_at=now + timedelta(days=1),
        revoked_at=None,
        family_id=uuid4(),
        device_label="Chrome on Windows",
    )
    active = SimpleNamespace(
        id=uuid4(),
        created_at=now - timedelta(hours=1),
        expires_at=now + timedelta(days=1),
        revoked_at=None,
        family_id=uuid4(),
        device_label="Firefox on macOS",
    )
    recent = SimpleNamespace(
        id=uuid4(),
        created_at=now - timedelta(days=1),
        expires_at=now - timedelta(hours=1),
        revoked_at=now - timedelta(hours=2),
        family_id=uuid4(),
        device_label=None,
    )

    payload = asyncio.run(
        security_ops.list_sessions(
            auth_context(user, current.id), SessionListDatabase([current, active, recent])
        )
    )

    assert [item["id"] for item in payload["items"]] == [
        str(current.id),
        str(active.id),
        str(recent.id),
    ]
    assert payload["items"][0]["current"] is True
    assert payload["items"][0]["status"] == "current"
    assert payload["items"][1]["can_revoke"] is True
    assert payload["items"][2] == {
        "id": str(recent.id),
        "device_label": None,
        "browser_name": None,
        "language": None,
        "ip_address": None,
        "country": None,
        "city": None,
        "current": False,
        "can_revoke": False,
        "status": "revoked",
        "created_at": recent.created_at.isoformat(),
        "expires_at": recent.expires_at.isoformat(),
        "revoked_at": recent.revoked_at.isoformat(),
    }
    assert payload["total"] == 3
    assert payload["older_total"] == 0


def test_session_serialization_exposes_only_decrypted_snapshot_fields():
    now = datetime.now(UTC)
    encryptor = get_encryptor()
    family_id = uuid4()
    session = SimpleNamespace(
        id=uuid4(),
        family_id=family_id,
        device_label="Chrome on Windows",
        browser_name="Chrome",
        language="ru-RU",
        ip_address_enc=encryptor.encrypt("8.8.8.8"),
        country_enc=encryptor.encrypt("Russia"),
        city_enc=encryptor.encrypt("Kazan"),
        created_at=now,
        expires_at=now + timedelta(days=1),
        revoked_at=None,
        refresh_jti_hash="secret-refresh-hash",
    )

    payload = security_ops._serialize_session(session, family_id, {family_id})

    assert payload["ip_address"] == "8.8.8.8"
    assert payload["country"] == "Russia"
    assert payload["city"] == "Kazan"
    assert "ip_address_enc" not in payload
    assert "secret-refresh-hash" not in str(payload)
    assert session.ip_address_enc not in str(payload)


def test_session_history_excludes_the_ten_rows_visible_in_settings():
    now = datetime.now(UTC)
    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    sessions = [
        SimpleNamespace(
            id=uuid4(),
            family_id=uuid4(),
            device_label=f"Browser {index}",
            created_at=now - timedelta(minutes=index),
            expires_at=now + timedelta(days=1),
            revoked_at=None,
        )
        for index in range(12)
    ]
    database = SessionListDatabase(sessions)

    summary = asyncio.run(security_ops.list_sessions(auth_context(user, sessions[0].id), database))
    history = asyncio.run(
        security_ops.list_session_history(
            offset=0,
            limit=25,
            auth=auth_context(user, sessions[0].id),
            db=database,
        )
    )

    assert len(summary["items"]) == 10
    assert summary["total"] == 12
    assert summary["older_total"] == 2
    assert [item["id"] for item in history["items"]] == [str(sessions[10].id), str(sessions[11].id)]
    assert history["total"] == 2


def test_session_revoke_marks_only_a_noncurrent_session(monkeypatch):
    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    target = SimpleNamespace(
        id=uuid4(),
        revoked_at=None,
        family_id=uuid4(),
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=1),
        device_label=None,
    )
    database = SecurityDatabase([target])

    async def append_audit(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(security_ops, "append_audit", append_audit)
    alerts: list[dict] = []
    monkeypatch.setattr(
        security_ops, "create_operator_alert", lambda *_args, **kwargs: alerts.append(kwargs)
    )

    result = asyncio.run(
        security_ops.revoke_session(target.id, auth_context(user, uuid4()), database)
    )
    assert alerts[0]["subject_kind"] == "session_family"

    assert result == {"id": str(target.id), "revoked": True}
    assert target.revoked_at is not None
    assert database.commits == 1


def test_session_revoke_rejects_the_current_session():
    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    session_id = uuid4()

    with pytest.raises(HTTPException, match="Use logout"):
        asyncio.run(
            security_ops.revoke_session(
                session_id,
                auth_context(user, session_id),
                SecurityDatabase(),
            )
        )


def test_rotated_rows_are_one_visible_active_family():
    now = datetime.now(UTC)
    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    family_id = uuid4()
    old = SimpleNamespace(
        id=uuid4(),
        family_id=family_id,
        device_label="Chrome on Windows",
        created_at=now - timedelta(hours=1),
        expires_at=now + timedelta(days=1),
        revoked_at=now,
    )
    current = SimpleNamespace(
        id=uuid4(),
        family_id=family_id,
        device_label="Chrome on Windows",
        created_at=now,
        expires_at=now + timedelta(days=1),
        revoked_at=None,
    )

    payload = asyncio.run(
        security_ops.list_sessions(
            auth_context(user, current.id), SessionListDatabase([current, old])
        )
    )

    assert len(payload["items"]) == 1
    assert payload["items"][0]["id"] == str(current.id)
    assert payload["items"][0]["current"] is True
    assert payload["older_total"] == 0


def test_audit_history_returns_metadata_without_payload():
    entry = SimpleNamespace(
        id=1,
        action="lead.pii_reveal",
        actor_id=uuid4(),
        created_at=datetime.now(UTC),
        record_hash="a" * 64,
        payload={"phone": "+79990000000", "secret": "must-not-leak"},
    )

    class Database:
        async def scalar(self, _statement):
            return 1

        async def execute(self, _statement):
            return SessionRows([entry])

    auth = SimpleNamespace(role="superadmin", tenant_id=None)
    payload = asyncio.run(
        security_ops.list_audit_history(action=None, offset=0, limit=50, auth=auth, db=Database())
    )

    assert payload["total"] == 1
    assert payload["items"] == [
        {
            "id": 1,
            "action": "lead.pii_reveal",
            "actor_id": str(entry.actor_id),
            "created_at": entry.created_at.isoformat(),
            "record_hash": "a" * 64,
        }
    ]
    assert "payload" not in str(payload)


def test_audit_routes_are_registered():
    from app.main import app

    paths = app.openapi()["paths"]

    assert "get" in paths["/api/v1/security/audit"]
    assert "get" in paths["/api/v1/security/audit/integrity"]
