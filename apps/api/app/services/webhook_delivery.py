"""Durable PostgreSQL-backed delivery of lead webhook notifications."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.config import get_settings
from app.models import Site
from app.models.leads import Lead, WebhookDelivery, WebhookDeliveryAttempt
from app.services.leads import dispatch_webhook, get_encryptor
from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

MAX_ATTEMPTS = 5
LEASE_SECONDS = 300
BACKOFF_SECONDS = (60, 300, 900, 3600, 3600)


def _now() -> datetime:
    return datetime.now(UTC)


def _error(result: dict) -> str | None:
    value = result.get("error")
    return str(value)[:512] if value else None


def _retryable(result: dict) -> bool:
    if result.get("error") == "Webhook secret cannot be decrypted":
        return False
    status = result.get("status")
    return status is None or status == 408 or status == 429 or status >= 500


def _next_attempt(attempt_count: int) -> datetime:
    delay = BACKOFF_SECONDS[min(attempt_count - 1, len(BACKOFF_SECONDS) - 1)]
    return _now() + timedelta(seconds=delay)


def _decrypt_secret(value: str) -> str | None:
    try:
        return get_encryptor().decrypt(value)
    except Exception:  # noqa: BLE001
        return None


async def create_lead_delivery(
    db: AsyncSession, *, lead: Lead, site: Site
) -> WebhookDelivery | None:
    contacts = site.manifest.get("contacts") or {}
    url = str(contacts.get("webhook_url") or "").strip()
    if not url:
        return None
    legacy_secret = str(contacts.get("webhook_secret") or "")
    secret_enc = str(contacts.get("webhook_secret_enc") or "")
    target_secret_enc = secret_enc or (
        get_encryptor().encrypt(legacy_secret) if legacy_secret else None
    )
    payload = {
        "lead_id": str(lead.id),
        "site_id": str(site.id),
        "domain": site.domain,
        "page_slug": lead.page_slug,
        "qualification": lead.qualification,
        "idempotency_key": lead.idempotency_key or str(lead.id),
    }
    delivery = WebhookDelivery(
        tenant_id=lead.tenant_id,
        site_id=site.id,
        lead_id=lead.id,
        target_key="site_contacts",
        target_url=url,
        target_secret_enc=target_secret_enc,
        payload=payload,
        idempotency_key=payload["idempotency_key"],
        status="queued" if target_secret_enc else "dead_letter",
        max_attempts=MAX_ATTEMPTS,
        next_attempt_at=_now() if target_secret_enc else None,
        last_error=None if target_secret_enc else "Webhook secret is not configured",
        dead_lettered_at=None if target_secret_enc else _now(),
    )
    db.add(delivery)
    if not target_secret_enc:
        lead.crm_status = "dead_letter"
    return delivery


async def enqueue_delivery(delivery_id: UUID, *, trigger: str = "automatic") -> bool:
    try:
        redis = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
        try:
            await redis.enqueue_job("webhook_delivery_task", str(delivery_id), trigger)
        finally:
            await redis.aclose()
    except Exception:  # The database due-job sweep remains the source of recovery.
        return False
    return True


async def due_delivery_ids(db: AsyncSession, *, limit: int = 50) -> list[UUID]:
    now = _now()
    result = await db.execute(
        select(WebhookDelivery.id)
        .where(
            WebhookDelivery.status.in_(("queued", "retrying")),
            WebhookDelivery.next_attempt_at.is_not(None),
            WebhookDelivery.next_attempt_at <= now,
        )
        .order_by(WebhookDelivery.next_attempt_at)
        .limit(limit)
    )
    return list(result.scalars().all())


async def recover_expired_leases(db: AsyncSession) -> int:
    now = _now()
    rows = list(
        (
            await db.execute(
                select(WebhookDelivery)
                .where(WebhookDelivery.status == "processing", WebhookDelivery.locked_until < now)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for delivery in rows:
        delivery.status = "retrying"
        delivery.next_attempt_at = now
        delivery.locked_until = None
        delivery.last_error = "Worker lease expired"
    await db.commit()
    return len(rows)


async def process_delivery(
    db: AsyncSession, delivery_id: UUID, *, trigger: str = "automatic"
) -> dict:
    now = _now()
    delivery = (
        await db.execute(
            select(WebhookDelivery)
            .where(WebhookDelivery.id == delivery_id)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if not delivery:
        return {"status": "skipped"}
    if delivery.status in {"delivered", "dead_letter"}:
        return {"status": delivery.status}
    if delivery.locked_until and delivery.locked_until > now:
        return {"status": "locked"}
    if trigger == "automatic" and delivery.next_attempt_at and delivery.next_attempt_at > now:
        return {"status": "not_due"}

    delivery.attempt_count += 1
    delivery.status = "processing"
    delivery.locked_until = now + timedelta(seconds=LEASE_SECONDS)
    sequence = (
        await db.execute(
            select(func.coalesce(func.max(WebhookDeliveryAttempt.sequence), 0)).where(
                WebhookDeliveryAttempt.delivery_id == delivery.id
            )
        )
    ).scalar_one() + 1
    attempt = WebhookDeliveryAttempt(
        delivery_id=delivery.id,
        tenant_id=delivery.tenant_id,
        sequence=sequence,
        trigger=trigger,
        status="processing",
    )
    db.add(attempt)
    await db.commit()

    if not delivery.target_secret_enc:
        result = {"ok": False, "error": "Webhook secret is not configured"}
    else:
        secret = _decrypt_secret(delivery.target_secret_enc)
        result = (
            await dispatch_webhook(delivery.target_url, delivery.payload, secret)
            if secret is not None
            else {"ok": False, "error": "Webhook secret cannot be decrypted"}
        )

    delivery = (
        await db.execute(
            select(WebhookDelivery).where(WebhookDelivery.id == delivery_id).with_for_update()
        )
    ).scalar_one()
    attempt = (
        await db.execute(
            select(WebhookDeliveryAttempt).where(
                WebhookDeliveryAttempt.delivery_id == delivery_id,
                WebhookDeliveryAttempt.sequence == sequence,
            )
        )
    ).scalar_one()
    finished = _now()
    http_status = result.get("status")
    delivery.locked_until = None
    delivery.last_http_status = http_status if isinstance(http_status, int) else None
    delivery.last_error = _error(result) or (
        f"HTTP {http_status}" if isinstance(http_status, int) and http_status >= 300 else None
    )
    attempt.http_status = delivery.last_http_status
    attempt.error = delivery.last_error
    attempt.finished_at = finished

    lead = (
        await db.execute(select(Lead).where(Lead.id == delivery.lead_id).with_for_update())
    ).scalar_one()
    if result.get("ok"):
        delivery.status = "delivered"
        delivery.delivered_at = finished
        delivery.next_attempt_at = None
        attempt.status = "delivered"
        lead.crm_status = "sent"
    elif _retryable(result) and delivery.attempt_count < delivery.max_attempts:
        delivery.status = "retrying"
        delivery.next_attempt_at = _next_attempt(delivery.attempt_count)
        attempt.status = "retrying"
        lead.crm_status = "retrying"
    else:
        delivery.status = "dead_letter"
        delivery.dead_lettered_at = finished
        delivery.next_attempt_at = None
        attempt.status = "dead_letter"
        lead.crm_status = "dead_letter"
    await db.commit()
    return {"status": delivery.status, "delivery_id": str(delivery.id), "attempt": sequence}


async def resend_delivery(db: AsyncSession, delivery: WebhookDelivery) -> None:
    if delivery.status != "dead_letter":
        raise ValueError("Delivery is already queued")
    delivery.status = "queued"
    delivery.attempt_count = 0
    delivery.next_attempt_at = _now()
    delivery.locked_until = None
    delivery.last_error = None
    delivery.last_http_status = None
    delivery.dead_lettered_at = None
    delivery.delivered_at = None
    lead = (
        await db.execute(select(Lead).where(Lead.id == delivery.lead_id).with_for_update())
    ).scalar_one()
    lead.crm_status = "queued"
