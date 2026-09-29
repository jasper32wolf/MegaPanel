from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1 import security_ops
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


def test_session_summary_keeps_all_active_and_limits_recent_rows():
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

    assert [item["id"] for item in payload["active"]] == [str(current.id), str(active.id)]
    assert payload["active"][0]["current"] is True
    assert payload["recent"] == [
        {
            "id": str(recent.id),
            "family_id": str(recent.family_id),
            "device_label": None,
            "current": False,
            "created_at": recent.created_at.isoformat(),
            "expires_at": recent.expires_at.isoformat(),
            "revoked_at": recent.revoked_at.isoformat(),
        }
    ]
    assert payload["history_total"] == 1


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

    result = asyncio.run(
        security_ops.revoke_session(target.id, auth_context(user, uuid4()), database)
    )

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

    assert len(payload["active"]) == 1
    assert payload["active"][0]["id"] == str(current.id)
    assert payload["active"][0]["current"] is True
    assert payload["recent"] == []


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
