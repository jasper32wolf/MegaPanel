from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.api.v1 import auth
from app.core.security import ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME, create_refresh_token
from app.models import AuthSession
from starlette.requests import Request
from starlette.responses import Response


class QueryResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class RefreshDatabase:
    def __init__(self, session: AuthSession, user: object) -> None:
        self.results = [QueryResult(session), QueryResult(user)]
        self.added: list[AuthSession] = []
        self.commits = 0

    async def execute(self, _statement: object) -> QueryResult:
        return self.results.pop(0)

    def add(self, value: AuthSession) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.added[-1].id = uuid4()

    async def commit(self) -> None:
        self.commits += 1


def refresh_request(refresh_token: str) -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(b"cookie", f"{REFRESH_COOKIE_NAME}={refresh_token}".encode())],
        }
    )


def test_refresh_rotates_the_persisted_session_and_cookies(monkeypatch):
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        role="superadmin",
        is_active=True,
    )
    monkeypatch.setattr(auth.settings, "app_secret_key", "test-session-key-" + "x" * 32)
    old_refresh = create_refresh_token(user.id)
    old_session = auth._new_session(user, old_refresh)
    database = RefreshDatabase(old_session, user)

    async def require_single_operator(_db: object, _user: object) -> None:
        return None

    monkeypatch.setattr(auth, "require_single_operator", require_single_operator)
    response = Response()

    result = asyncio.run(auth.refresh(refresh_request(old_refresh), response, database))

    assert result == {"ok": True}
    assert old_session.revoked_at is not None
    assert old_session.replaced_by_id is not None
    assert len(database.added) == 1
    replacement = database.added[0]
    assert replacement.id == old_session.replaced_by_id
    assert replacement.user_id == user.id
    assert replacement.refresh_jti_hash != old_session.refresh_jti_hash
    assert database.commits == 1
    set_cookies = [
        value.decode() for name, value in response.raw_headers if name.lower() == b"set-cookie"
    ]
    assert any(value.startswith(f"{ACCESS_COOKIE_NAME}=") for value in set_cookies)
    assert any(value.startswith(f"{REFRESH_COOKIE_NAME}=") for value in set_cookies)


def test_production_session_cookies_are_secure_and_scoped(monkeypatch):
    monkeypatch.setattr(auth.settings, "app_env", "production")
    response = Response()

    auth._set_session_cookies(response, "access", "refresh")

    set_cookies = {
        value.decode().split("=", 1)[0]: value.decode()
        for name, value in response.raw_headers
        if name.lower() == b"set-cookie"
    }
    assert "HttpOnly" in set_cookies[ACCESS_COOKIE_NAME]
    assert "Path=/api/v1" in set_cookies[ACCESS_COOKIE_NAME]
    assert "SameSite=strict" in set_cookies[ACCESS_COOKIE_NAME]
    assert "Secure" in set_cookies[ACCESS_COOKIE_NAME]
    assert "HttpOnly" in set_cookies[REFRESH_COOKIE_NAME]
    assert "Path=/api/v1/auth" in set_cookies[REFRESH_COOKIE_NAME]
    assert "SameSite=strict" in set_cookies[REFRESH_COOKIE_NAME]
    assert "Secure" in set_cookies[REFRESH_COOKIE_NAME]
    assert "HttpOnly" not in set_cookies[auth.CSRF_COOKIE_NAME]
    assert "Path=/" in set_cookies[auth.CSRF_COOKIE_NAME]
    assert "SameSite=strict" in set_cookies[auth.CSRF_COOKIE_NAME]
    assert "Secure" in set_cookies[auth.CSRF_COOKIE_NAME]
