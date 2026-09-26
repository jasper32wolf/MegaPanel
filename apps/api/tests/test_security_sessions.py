from __future__ import annotations

import asyncio
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


class SecurityDatabase:
    def __init__(self, result: object | None = None) -> None:
        self.result = result
        self.commits = 0

    async def execute(self, _statement: object) -> QueryResult:
        return QueryResult(self.result)

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


def test_session_revoke_marks_only_a_noncurrent_session(monkeypatch):
    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    target = SimpleNamespace(id=uuid4(), revoked_at=None)
    database = SecurityDatabase(target)

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
