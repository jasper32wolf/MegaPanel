from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.services.operations import (
    auto_resolve_inactive_incidents,
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


class EvaluatorDatabase:
    def __init__(self, incidents: list[object], blocking_counts: dict[object, int]):
        self.incidents = incidents
        self.blocking_counts = blocking_counts
        self.calls = 0
        self.commits = 0

    async def execute(self, _statement):
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(scalars=lambda: self.incidents)
        return SimpleNamespace(all=lambda: list(self.blocking_counts.items()))

    async def commit(self) -> None:
        self.commits += 1


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


def test_auto_resolution_closes_only_inactive_qa_block_incidents(monkeypatch):
    inactive_tenant, active_tenant = uuid4(), uuid4()
    open_incident = SimpleNamespace(
        id=uuid4(), tenant_id=inactive_tenant, signal_code="qa-block", status="open"
    )
    acknowledged_incident = SimpleNamespace(
        id=uuid4(), tenant_id=inactive_tenant, signal_code="qa-block", status="acknowledged"
    )
    active_incident = SimpleNamespace(
        id=uuid4(), tenant_id=active_tenant, signal_code="qa-block", status="open"
    )
    db = EvaluatorDatabase(
        [open_incident, acknowledged_incident, active_incident], {active_tenant: 1}
    )
    audits: list[dict] = []

    async def fake_append_audit(_db, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr("app.services.operations.append_audit", fake_append_audit)

    assert asyncio.run(auto_resolve_inactive_incidents(db)) == 2
    assert open_incident.status == acknowledged_incident.status == "resolved"
    assert active_incident.status == "open"
    assert db.commits == 1
    assert len(audits) == 2
    assert all(audit["actor_id"] is None for audit in audits)
    assert all(audit["action"] == "operational_incident.auto_resolve" for audit in audits)
    assert all(set(audit["payload"]) == {"incident_id", "signal_code"} for audit in audits)


def test_auto_resolution_does_not_commit_when_nothing_is_repaired(monkeypatch):
    tenant_id = uuid4()
    incident = SimpleNamespace(
        id=uuid4(), tenant_id=tenant_id, signal_code="qa-block", status="open"
    )
    db = EvaluatorDatabase([incident], {tenant_id: 2})
    monkeypatch.setattr(
        "app.services.operations.append_audit",
        lambda *_args, **_kwargs: pytest.fail("unexpected audit"),
    )

    assert asyncio.run(auto_resolve_inactive_incidents(db)) == 0
    assert incident.status == "open"
    assert db.commits == 0


def test_auto_resolution_is_noop_without_active_qa_incidents(monkeypatch):
    db = EvaluatorDatabase([], {})
    monkeypatch.setattr(
        "app.services.operations.append_audit",
        lambda *_args, **_kwargs: pytest.fail("unexpected audit"),
    )

    assert asyncio.run(auto_resolve_inactive_incidents(db)) == 0
    assert db.calls == 1
    assert db.commits == 0


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
