from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.schemas.workflow import IndexPromotionScheduleCreate
from pydantic import ValidationError


def test_index_schedule_schema_requires_unique_paths_and_timezone():
    starts_at = datetime.now(UTC) + timedelta(hours=2)
    schedule = IndexPromotionScheduleCreate(
        slugs=["/repair", "/contacts"],
        reason="Постепенно открыть проверенные страницы для индексации.",
        starts_at=starts_at,
        interval_hours=24,
        batch_size=1,
    )

    assert schedule.slugs == ["/repair", "/contacts"]
    assert schedule.starts_at == starts_at
    with pytest.raises(ValidationError):
        IndexPromotionScheduleCreate(
            slugs=["/repair", "repair"],
            reason="Постепенно открыть проверенные страницы для индексации.",
            starts_at=starts_at,
        )
    with pytest.raises(ValidationError):
        IndexPromotionScheduleCreate(
            slugs=["/repair"],
            reason="Постепенно открыть проверенные страницы для индексации.",
            starts_at=starts_at.replace(tzinfo=None),
        )


def test_index_schedule_routes_and_worker_keep_publication_separate():
    from app.main import app

    paths = app.openapi()["paths"]
    base = "/api/v1/projects/{project_id}/index-schedules"
    assert "get" in paths[base]
    assert "post" in paths[base]
    assert "post" in paths[f"{base}/{{schedule_id}}/submit-review"]
    assert "post" in paths[f"{base}/{{schedule_id}}/approve"]

    root = Path(__file__).parents[1]
    service = (root / "app" / "services" / "index_schedule.py").read_text(encoding="utf-8")
    worker = (root / "app" / "worker.py").read_text(encoding="utf-8")

    assert "_freeze_candidate_build_input" in service
    assert 'status="queued"' in service
    assert "publish_project_build" not in service
    assert "index_schedule_sweep_task" in worker
    assert "never publish it" in worker
