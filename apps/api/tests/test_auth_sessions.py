from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1 import auth
from app.core.security import ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME, create_refresh_token
from app.models import AuthSession
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response


def request_with_headers(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request({"type": "http", "headers": headers, "client": ("198.51.100.7", 443)})


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


def test_refresh_reuse_of_a_revoked_session_clears_cookies(monkeypatch):
    user = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        role="superadmin",
        is_active=True,
    )
    monkeypatch.setattr(auth.settings, "app_secret_key", "test-session-key-" + "x" * 32)
    refresh_token = create_refresh_token(user.id)
    revoked_session = auth._new_session(user, refresh_token)
    revoked_session.revoked_at = datetime.now(UTC)
    database = RefreshDatabase(revoked_session, user)
    response = Response()

    with pytest.raises(HTTPException, match="Refresh session expired or reused") as exc_info:
        asyncio.run(auth.refresh(refresh_request(refresh_token), response, database))

    assert exc_info.value.status_code == 401
    assert database.added == []
    assert database.commits == 1
    set_cookies = [
        value.decode() for name, value in response.raw_headers if name.lower() == b"set-cookie"
    ]
    access_cookie = next(
        value for value in set_cookies if value.startswith(f"{ACCESS_COOKIE_NAME}=")
    )
    refresh_cookie = next(
        value for value in set_cookies if value.startswith(f"{REFRESH_COOKIE_NAME}=")
    )
    assert "Max-Age=0" in access_cookie
    assert "Max-Age=0" in refresh_cookie


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


def test_refresh_rotation_preserves_family_and_safe_device_label(monkeypatch):
    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4(), role="superadmin", is_active=True)
    monkeypatch.setattr(auth.settings, "app_secret_key", "test-session-key-" + "x" * 32)
    refresh = create_refresh_token(user.id)
    session = auth._new_session(user, refresh, device_label="Chrome on Windows")
    replacement = auth._new_session(
        user,
        create_refresh_token(user.id),
        family_id=session.family_id,
        device_label=session.device_label,
    )

    assert session.family_id == session.id
    assert replacement.family_id == session.family_id
    assert replacement.device_label == "Chrome on Windows"


def test_session_context_keeps_only_bounded_browser_language_and_encrypted_location(monkeypatch):
    request = request_with_headers(
        [
            (b"user-agent", b"Mozilla/5.0 Chrome/123.0"),
            (b"sec-ch-ua", b'"Google Chrome";v="123", "Chromium";v="123"'),
            (b"accept-language", b"ru-RU,ru;q=0.9,en;q=0.8"),
        ]
    )
    location = SimpleNamespace(country="Russia", city="Kazan")
    monkeypatch.setattr(auth, "session_client_ip", lambda _request: "8.8.8.8")
    monkeypatch.setattr(
        auth,
        "get_geoip_resolver",
        lambda _path: SimpleNamespace(lookup=lambda _ip: location),
    )

    context = auth._session_context(request)

    assert context["browser_name"] == "Chrome"
    assert context["language"] == "ru-RU"
    assert context["ip_address_enc"] != "8.8.8.8"
    assert auth.get_encryptor().decrypt(context["ip_address_enc"]) == "8.8.8.8"
    assert auth.get_encryptor().decrypt(context["country_enc"]) == "Russia"
    assert auth.get_encryptor().decrypt(context["city_enc"]) == "Kazan"


def test_new_session_copies_the_original_environment_snapshot_on_rotation(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(auth.settings, "app_secret_key", "test-session-key-" + "x" * 32)
    first = auth._new_session(
        user,
        create_refresh_token(user.id),
        browser_name="Firefox",
        language="en-US",
        ip_address_enc="encrypted-ip",
        country_enc="encrypted-country",
        city_enc="encrypted-city",
    )
    replacement = auth._new_session(
        user,
        create_refresh_token(user.id),
        family_id=first.family_id,
        browser_name=first.browser_name,
        language=first.language,
        ip_address_enc=first.ip_address_enc,
        country_enc=first.country_enc,
        city_enc=first.city_enc,
    )

    assert replacement.family_id == first.family_id
    assert replacement.browser_name == "Firefox"
    assert replacement.language == "en-US"
    assert replacement.ip_address_enc == "encrypted-ip"
    assert replacement.country_enc == "encrypted-country"
    assert replacement.city_enc == "encrypted-city"


@pytest.mark.parametrize(
    ("user_agent", "expected"),
    [
        ("Mozilla/5.0 YaBrowser/25.1 Chrome/132.0", "Yandex Browser"),
        ("Mozilla/5.0 OPR/117.0 Chrome/132.0", "Opera"),
        ("Mozilla/5.0 Vivaldi/7.1 Chrome/132.0", "Vivaldi"),
        ("Mozilla/5.0 Edg/132.0 Chrome/132.0", "Microsoft Edge"),
    ],
)
def test_browser_name_prioritizes_specific_chromium_markers(user_agent: str, expected: str):
    request = request_with_headers([(b"user-agent", user_agent.encode())])

    assert auth._browser_name(request) == expected


def test_browser_name_does_not_claim_unknown_chromium_is_chrome():
    request = request_with_headers([(b"user-agent", b"Mozilla/5.0 Chrome/132.0")])

    assert auth._browser_name(request) == "Chromium browser"


def test_refresh_backfills_context_for_legacy_session(monkeypatch):
    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4(), role="superadmin", is_active=True)
    monkeypatch.setattr(auth.settings, "app_secret_key", "test-session-key-" + "x" * 32)
    refresh = create_refresh_token(user.id)
    legacy_session = auth._new_session(user, refresh, device_label="Chrome on Windows")
    database = RefreshDatabase(legacy_session, user)

    async def require_single_operator(_db: object, _user: object) -> None:
        return None

    monkeypatch.setattr(auth, "require_single_operator", require_single_operator)
    monkeypatch.setattr(auth, "_device_label", lambda _request: "Yandex Browser on Windows")
    monkeypatch.setattr(
        auth,
        "_session_context",
        lambda _request: {
            "browser_name": "Yandex Browser",
            "language": "ru-RU",
            "ip_address_enc": "encrypted-ip",
            "country_enc": "encrypted-country",
            "city_enc": "encrypted-city",
        },
    )

    asyncio.run(auth.refresh(refresh_request(refresh), Response(), database))

    replacement = database.added[0]
    assert replacement.device_label == "Yandex Browser on Windows"
    assert replacement.browser_name == "Yandex Browser"
    assert replacement.language == "ru-RU"
    assert replacement.ip_address_enc == "encrypted-ip"
    assert replacement.country_enc == "encrypted-country"
    assert replacement.city_enc == "encrypted-city"
