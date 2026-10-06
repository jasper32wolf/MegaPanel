from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1 import telemetry as telemetry_api
from app.models.leads import AnalyticsEvent
from app.services.telemetry import (
    normalize_telemetry_path,
    session_digest,
    telemetry_token,
    verify_telemetry_token,
)
from app.services.telemetry_retention import _valid_first_party, page_view_summary, rollup_and_purge
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from starlette.requests import Request


def test_telemetry_token_is_scoped_to_the_exact_site_and_domain():
    site_id = uuid4()
    token = telemetry_token(site_id=site_id, domain="example.test")

    assert verify_telemetry_token(token=token, site_id=site_id, domain="example.test")
    assert not verify_telemetry_token(token=token, site_id=site_id, domain="other.test")
    assert not verify_telemetry_token(token=token, site_id=uuid4(), domain="example.test")


def test_telemetry_rejects_query_urls_and_never_keeps_raw_session_identity():
    assert normalize_telemetry_path("/repair/") == "/repair/"
    assert normalize_telemetry_path("https://example.test/repair/") is None
    assert normalize_telemetry_path("/repair/?phone=123") is None
    assert normalize_telemetry_path("/repair/#contact") is None
    assert session_digest("session-identifier-123") != "session-identifier-123"
    assert session_digest("short") is None
    assert normalize_telemetry_path("/" + "a" * 511) == "/" + "a" * 511
    assert normalize_telemetry_path("/" + "a" * 512) is None


def test_rollup_filters_paths_without_unsupported_postgres_regex_repetition():
    statement = select(AnalyticsEvent.id).where(*_valid_first_party())
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "left(analytics_events.path" in sql
    assert "char_length(analytics_events.path)" in sql
    assert "strpos(analytics_events.path" in sql
    assert "{0,511}" not in sql


def test_secure_telemetry_route_and_ssg_script_are_consent_gated():
    from app.main import app

    paths = app.openapi()["paths"]
    assert "post" in paths["/api/v1/telemetry/collect"]
    assert "get" in paths["/api/v1/telemetry/sites/{site_id}/summary"]

    root = Path(__file__).parents[1]
    route = (root / "app" / "api" / "v1" / "telemetry.py").read_text(encoding="utf-8")
    builder = (
        root.parents[1] / "packages" / "ssg" / "src" / "site_panel_ssg" / "builder.py"
    ).read_text(encoding="utf-8")

    assert "tenant_id" not in route.split("class TelemetryEventIn", 1)[1].split(
        "async def _site_from_public_token", 1
    )[0]
    assert "origin does not match the site" in route
    assert "client_ip" not in route.split("AnalyticsEvent(", 1)[1].split("await db.commit()", 1)[0]
    assert "post" in paths["/api/v1/telemetry/revoke"]
    assert "window.__spConsent?.analytics" in builder
    assert "navigator.globalPrivacyControl" in builder


def test_collection_rejects_gpc_and_replayed_withdrawn_sessions(monkeypatch):
    site = SimpleNamespace(id=uuid4(), tenant_id=uuid4(), domain="example.test")
    token = telemetry_token(site_id=site.id, domain=site.domain)
    session_id = "consented-browser-session-123"

    class FakeDB:
        def __init__(self):
            self.events = []
            self.commits = 0
            self.rollbacks = 0

        async def execute(self, statement):
            return SimpleNamespace(scalar_one_or_none=lambda: site)

        def add(self, event):
            self.events.append(event)

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    async def no_limit(key):
        return None

    async def no_lock(db, digest):
        assert digest == session_digest(session_id)

    revoked = False

    async def is_revoked(db, **kwargs):
        assert kwargs["tenant_id"] == site.tenant_id
        assert kwargs["site_id"] == site.id
        return revoked

    monkeypatch.setattr(telemetry_api.shared_lead_limiter, "check", no_limit)
    monkeypatch.setattr(telemetry_api, "lock_session", no_lock)
    monkeypatch.setattr(telemetry_api, "session_was_revoked", is_revoked)
    db = FakeDB()
    body = telemetry_api.TelemetryEventIn(
        token=token, event="page_view", path="/legal/", session_id=session_id,
        consent_analytics=True,
    )

    def request(origin="https://example.test", gpc=False):
        headers = [(b"origin", origin.encode("ascii"))]
        if gpc:
            headers.append((b"sec-gpc", b"1"))
        return Request({"type": "http", "headers": headers, "client": ("127.0.0.1", 1234)})

    async def run():
        nonlocal revoked
        assert await telemetry_api.collect_telemetry(body, request(), db) == {"ok": True}
        assert len(db.events) == 1
        assert db.events[0].source == "first_party"
        assert db.events[0].payload == {"session": session_digest(session_id)}
        revoked = True
        assert await telemetry_api.collect_telemetry(body, request(), db) == {
            "ok": False, "reason": "consent_withdrawn",
        }
        assert db.rollbacks == 1 and len(db.events) == 1
        assert await telemetry_api.collect_telemetry(body, request(gpc=True), db) == {
            "ok": False, "reason": "no_consent",
        }
        assert db.commits == 1
        with pytest.raises(HTTPException) as error:
            await telemetry_api.collect_telemetry(body, request("http://example.test"), db)
        assert error.value.status_code == 403

    asyncio.run(run())


def test_summary_hides_small_counts_and_labels_daily_sessions():
    class FakeDB:
        async def execute(self, statement):
            return SimpleNamespace(all=lambda: [("/small/", 4, 4), ("/popular/", 10, 5)])

    result = asyncio.run(
        page_view_summary(FakeDB(), tenant_id=uuid4(), site_id=uuid4(), days=30)
    )
    assert result[0] == {
        "path": "/small/", "page_views": None, "consented_session_days": None,
        "low_sample": True, "traffic_state": "not_enough_data",
    }
    assert result[1]["page_views"] == 10
    assert result[1]["consented_session_days"] == 5


def test_failed_rollup_never_commits_a_raw_purge():
    class FailedDB:
        def __init__(self):
            self.commits = 0
            self.statements = []

        async def execute(self, statement, params=None):
            sql = str(statement)
            self.statements.append(sql)
            if sql.startswith("DELETE FROM analytics_events"):
                raise RuntimeError("database write failed")

        async def scalar(self, statement):
            return 7

        async def commit(self):
            self.commits += 1

    db = FailedDB()
    with pytest.raises(RuntimeError, match="database write failed"):
        asyncio.run(rollup_and_purge(db, now=datetime(2026, 10, 6, 12, tzinfo=UTC)))
    assert any(sql.startswith("INSERT INTO analytics_daily_aggregates") for sql in db.statements)
    assert db.commits == 0


def test_changed_retention_blocks_frozen_candidate_publication():
    from app.api.v1.projects import _telemetry_retention_blockers
    from app.core.config import get_settings

    settings = get_settings()
    build = SimpleNamespace(input_snapshot={"context": {
        "telemetry_retention": {
            "raw_days": settings.telemetry_raw_retention_days,
            "aggregate_days": settings.telemetry_aggregate_retention_days,
        }
    }})
    assert _telemetry_retention_blockers(build) == []
    build.input_snapshot["context"]["telemetry_retention"]["raw_days"] += 1
    assert "rebuild the candidate" in _telemetry_retention_blockers(build)[0]
    assert _telemetry_retention_blockers(SimpleNamespace(input_snapshot={})) == []


def test_daily_retention_is_registered_in_the_real_worker():
    from app.worker import WorkerSettings, telemetry_retention_task

    assert telemetry_retention_task in WorkerSettings.functions
    assert any(
        job.coroutine is telemetry_retention_task for job in WorkerSettings.cron_jobs
    )
