from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.main import app
from app.services import site_metrics


class SeriesDatabase:
    def __init__(self, rows: list[tuple]):
        self.rows = rows

    async def execute(self, _statement):
        return SimpleNamespace(all=lambda: self.rows)


def test_site_metrics_report_route_is_registered_read_only():
    paths = app.openapi()["paths"]

    assert set(paths["/api/v1/panel/reports/site-metrics"]) == {"get"}


def test_page_view_series_zero_fills_missing_dates_and_suppresses_small_samples(monkeypatch):
    now = datetime(2026, 10, 12, 12, tzinfo=UTC)
    day = datetime(2026, 10, 11, tzinfo=UTC).date()
    monkeypatch.setattr(
        site_metrics,
        "get_settings",
        lambda: SimpleNamespace(telemetry_raw_retention_days=30),
    )
    db = SeriesDatabase([(day, 6, 4)])

    series = asyncio.run(
        site_metrics.page_view_series(
            db,
            tenant_id=uuid4(),
            site_id=None,
            days=3,
            now=now,
        )
    )

    assert series == [
        {
            "date": "2026-10-10",
            "page_views": 0,
            "consented_session_days": None,
            "low_sample": False,
        },
        {"date": "2026-10-11", "page_views": 6, "consented_session_days": None, "low_sample": True},
        {
            "date": "2026-10-12",
            "page_views": 0,
            "consented_session_days": None,
            "low_sample": False,
        },
    ]


def test_site_metrics_only_declares_external_search_sources_as_not_connected():
    source = site_metrics.__file__ and open(site_metrics.__file__, encoding="utf-8").read()

    assert '"google_search_console": "not_connected"' in source
    assert '"yandex_webmaster": "not_connected"' in source
    assert 'AnalyticsEvent.source == "first_party"' in source
