"""Durable PostgreSQL-backed delivery of lead webhook notifications."""

from __future__ import annotations

import asyncio
import json
import smtplib
import ssl
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from hashlib import sha256
from uuid import UUID

from app.core.config import get_settings
from app.models import Project, Site
from app.models.leads import (
    Lead,
    LeadDeliveryAggregate,
    LeadRoutingDestination,
    LeadRoutingPolicy,
    WebhookDelivery,
    WebhookDeliveryAttempt,
)
from app.models.project import ProjectFactRevision
from app.services.leads import dispatch_webhook, get_encryptor
from app.services.metrics import record_delivery_transition
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
    if "retryable" in result:
        return bool(result["retryable"])
    if result.get("error") in {
        "Webhook secret cannot be decrypted",
        "SMTP recipient cannot be decrypted",
        "SMTP transport is not configured",
        "Unsupported delivery channel",
    }:
        return False
    status = result.get("status")
    return status is None or status == 408 or status == 429 or status >= 500


def _next_attempt(attempt_count: int) -> datetime:
    delay = BACKOFF_SECONDS[min(attempt_count - 1, len(BACKOFF_SECONDS) - 1)]
    return _now() + timedelta(seconds=delay)


def _decrypt(value: str) -> str | None:
    try:
        return get_encryptor().decrypt(value)
    except Exception:  # noqa: BLE001
        return None


def _decrypt_secret(value: str) -> str | None:
    return _decrypt(value)


def wire_payload(delivery: WebhookDelivery, lead: Lead) -> dict:
    encryptor = get_encryptor()
    return {
        **delivery.payload,
        "phone": encryptor.decrypt(lead.phone_enc) if lead.phone_enc else None,
        "email": encryptor.decrypt(lead.email_enc) if lead.email_enc else None,
        "name": encryptor.decrypt(lead.name_enc) if lead.name_enc else None,
        "message": encryptor.decrypt(lead.message_enc) if lead.message_enc else None,
    }


def _delivery_payload(lead: Lead, site: Site) -> dict:
    return {
        "lead_id": str(lead.id),
        "site_id": str(site.id),
        "domain": site.domain,
        "page_slug": lead.page_slug,
        "qualification": lead.qualification,
        "idempotency_key": lead.idempotency_key or str(lead.id),
    }


def policy_hash(destinations: list[dict]) -> str:
    return sha256(
        json.dumps(destinations, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def active_routing_policy(
    db: AsyncSession, *, site: Site
) -> tuple[LeadRoutingPolicy, list[LeadRoutingDestination]] | None:
    if not site.project_id:
        return None
    policy = (
        await db.execute(
            select(LeadRoutingPolicy).where(
                LeadRoutingPolicy.tenant_id == site.tenant_id,
                LeadRoutingPolicy.project_id == site.project_id,
                LeadRoutingPolicy.site_id == site.id,
                LeadRoutingPolicy.state == "active",
            )
        )
    ).scalar_one_or_none()
    if not policy:
        return None
    destinations = list(
        (
            await db.execute(
                select(LeadRoutingDestination)
                .where(
                    LeadRoutingDestination.policy_id == policy.id,
                    LeadRoutingDestination.tenant_id == site.tenant_id,
                )
                .order_by(LeadRoutingDestination.target_key)
            )
        )
        .scalars()
        .all()
    )
    return policy, destinations


async def recompute_lead_delivery_aggregate(
    db: AsyncSession, *, lead: Lead
) -> LeadDeliveryAggregate:
    deliveries = list(
        (
            await db.execute(
                select(WebhookDelivery)
                .where(WebhookDelivery.lead_id == lead.id)
                .order_by(WebhookDelivery.created_at, WebhookDelivery.id)
            )
        )
        .scalars()
        .all()
    )
    required = [delivery for delivery in deliveries if getattr(delivery, "required", True)]
    pending_statuses = {"queued", "retrying", "processing"}
    expected_count = len(required)
    delivered_count = sum(delivery.status == "delivered" for delivery in required)
    attention_count = sum(delivery.status == "dead_letter" for delivery in required)
    pending_count = sum(delivery.status in pending_statuses for delivery in required)
    oldest_pending_at = min(
        (
            delivery.created_at
            for delivery in required
            if delivery.status in pending_statuses and delivery.created_at is not None
        ),
        default=None,
    )
    if not deliveries:
        status = "not_configured"
    elif attention_count:
        status = "attention"
    elif expected_count and delivered_count == expected_count:
        status = "delivered"
    else:
        status = "pending"
    aggregate = await db.get(LeadDeliveryAggregate, lead.id)
    if aggregate is None:
        aggregate = LeadDeliveryAggregate(lead_id=lead.id, tenant_id=lead.tenant_id)
        db.add(aggregate)
    aggregate.status = status
    aggregate.expected_count = expected_count
    aggregate.delivered_count = delivered_count
    aggregate.pending_count = pending_count
    aggregate.attention_count = attention_count
    aggregate.oldest_pending_at = oldest_pending_at
    lead.crm_status = status
    return aggregate


async def create_lead_delivery(
    db: AsyncSession, *, lead: Lead, site: Site
) -> WebhookDelivery | None:
    contacts = site.manifest.get("contacts") or {}
    url = str(contacts.get("webhook_url") or "").strip()
    if not url:
        return None
    target_secret_enc = str(contacts.get("webhook_secret_enc") or "") or None
    payload = _delivery_payload(lead, site)
    delivery = WebhookDelivery(
        tenant_id=lead.tenant_id,
        site_id=site.id,
        lead_id=lead.id,
        target_key="site_webhook",
        channel="webhook",
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
    return delivery


async def _private_lead_email_enc(db: AsyncSession, site: Site) -> str | None:
    if not site.project_id:
        return None
    return (
        await db.execute(
            select(ProjectFactRevision.private_lead_email_enc)
            .join(Project, Project.current_fact_revision_id == ProjectFactRevision.id)
            .where(
                Project.id == site.project_id,
                Project.tenant_id == site.tenant_id,
                ProjectFactRevision.project_id == Project.id,
                ProjectFactRevision.state == "confirmed",
            )
        )
    ).scalar_one_or_none()


def create_smtp_delivery(
    db: AsyncSession, *, lead: Lead, site: Site, recipient_enc: str
) -> WebhookDelivery:
    payload = _delivery_payload(lead, site)
    configured = get_settings().smtp_configured
    delivery = WebhookDelivery(
        tenant_id=lead.tenant_id,
        site_id=site.id,
        lead_id=lead.id,
        target_key="private_email",
        channel="email",
        target_recipient_enc=recipient_enc,
        payload=payload,
        idempotency_key=payload["idempotency_key"],
        status="queued" if configured else "dead_letter",
        max_attempts=MAX_ATTEMPTS,
        next_attempt_at=_now() if configured else None,
        last_error=None if configured else "SMTP transport is not configured",
        dead_lettered_at=None if configured else _now(),
    )
    db.add(delivery)
    return delivery


def create_policy_delivery(
    db: AsyncSession,
    *,
    lead: Lead,
    site: Site,
    policy: LeadRoutingPolicy,
    destination: LeadRoutingDestination,
) -> WebhookDelivery:
    payload = _delivery_payload(lead, site)
    if destination.channel == "webhook":
        configured = bool(destination.target_url and destination.target_secret_enc)
        delivery = WebhookDelivery(
            tenant_id=lead.tenant_id,
            site_id=site.id,
            lead_id=lead.id,
            routing_policy_id=policy.id,
            routing_policy_version=policy.version,
            routing_policy_hash=policy.policy_hash,
            required=destination.required,
            target_key=destination.target_key,
            channel="webhook",
            target_url=destination.target_url,
            target_secret_enc=destination.target_secret_enc,
            payload=payload,
            idempotency_key=payload["idempotency_key"],
            status="queued" if configured else "dead_letter",
            max_attempts=MAX_ATTEMPTS,
            next_attempt_at=_now() if configured else None,
            last_error=None if configured else "Webhook target is not configured",
            dead_lettered_at=None if configured else _now(),
        )
    elif destination.channel == "email":
        configured = bool(destination.target_recipient_enc and get_settings().smtp_configured)
        delivery = WebhookDelivery(
            tenant_id=lead.tenant_id,
            site_id=site.id,
            lead_id=lead.id,
            routing_policy_id=policy.id,
            routing_policy_version=policy.version,
            routing_policy_hash=policy.policy_hash,
            required=destination.required,
            target_key=destination.target_key,
            channel="email",
            target_recipient_enc=destination.target_recipient_enc,
            payload=payload,
            idempotency_key=payload["idempotency_key"],
            status="queued" if configured else "dead_letter",
            max_attempts=MAX_ATTEMPTS,
            next_attempt_at=_now() if configured else None,
            last_error=None if configured else "SMTP target is not configured",
            dead_lettered_at=None if configured else _now(),
        )
    else:
        raise ValueError("Unsupported routing destination channel")
    db.add(delivery)
    return delivery


async def create_lead_deliveries(
    db: AsyncSession, *, lead: Lead, site: Site
) -> list[WebhookDelivery]:
    routing = await active_routing_policy(db, site=site)
    if routing is not None:
        policy, destinations = routing
        return [
            create_policy_delivery(db, lead=lead, site=site, policy=policy, destination=destination)
            for destination in destinations
        ]

    deliveries = []
    webhook = await create_lead_delivery(db, lead=lead, site=site)
    if webhook:
        deliveries.append(webhook)
    recipient_enc = await _private_lead_email_enc(db, site)
    if recipient_enc:
        deliveries.append(
            create_smtp_delivery(db, lead=lead, site=site, recipient_enc=recipient_enc)
        )
    return deliveries


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
        lead = await db.get(Lead, delivery.lead_id)
        if lead is not None:
            await recompute_lead_delivery_aggregate(db, lead=lead)
        record_delivery_transition(channel=delivery.channel, status="retrying", trigger="recovery")
    await db.commit()
    return len(rows)


def _smtp_message(payload: dict, sender: str, recipient: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = f"Новая заявка с сайта {payload.get('domain') or 'Site Panel'}"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        "\n".join(
            [
                "Новая заявка",
                f"Сайт: {payload.get('domain') or '—'}",
                f"Страница: {payload.get('page_slug') or '—'}",
                f"Телефон: {payload.get('phone') or '—'}",
                f"Email: {payload.get('email') or '—'}",
                f"Имя: {payload.get('name') or '—'}",
                "",
                "Сообщение:",
                str(payload.get("message") or "—"),
            ]
        )
    )
    return message


def _send_smtp_message(payload: dict, recipient: str) -> None:
    settings = get_settings()
    message = _smtp_message(payload, settings.smtp_from_email, recipient)
    client_class = smtplib.SMTP_SSL if settings.smtp_use_ssl else smtplib.SMTP
    with client_class(settings.smtp_host, settings.smtp_port, timeout=10) as client:
        client.ehlo()
        if settings.smtp_starttls:
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(message, from_addr=settings.smtp_from_email, to_addrs=[recipient])


async def dispatch_smtp(recipient: str, payload: dict) -> dict:
    if not get_settings().smtp_configured:
        return {"ok": False, "error": "SMTP transport is not configured", "retryable": False}
    try:
        await asyncio.to_thread(_send_smtp_message, payload, recipient)
    except Exception:  # SMTP details can contain sensitive infrastructure context.
        return {"ok": False, "error": "SMTP delivery failed", "retryable": True}
    return {"ok": True}


async def _dispatch_delivery(delivery: WebhookDelivery, lead: Lead) -> dict:
    channel = getattr(delivery, "channel", "webhook")
    if channel == "webhook":
        if not delivery.target_secret_enc:
            return {"ok": False, "error": "Webhook secret is not configured", "retryable": False}
        secret = _decrypt_secret(delivery.target_secret_enc)
        return (
            await dispatch_webhook(delivery.target_url or "", wire_payload(delivery, lead), secret)
            if secret is not None
            else {"ok": False, "error": "Webhook secret cannot be decrypted", "retryable": False}
        )
    if channel == "email":
        recipient = _decrypt(delivery.target_recipient_enc or "")
        return (
            await dispatch_smtp(recipient, wire_payload(delivery, lead))
            if recipient
            else {"ok": False, "error": "SMTP recipient cannot be decrypted", "retryable": False}
        )
    return {"ok": False, "error": "Unsupported delivery channel", "retryable": False}


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
    lead = await db.get(Lead, delivery.lead_id)
    assert lead is not None
    await recompute_lead_delivery_aggregate(db, lead=lead)
    await db.commit()

    lead = await db.get(Lead, delivery.lead_id)
    assert lead is not None
    result = await _dispatch_delivery(delivery, lead)

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
    elif _retryable(result) and delivery.attempt_count < delivery.max_attempts:
        delivery.status = "retrying"
        delivery.next_attempt_at = _next_attempt(delivery.attempt_count)
        attempt.status = "retrying"
    else:
        delivery.status = "dead_letter"
        delivery.dead_lettered_at = finished
        delivery.next_attempt_at = None
        attempt.status = "dead_letter"
    await recompute_lead_delivery_aggregate(db, lead=lead)
    record_delivery_transition(channel=delivery.channel, status=delivery.status, trigger=trigger)
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
    await recompute_lead_delivery_aggregate(db, lead=lead)
