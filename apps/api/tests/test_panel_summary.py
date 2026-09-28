from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.api.v1.panel import report_summary


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
    }
    assert next(alert for alert in summary["alerts"] if alert["code"] == "domain-tls-error") == {
        "code": "domain-tls-error",
        "severity": "critical",
        "title": "Ошибки DNS или TLS",
        "detail": "Проверьте конкретный домен до публикации или после изменения DNS.",
        "count": 1,
        "route": "/domains",
    }


def test_observability_route_is_registered():
    from app.main import app

    assert "get" in app.openapi()["paths"]["/api/v1/panel/reports/observability"]
