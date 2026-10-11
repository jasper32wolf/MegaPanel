from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.config import get_settings
from app.models import (
    AlertDelivery,
    AuthSession,
    Notification,
    OperationalEvent,
    StorageRetentionRun,
    Tenant,
    User,
    WebhookDelivery,
    WebhookDeliveryAttempt,
)
from app.services.audit import append_audit
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

_LOCK_KEY = 734589215
_BATCH_SIZE = 500
_TERMINAL_DELIVERY_STATUSES = ("delivered",)


def _now() -> datetime:
    return datetime.now(UTC)


def effective_policy() -> dict[str, int]:
    settings = get_settings()
    return {
        "auth_session_retention_days": settings.auth_session_retention_days,
        "notification_retention_days": settings.notification_retention_days,
        "operational_event_retention_days": settings.operational_event_retention_days,
        "delivery_attempt_retention_days": settings.delivery_attempt_retention_days,
        "dsar_export_ttl_hours": settings.dsar_export_ttl_hours,
    }


def policy_hash() -> str:
    payload = json.dumps(effective_policy(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _session_selector(*, tenant_id: UUID, cutoff: datetime):
    return (
        AuthSession.user_id == User.id,
        User.tenant_id == tenant_id,
        or_(AuthSession.expires_at < cutoff, AuthSession.revoked_at < cutoff),
    )


def _notification_selector(*, cutoff: datetime):
    active_delivery = select(AlertDelivery.id).where(
        AlertDelivery.notification_id == Notification.id,
        AlertDelivery.status.not_in(_TERMINAL_DELIVERY_STATUSES),
    )
    return (
        Notification.read.is_(True),
        Notification.created_at < cutoff,
        ~active_delivery.exists(),
    )


def _webhook_attempt_selector(*, tenant_id: UUID, cutoff: datetime):
    return (
        WebhookDeliveryAttempt.delivery_id == WebhookDelivery.id,
        WebhookDeliveryAttempt.tenant_id == tenant_id,
        WebhookDelivery.status == "delivered",
        WebhookDeliveryAttempt.finished_at.is_not(None),
        WebhookDeliveryAttempt.finished_at < cutoff,
    )


async def build_preview(
    db: AsyncSession, *, tenant_id: UUID, requested_by: UUID, now: datetime | None = None
) -> StorageRetentionRun:
    now = now or _now()
    policy = effective_policy()
    session_cutoff = now - timedelta(days=policy["auth_session_retention_days"])
    notification_cutoff = now - timedelta(days=policy["notification_retention_days"])
    event_cutoff = now - timedelta(days=policy["operational_event_retention_days"])
    webhook_attempt_cutoff = now - timedelta(days=policy["delivery_attempt_retention_days"])
    session_count = await db.scalar(
        select(func.count(AuthSession.id)).select_from(AuthSession, User).where(
            *_session_selector(tenant_id=tenant_id, cutoff=session_cutoff)
        )
    )
    notification_count = await db.scalar(
        select(func.count(Notification.id)).where(
            Notification.tenant_id == tenant_id,
            *_notification_selector(cutoff=notification_cutoff),
        )
    )
    event_count = await db.scalar(
        select(func.count(OperationalEvent.id)).where(
            OperationalEvent.tenant_id == tenant_id,
            OperationalEvent.occurred_at < event_cutoff,
        )
    )
    webhook_attempt_count = await db.scalar(
        select(func.count(WebhookDeliveryAttempt.id))
        .select_from(WebhookDeliveryAttempt, WebhookDelivery)
        .where(*_webhook_attempt_selector(tenant_id=tenant_id, cutoff=webhook_attempt_cutoff))
    )
    preview = {
        "policy": policy,
        "sources": {
            "auth_sessions": int(session_count or 0),
            "read_terminal_notifications": int(notification_count or 0),
            "operational_events": int(event_count or 0),
            "delivered_webhook_attempts": int(webhook_attempt_count or 0),
        },
        "protected": {
            "audit_log": "immutable",
            "open_incidents": "excluded",
            "active_deliveries": "excluded",
            "release_evidence": "excluded",
        },
    }
    run = StorageRetentionRun(
        tenant_id=tenant_id,
        requested_by=requested_by,
        status="preview",
        policy_hash=policy_hash(),
        preview=preview,
        expires_at=now + timedelta(minutes=get_settings().storage_retention_preview_minutes),
    )
    db.add(run)
    await append_audit(
        db,
        action="storage.preview.created",
        payload={"policy_hash": run.policy_hash, "sources": preview["sources"]},
        tenant_id=tenant_id,
        actor_id=requested_by,
    )
    await db.commit()
    return run


async def queue_preview_run(
    db: AsyncSession,
    *,
    run_id: UUID,
    tenant_id: UUID,
    requested_by: UUID,
    confirmation: str,
    now: datetime | None = None,
) -> StorageRetentionRun:
    now = now or _now()
    run = (
        await db.execute(
            select(StorageRetentionRun)
            .where(StorageRetentionRun.id == run_id, StorageRetentionRun.tenant_id == tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if run is None:
        raise ValueError("Retention preview not found")
    if run.requested_by != requested_by:
        raise ValueError("Retention preview belongs to another operator")
    if run.status != "preview" or run.expires_at <= now:
        raise ValueError("Retention preview has expired")
    if run.policy_hash != policy_hash():
        raise ValueError("Retention policy changed; create a new preview")
    if confirmation != "PURGE EXPIRED DATA":
        raise ValueError("Retention confirmation does not match")
    run.status = "queued"
    await append_audit(
        db,
        action="storage.retention.queued",
        payload={"run_id": str(run.id), "policy_hash": run.policy_hash},
        tenant_id=tenant_id,
        actor_id=requested_by,
    )
    await db.commit()
    return run


async def queue_scheduled_retention_runs(db: AsyncSession, *, now: datetime | None = None) -> int:
    now = now or _now()
    current_policy_hash = policy_hash()
    tenant_ids = list(
        (await db.execute(select(Tenant.id).where(Tenant.is_active.is_(True)))).scalars()
    )
    queued = 0
    for tenant_id in tenant_ids:
        existing = await db.scalar(
            select(StorageRetentionRun.id).where(
                StorageRetentionRun.tenant_id == tenant_id,
                StorageRetentionRun.status.in_(("queued", "processing")),
            )
        )
        if existing is not None:
            continue
        db.add(
            StorageRetentionRun(
                tenant_id=tenant_id,
                requested_by=None,
                status="queued",
                policy_hash=current_policy_hash,
                preview={"policy": effective_policy(), "source": "scheduled"},
                expires_at=now,
            )
        )
        await append_audit(
            db,
            action="storage.retention.scheduled",
            payload={"policy_hash": current_policy_hash},
            tenant_id=tenant_id,
        )
        queued += 1
    if queued:
        await db.commit()
    return queued


async def _delete_ids(db: AsyncSession, model: type, statement) -> int:
    deleted = 0
    while True:
        ids = list((await db.execute(statement.limit(_BATCH_SIZE))).scalars())
        if not ids:
            return deleted
        await db.execute(delete(model).where(model.id.in_(ids)))
        deleted += len(ids)


async def _execute_run(
    db: AsyncSession, run: StorageRetentionRun, *, now: datetime
) -> dict[str, int]:
    policy = effective_policy()
    session_cutoff = now - timedelta(days=policy["auth_session_retention_days"])
    notification_cutoff = now - timedelta(days=policy["notification_retention_days"])
    event_cutoff = now - timedelta(days=policy["operational_event_retention_days"])
    webhook_attempt_cutoff = now - timedelta(days=policy["delivery_attempt_retention_days"])
    sessions = await _delete_ids(
        db,
        AuthSession,
        select(AuthSession.id).select_from(AuthSession, User).where(
            *_session_selector(tenant_id=run.tenant_id, cutoff=session_cutoff)
        ),
    )
    notifications = await _delete_ids(
        db,
        Notification,
        select(Notification.id).where(
            Notification.tenant_id == run.tenant_id,
            *_notification_selector(cutoff=notification_cutoff),
        ),
    )
    events = await _delete_ids(
        db,
        OperationalEvent,
        select(OperationalEvent.id).where(
            OperationalEvent.tenant_id == run.tenant_id,
            OperationalEvent.occurred_at < event_cutoff,
        ),
    )
    webhook_attempts = await _delete_ids(
        db,
        WebhookDeliveryAttempt,
        select(WebhookDeliveryAttempt.id)
        .select_from(WebhookDeliveryAttempt, WebhookDelivery)
        .where(
            *_webhook_attempt_selector(
                tenant_id=run.tenant_id,
                cutoff=webhook_attempt_cutoff,
            )
        ),
    )
    return {
        "auth_sessions": sessions,
        "read_terminal_notifications": notifications,
        "operational_events": events,
        "delivered_webhook_attempts": webhook_attempts,
    }


async def process_queued_retention_runs(db: AsyncSession, *, limit: int = 1) -> int:
    now = _now()
    run_ids = list(
        (
            await db.execute(
                select(StorageRetentionRun.id)
                .where(StorageRetentionRun.status == "queued")
                .order_by(StorageRetentionRun.created_at, StorageRetentionRun.id)
                .limit(limit)
            )
        ).scalars()
    )
    completed = 0
    for run_id in run_ids:
        run = (
            await db.execute(
                select(StorageRetentionRun)
                .where(StorageRetentionRun.id == run_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if run is None or run.status != "queued":
            continue
        if run.policy_hash != policy_hash():
            run.status = "failed"
            run.error_code = "policy_changed"
            run.completed_at = now
            await append_audit(
                db,
                action="storage.retention.failed",
                payload={"run_id": str(run.id), "error_code": run.error_code},
                tenant_id=run.tenant_id,
                actor_id=run.requested_by,
            )
            await db.commit()
            completed += 1
            continue
        run.status = "processing"
        run.started_at = now
        await db.commit()
        try:
            await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
            result = await _execute_run(db, run, now=now)
            run.result = result
            run.status = "succeeded"
            run.completed_at = _now()
            await append_audit(
                db,
                action="storage.retention.completed",
                payload={"run_id": str(run.id), "result": result},
                tenant_id=run.tenant_id,
                actor_id=run.requested_by,
            )
            await db.commit()
        except Exception:  # noqa: BLE001
            await db.rollback()
            run = await db.get(StorageRetentionRun, run_id, with_for_update=True)
            if run is not None:
                run.status = "failed"
                run.error_code = "retention_failed"
                run.completed_at = _now()
                await append_audit(
                    db,
                    action="storage.retention.failed",
                    payload={"run_id": str(run.id), "error_code": run.error_code},
                    tenant_id=run.tenant_id,
                    actor_id=run.requested_by,
                )
                await db.commit()
        completed += 1
    return completed


def serialize_run(run: StorageRetentionRun) -> dict:
    return {
        "id": str(run.id),
        "status": run.status,
        "policy_hash": run.policy_hash,
        "preview": run.preview,
        "result": run.result,
        "error_code": run.error_code,
        "expires_at": run.expires_at.isoformat() if run.expires_at else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }
