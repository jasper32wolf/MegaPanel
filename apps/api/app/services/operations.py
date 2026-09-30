from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.models.operations import AlertIncident, OperationalEvent
from app.models.project import PageDraft
from app.services.audit import append_audit
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

EVENT_TYPES = frozenset({"delivery", "qa", "release", "system", "worker"})
SEVERITIES = frozenset({"info", "warning", "critical"})
OUTCOMES = frozenset({"success", "warning", "failure", "recovered"})
SIGNALS = {
    "qa-block": "warning",
    "delivery-dead-letter": "critical",
    "release-failure": "critical",
    "system-operation-failed": "warning",
    "worker-heartbeat-stale": "warning",
}


def _require_value(value: str, allowed: frozenset[str], label: str) -> None:
    if value not in allowed:
        raise ValueError(f"Unsupported operational {label}")


def _serialize_incident(incident: AlertIncident) -> dict:
    return {
        "id": str(incident.id),
        "signal_code": incident.signal_code,
        "severity": incident.severity,
        "status": incident.status,
        "occurrence_count": incident.occurrence_count,
        "opened_at": incident.opened_at.isoformat() if incident.opened_at else None,
        "acknowledged_at": incident.acknowledged_at.isoformat()
        if incident.acknowledged_at
        else None,
        "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
    }


def serialize_operational_event(event: OperationalEvent) -> dict:
    return {
        "event_type": event.event_type,
        "severity": event.severity,
        "outcome": event.outcome,
        "quantity": event.quantity,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
    }


def record_operational_event(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    event_type: str,
    severity: str,
    outcome: str,
    quantity: int = 1,
) -> OperationalEvent:
    _require_value(event_type, EVENT_TYPES, "event type")
    _require_value(severity, SEVERITIES, "severity")
    _require_value(outcome, OUTCOMES, "outcome")
    if (
        not isinstance(quantity, int)
        or isinstance(quantity, bool)
        or quantity < 1
        or quantity > 10000
    ):
        raise ValueError("Operational quantity is invalid")
    event = OperationalEvent(
        tenant_id=tenant_id,
        event_type=event_type,
        severity=severity,
        outcome=outcome,
        quantity=quantity,
    )
    db.add(event)
    return event


async def observe_alert(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    signal_code: str,
    active: bool,
) -> AlertIncident | None:
    severity = SIGNALS.get(signal_code)
    if severity is None:
        raise ValueError("Unsupported operational signal")
    incident = (
        await db.execute(
            select(AlertIncident)
            .where(
                AlertIncident.tenant_id == tenant_id,
                AlertIncident.signal_code == signal_code,
                AlertIncident.status.in_(("open", "acknowledged")),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if active:
        if incident is None:
            incident = AlertIncident(
                tenant_id=tenant_id,
                signal_code=signal_code,
                severity=severity,
                status="open",
                occurrence_count=1,
                opened_at=now,
            )
            db.add(incident)
        else:
            incident.occurrence_count += 1
        return incident
    if incident is not None:
        incident.status = "resolved"
        incident.resolved_at = now
    return incident


async def auto_resolve_inactive_incidents(db: AsyncSession) -> int:
    incidents = list(
        (
            await db.execute(
                select(AlertIncident)
                .where(
                    AlertIncident.signal_code == "qa-block",
                    AlertIncident.status.in_(("open", "acknowledged")),
                )
                .order_by(AlertIncident.opened_at, AlertIncident.id)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    if not incidents:
        return 0

    tenant_ids = {incident.tenant_id for incident in incidents}
    blocking_counts = dict(
        (
            await db.execute(
                select(PageDraft.tenant_id, func.count(PageDraft.id))
                .where(
                    PageDraft.tenant_id.in_(tenant_ids),
                    PageDraft.last_qa_verdict == "block",
                )
                .group_by(PageDraft.tenant_id)
            )
        ).all()
    )
    resolved = 0
    for incident in incidents:
        if blocking_counts.get(incident.tenant_id, 0) > 0:
            continue
        await transition_incident(db, incident=incident, action="resolve")
        await append_audit(
            db,
            action="operational_incident.auto_resolve",
            payload={"incident_id": str(incident.id), "signal_code": incident.signal_code},
            tenant_id=incident.tenant_id,
            actor_id=None,
        )
        resolved += 1
    if resolved:
        await db.commit()
    return resolved


async def transition_incident(
    db: AsyncSession,
    *,
    incident: AlertIncident,
    action: str,
) -> AlertIncident:
    now = datetime.now(UTC)
    if action == "acknowledge":
        if incident.status == "open":
            incident.status = "acknowledged"
            incident.acknowledged_at = now
        return incident
    if action == "resolve":
        if incident.status != "resolved":
            incident.status = "resolved"
            incident.resolved_at = now
        return incident
    raise ValueError("Unsupported incident action")
