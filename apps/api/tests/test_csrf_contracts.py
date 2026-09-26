from __future__ import annotations

from uuid import uuid4

from app.core.middleware import CsrfMiddleware
from app.core.security import ACCESS_COOKIE_NAME, CSRF_COOKIE_NAME
from app.main import app
from fastapi import FastAPI
from fastapi.testclient import TestClient


def set_csrf_cookies(client: TestClient, token: str = "csrf-token") -> None:
    client.cookies.set(ACCESS_COOKIE_NAME, "access-token")
    client.cookies.set(CSRF_COOKIE_NAME, token)


def test_login_remains_the_only_auth_mutation_without_csrf_cookie():
    csrf_app = FastAPI()
    csrf_app.add_middleware(CsrfMiddleware)

    @csrf_app.post("/api/v1/auth/login")
    async def login() -> dict[str, bool]:
        return {"ok": True}

    @csrf_app.post("/api/v1/auth/refresh")
    async def refresh() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(csrf_app) as client:
        assert client.post("/api/v1/auth/login").status_code == 200
        set_csrf_cookies(client)
        assert client.post("/api/v1/auth/refresh").status_code == 403
        assert (
            client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": "csrf-token"}).status_code
            == 200
        )


def test_registered_refresh_rejects_missing_or_mismatched_csrf_tokens():
    with TestClient(app) as client:
        set_csrf_cookies(client)
        assert client.post("/api/v1/auth/refresh").status_code == 403
        assert (
            client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": "other-token"}).status_code
            == 403
        )


def test_domain_health_is_a_csrf_protected_post_mutation():
    domain_id = uuid4()
    path = f"/api/v1/domains/health/{domain_id}"
    paths = app.openapi()["paths"]

    assert "get" not in paths["/api/v1/domains/health/{domain_id}"]
    assert "post" in paths["/api/v1/domains/health/{domain_id}"]

    with TestClient(app) as client:
        assert client.get(path).status_code == 405
        set_csrf_cookies(client)
        assert client.post(path).status_code == 403
