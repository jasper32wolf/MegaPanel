from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.services.operations import (
    observe_alert,
    record_operational_event,
    serialize_operational_event,
    transition_incident,
)


class OperationsDatabase:
    def __init__(self):
        self.active = None
        self.added: list[object] = []

    async def execute(self, _statement):
        incident = self.active
        if incident is not None and incident.status not in {"open", "acknowledged"}:
            incident = None
        return SimpleNamespace(scalar_one_or_none=lambda: incident)

    def add(self, item: object) -> None:
        self.added.append(item)
        if type(item).__name__ == "AlertIncident":
            self.active = item


def test_incident_lifecycle_deduplicates_active_signal_without_details():
    db = OperationsDatabase()
    tenant_id = uuid4()

    incident = asyncio.run(
        observe_alert(db, tenant_id=tenant_id, signal_code="qa-block", active=True)
    )
    assert incident is not None
    assert incident.status == "open"
    assert incident.occurrence_count == 1
    assert not hasattr(incident, "details")

    asyncio.run(transition_incident(db, incident=incident, action="acknowledge"))
    repeated = asyncio.run(
        observe_alert(db, tenant_id=tenant_id, signal_code="qa-block", active=True)
    )
    assert repeated is incident
    assert (incident.status, incident.occurrence_count) == ("acknowledged", 2)

    asyncio.run(observe_alert(db, tenant_id=tenant_id, signal_code="qa-block", active=False))
    assert incident.status == "resolved"

    reopened = asyncio.run(
        observe_alert(db, tenant_id=tenant_id, signal_code="qa-block", active=True)
    )
    assert reopened is not incident
    assert reopened.status == "open"
    assert reopened.occurrence_count == 1


def test_operational_events_are_allowlisted_and_redacted():
    db = OperationsDatabase()
    event = record_operational_event(
        db,
        tenant_id=uuid4(),
        event_type="qa",
        severity="warning",
        outcome="failure",
    )

    assert serialize_operational_event(event) == {
        "event_type": "qa",
        "severity": "warning",
        "outcome": "failure",
        "quantity": 1,
        "occurred_at": None,
    }
    assert not hasattr(event, "details")
    with pytest.raises(ValueError, match="event type"):
        record_operational_event(
            db,
            tenant_id=uuid4(),
            event_type="lead:customer@example.com",
            severity="warning",
            outcome="failure",
        )


def test_operational_migration_follows_index_promotion_head_and_enables_rls():
    path = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0036_operational_events_incidents.py"
    )
    spec = importlib.util.spec_from_file_location("operational_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.down_revision == "0035_page_index_promotions"
    source = path.read_text(encoding="utf-8")
    assert "operational_events" in source
    assert "alert_incidents" in source
    assert "ENABLE ROW LEVEL SECURITY" in source


def test_incident_and_event_routes_are_registered():
    from app.main import app

    paths = app.openapi()["paths"]
    assert "get" in paths["/api/v1/panel/incidents"]
    assert "patch" in paths["/api/v1/panel/incidents/{incident_id}"]
    assert "get" in paths["/api/v1/panel/events"]
