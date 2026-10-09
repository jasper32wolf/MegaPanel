from __future__ import annotations

import asyncio
import smtplib
import ssl
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from uuid import UUID, uuid4

import httpx
from app.core.config import get_settings
from app.models import AlertDelivery, AlertDeliveryAttempt, Notification, OperatorAlertRecipient
from app.services.leads import get_encryptor
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

MAX_ATTEMPTS = 5
BACKOFF_SECONDS = (60, 300, 900, 3600, 3600)
LEASE_SECONDS = 300
ALERT_CATEGORIES = frozenset({"security", "site", "system"})
DELIVERY_CHANNELS = frozenset({"email", "telegram"})


def _now() -> datetime:
    return datetime.now(UTC)


def _bounded(value: str, maximum: int) -> str:
    return value[:maximum]


def _settings_channels() -> tuple[str, ...]:
    settings = get_settings()
    if not settings.operator_alerts_enabled:
        return ()
    return tuple(
        channel
        for channel, configured in (
            ("email", settings.smtp_configured),
            ("telegram", settings.telegram_configured),
        )
        if configured
    )


def alert_channel_status() -> dict[str, bool]:
    settings = get_settings()
    return {
        "enabled": settings.operator_alerts_enabled,
        "email": settings.smtp_configured,
        "telegram": settings.telegram_configured,
    }


def create_operator_alert(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    category: str,
    signal_code: str,
    title: str,
    body: str,
    subject_kind: str | None = None,
    subject_key: str | None = None,
    user_id: UUID | None = None,
) -> Notification:
    if category not in ALERT_CATEGORIES:
        raise ValueError("Unsupported alert category")
    if not signal_code or len(signal_code) > 64:
        raise ValueError("Invalid alert signal")
    notification = Notification(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        priority="high" if category == "security" else "normal",
        title=_bounded(title, 255),
        body=_bounded(body, 1000),
        group_key=f"{signal_code}:{subject_kind or 'system'}:{subject_key or ''}"[:128],
        category=category,
        signal_code=signal_code,
        subject_kind=_bounded(subject_kind or "system", 32),
        subject_key=_bounded(subject_key or "", 64),
    )
    db.add(notification)
    for channel in _settings_channels():
        db.add(
            AlertDelivery(
                tenant_id=tenant_id,
                notification_id=notification.id,
                channel=channel,
                status="queued",
                idempotency_key=f"{notification.id}:{channel}",
                next_attempt_at=_now(),
            )
        )
    return notification


async def _operator_email(db: AsyncSession, tenant_id: UUID) -> str | None:
    recipient = (
        await db.execute(
            select(OperatorAlertRecipient).where(OperatorAlertRecipient.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if recipient is None:
        return None
    try:
        return get_encryptor().decrypt(recipient.recipient_enc)
    except Exception:  # noqa: BLE001
        return None


def mask_email(value: str) -> str:
    local, _, domain = value.partition("@")
    if not local or not domain:
        return "настроен"
    return f"{local[:1]}***@{domain}"


async def alert_recipient_status(db: AsyncSession, tenant_id: UUID) -> dict[str, str | bool | None]:
    recipient = await _operator_email(db, tenant_id)
    return {
        "configured": bool(recipient),
        "masked_email": mask_email(recipient) if recipient else None,
    }


async def set_alert_recipient(db: AsyncSession, *, tenant_id: UUID, email: str) -> None:
    recipient = (
        await db.execute(
            select(OperatorAlertRecipient)
            .where(OperatorAlertRecipient.tenant_id == tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    encrypted = get_encryptor().encrypt(email)
    if recipient is None:
        db.add(OperatorAlertRecipient(tenant_id=tenant_id, recipient_enc=encrypted))
    else:
        recipient.recipient_enc = encrypted


async def clear_alert_recipient(db: AsyncSession, *, tenant_id: UUID) -> bool:
    recipient = (
        await db.execute(
            select(OperatorAlertRecipient)
            .where(OperatorAlertRecipient.tenant_id == tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if recipient is None:
        return False
    await db.delete(recipient)
    return True


def _send_email(recipient: str, title: str, body: str) -> None:
    settings = get_settings()
    message = EmailMessage()
    message["Subject"] = title
    message["From"] = settings.smtp_from_email
    message["To"] = recipient
    message.set_content(body)
    client_class = smtplib.SMTP_SSL if settings.smtp_use_ssl else smtplib.SMTP
    with client_class(settings.smtp_host, settings.smtp_port, timeout=10) as client:
        client.ehlo()
        if settings.smtp_starttls:
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(message, from_addr=settings.smtp_from_email, to_addrs=[recipient])


async def _dispatch_email(
    db: AsyncSession, notification: Notification
) -> tuple[bool, str | None, bool]:
    recipient = await _operator_email(db, notification.tenant_id)
    if not recipient or not get_settings().smtp_configured:
        return False, "invalid_config", False
    try:
        await asyncio.to_thread(_send_email, recipient, notification.title, notification.body)
    except (OSError, smtplib.SMTPException):
        return False, "unavailable", True
    except Exception:
        return False, "unavailable", True
    return True, None, False


async def _dispatch_telegram(notification: Notification) -> tuple[bool, str | None, bool]:
    settings = get_settings()
    if not settings.telegram_configured:
        return False, "invalid_config", False
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                json={
                    "chat_id": settings.telegram_chat_id,
                    "text": f"{notification.title}\n\n{notification.body}",
                },
            )
    except httpx.TimeoutException:
        return False, "timeout", True
    except httpx.HTTPError:
        return False, "unavailable", True
    if response.is_success:
        return True, None, False
    if response.status_code in {401, 403, 404}:
        return False, "invalid_config", False
    return False, "unavailable", response.status_code in {408, 429} or response.status_code >= 500


async def _dispatch(
    db: AsyncSession, delivery: AlertDelivery, notification: Notification
) -> tuple[bool, str | None, bool]:
    if delivery.channel == "email":
        return await _dispatch_email(db, notification)
    if delivery.channel == "telegram":
        return await _dispatch_telegram(notification)
    return False, "invalid_config", False


async def process_alert_delivery(db: AsyncSession, delivery_id: UUID) -> str:
    now = _now()
    delivery = (
        await db.execute(
            select(AlertDelivery)
            .where(AlertDelivery.id == delivery_id)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if delivery is None:
        return "skipped"
    if delivery.status in {"delivered", "dead_letter"}:
        return delivery.status
    if delivery.lease_expires_at and delivery.lease_expires_at > now:
        return "locked"
    if delivery.next_attempt_at and delivery.next_attempt_at > now:
        return "deferred"

    notification = await db.get(Notification, delivery.notification_id)
    if notification is None:
        delivery.status = "dead_letter"
        delivery.error_code = "missing_notification"
        await db.commit()
        return delivery.status

    delivery.attempt_count += 1
    delivery.status = "processing"
    delivery.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    attempt = AlertDeliveryAttempt(
        tenant_id=delivery.tenant_id,
        delivery_id=delivery.id,
        sequence=delivery.attempt_count,
        status="processing",
    )
    db.add(attempt)
    await db.commit()

    ok, error_code, retryable = await _dispatch(db, delivery, notification)
    delivery = (
        await db.execute(
            select(AlertDelivery).where(AlertDelivery.id == delivery_id).with_for_update()
        )
    ).scalar_one()
    attempt = (
        await db.execute(
            select(AlertDeliveryAttempt).where(
                AlertDeliveryAttempt.delivery_id == delivery_id,
                AlertDeliveryAttempt.sequence == delivery.attempt_count,
            )
        )
    ).scalar_one()
    attempt.finished_at = _now()
    delivery.lease_expires_at = None
    delivery.error_code = error_code
    if ok:
        delivery.status = "delivered"
        delivery.delivered_at = _now()
        attempt.status = "delivered"
    elif retryable and delivery.attempt_count < MAX_ATTEMPTS:
        delivery.status = "retrying"
        delivery.next_attempt_at = _now() + timedelta(
            seconds=BACKOFF_SECONDS[delivery.attempt_count - 1]
        )
        attempt.status = "retrying"
        attempt.error_code = error_code
    else:
        delivery.status = "dead_letter"
        attempt.status = "dead_letter"
        attempt.error_code = error_code
    await db.commit()
    return delivery.status


async def recover_expired_alert_leases(db: AsyncSession) -> int:
    now = _now()
    deliveries = list(
        (
            await db.execute(
                select(AlertDelivery)
                .where(AlertDelivery.status == "processing", AlertDelivery.lease_expires_at < now)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    for delivery in deliveries:
        delivery.status = "retrying"
        delivery.next_attempt_at = now
        delivery.lease_expires_at = None
        delivery.error_code = "lease_expired"
    if deliveries:
        await db.commit()
    return len(deliveries)


async def process_due_alert_deliveries(db: AsyncSession, *, limit: int = 100) -> int:
    await recover_expired_alert_leases(db)
    now = _now()
    ids = list(
        (
            await db.execute(
                select(AlertDelivery.id)
                .where(
                    AlertDelivery.status.in_(("queued", "retrying")),
                    AlertDelivery.next_attempt_at <= now,
                )
                .order_by(AlertDelivery.next_attempt_at, AlertDelivery.created_at)
                .limit(limit)
            )
        ).scalars()
    )
    completed = 0
    for delivery_id in ids:
        status = await process_alert_delivery(db, delivery_id)
        if status in {"delivered", "retrying", "dead_letter"}:
            completed += 1
    return completed
