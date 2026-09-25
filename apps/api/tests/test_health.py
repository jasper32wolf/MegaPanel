from __future__ import annotations

import asyncio

import pytest
from app.api.v1 import health
from app.core.config import Settings
from app.main import app
from fastapi.testclient import TestClient
from pydantic import ValidationError


def test_liveness_routes_do_not_require_dependencies(monkeypatch):
    async def unavailable() -> None:
        raise RuntimeError("postgresql://internal.example.test/secret")

    monkeypatch.setattr(health, "_postgres_ready", unavailable)
    monkeypatch.setattr(health, "_redis_ready", unavailable)

    with TestClient(app) as client:
        for path in ("/api/v1/health", "/api/v1/health/live"):
            response = client.get(path)

            assert response.status_code == 200
            assert response.json()["status"] == "ok"


def test_readiness_returns_ok_when_dependencies_are_available(monkeypatch):
    async def available() -> None:
        return None

    monkeypatch.setattr(health, "_postgres_ready", available)
    monkeypatch.setattr(health, "_redis_ready", available)

    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("dependency", ["_postgres_ready", "_redis_ready"])
def test_readiness_hides_dependency_failures(monkeypatch, dependency):
    async def unavailable() -> None:
        raise RuntimeError("redis://internal.example.test/secret")

    async def available() -> None:
        return None

    monkeypatch.setattr(health, "_postgres_ready", available)
    monkeypatch.setattr(health, "_redis_ready", available)
    monkeypatch.setattr(health, dependency, unavailable)

    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "internal" not in response.text
    assert "secret" not in response.text


def test_readiness_times_out(monkeypatch):
    async def unavailable() -> None:
        await asyncio.Event().wait()

    async def available() -> None:
        return None

    monkeypatch.setattr(health, "_postgres_ready", unavailable)
    monkeypatch.setattr(health, "_redis_ready", available)
    monkeypatch.setattr(health.settings, "health_readiness_timeout_seconds", 0.01)

    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_readiness_cancels_the_other_dependency_after_failure(monkeypatch):
    cancelled = asyncio.Event()

    async def unavailable() -> None:
        raise RuntimeError("postgres unavailable")

    async def pending() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(health, "_postgres_ready", unavailable)
    monkeypatch.setattr(health, "_redis_ready", pending)

    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert cancelled.is_set()


def test_redis_readiness_closes_client(monkeypatch):
    class FakeRedis:
        closed = False

        async def ping(self) -> None:
            return None

        async def aclose(self) -> None:
            self.closed = True

    fake_redis = FakeRedis()
    monkeypatch.setattr("redis.asyncio.Redis.from_url", lambda *_args, **_kwargs: fake_redis)

    asyncio.run(health._redis_ready())

    assert fake_redis.closed is True


def test_readiness_timeout_setting_is_bounded():
    with pytest.raises(ValidationError):
        Settings(health_readiness_timeout_seconds=0)

    with pytest.raises(ValidationError):
        Settings(health_readiness_timeout_seconds=11)
