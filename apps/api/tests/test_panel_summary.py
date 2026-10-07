from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.api.v1.panel import (
    _latest_ready_candidate,
    _manifest_media_reference_observation,
    _worker_heartbeat_observation,
    report_summary,
)


class Result:
    def __init__(self, rows: list[object]):
        self.rows = rows

    def all(self):
        return self.rows

    def scalars(self):
        return self


class SummaryDatabase:
    def __init__(self):
        self.results = [
            Result(
                [
                    SimpleNamespace(
                        publish_state="published",
                        manifest={"pages": [{"slug": "/"}, {"slug": "/repair"}]},
                    ),
                    SimpleNamespace(publish_state="draft", manifest={"pages": [{"slug": "/"}]}),
                ]
            ),
            Result([("new", 2), ("qualified", 1)]),
            Result([("queued", 2), ("dead_letter", 1)]),
            Result([("error", 1), ("pending", 2)]),
            Result([("active", 1)]),
            Result([("review", 2)]),
            Result([("review", 3), ("failed", 1)]),
            Result([("ready", 1)]),
            Result([("failed", 1)]),
            Result([("attention", 1)]),
            Result([("review", 1)]),
            Result([]),
        ]

    async def execute(self, _: object) -> Result:
        return self.results.pop(0)

    async def scalar(self, _: object):
        return None


def test_report_summary_returns_actionable_current_state_alerts():
    auth = SimpleNamespace(role="superadmin", tenant_id=None)

    summary = asyncio.run(report_summary(auth=auth, db=SummaryDatabase()))

    assert summary["sites"] == 2
    assert summary["published_sites"] == 1
    assert summary["pages_estimate"] == 3
    assert summary["active_leads"] == 3
    assert summary["delivery_pending"] == 2
    assert summary["delivery_dead_letter"] == 1
    assert summary["page_plan_statuses"] == {"review": 2}
    assert summary["page_draft_statuses"] == {"review": 3, "failed": 1}
    assert summary["build_statuses"] == {"ready": 1}
    assert {alert["code"] for alert in summary["alerts"]} == {
        "lead-delivery-dead-letter",
        "domain-tls-error",
        "page-draft-failed",
        "review-queue",
        "system-operation-failed",
        "lead-routing-review",
    }
    assert next(alert for alert in summary["alerts"] if alert["code"] == "domain-tls-error") == {
        "code": "domain-tls-error",
        "severity": "critical",
        "title": "Ошибки DNS или TLS",
        "detail": "Проверьте конкретный домен до публикации или после изменения DNS.",
        "count": 1,
        "route": "/domains",
    }


def test_observability_latest_success_uses_ready_candidate_not_legacy_success_state():
    tenant_id = uuid4()
    candidate = SimpleNamespace(status="ready", tenant_id=tenant_id)

    class CaptureDatabase:
        query = None

        async def execute(self, statement):
            self.query = str(statement.compile(compile_kwargs={"literal_binds": True}))
            return SimpleNamespace(scalar_one_or_none=lambda: candidate)

    db = CaptureDatabase()
    auth = SimpleNamespace(role="manager", tenant_id=tenant_id)
    assert asyncio.run(_latest_ready_candidate(db, auth)) is candidate
    assert "site_builds.status = 'ready'" in db.query
    assert tenant_id.hex in db.query
    assert "success" not in db.query


def test_materialized_media_references_are_aggregated_without_claiming_a_full_graph():
    shared_asset = "11111111-1111-1111-1111-111111111111"
    block_asset = "22222222-2222-2222-2222-222222222222"
    observation = _manifest_media_reference_observation(
        [
            SimpleNamespace(
                manifest={
                    "media": [{"asset_id": shared_asset}],
                    "block_media": {"hero": {"asset_id": block_asset}},
                }
            ),
            SimpleNamespace(manifest={"media": [{"asset_id": shared_asset}]}),
            SimpleNamespace(manifest={"media": [{"asset_id": "not-a-uuid"}]}),
        ]
    )

    assert observation == {
        "status": "manifest_snapshot",
        "source": "materialized_site_page_manifest",
        "assets": 2,
        "pages": 2,
        "invalid_entries": 1,
    }


def test_worker_heartbeat_observation_is_truthful_about_freshness():
    now = datetime(2026, 9, 29, 12, tzinfo=UTC)

    assert _worker_heartbeat_observation(None, now, 90) == {
        "status": "not_observed",
        "last_heartbeat_at": None,
        "age_seconds": None,
        "stale_after_seconds": 90,
        "reason": "No persisted worker heartbeat has been recorded",
    }
    fresh = _worker_heartbeat_observation(now - timedelta(seconds=30), now, 90)
    assert fresh["status"] == "healthy"
    assert fresh["age_seconds"] == 30
    assert fresh["reason"] is None
    stale = _worker_heartbeat_observation(now - timedelta(seconds=91), now, 90)
    assert stale["status"] == "stale"
    assert stale["age_seconds"] == 91
    assert stale["stale_after_seconds"] == 90


def test_observability_route_is_registered():
    from app.main import app

    assert "get" in app.openapi()["paths"]["/api/v1/panel/reports/observability"]
