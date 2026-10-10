from __future__ import annotations

import asyncio
import html
import smtplib
import ssl
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from uuid import UUID, uuid4

import httpx
from app.core.config import get_settings
from app.models import (
    AlertDelivery,
    AlertDeliveryAttempt,
    Notification,
    OperatorAlertRecipient,
    OperatorAlertTransport,
)
from app.services.leads import get_encryptor
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

MAX_ATTEMPTS = 5
BACKOFF_SECONDS = (60, 300, 900, 3600, 3600)
LEASE_SECONDS = 300
ALERT_CATEGORIES = frozenset({"security", "site", "system"})
SMTP_BZ_HOST = "connect.smtp.bz"
SMTP_BZ_API_ROOT = "https://api.smtp.bz/v1"
SMTP_BZ_SENDER_DOMAIN = "osco-servis.ru"
SMTP_BZ_SMTP_PORTS = {"starttls": frozenset({587, 9587}), "implicit_tls": frozenset({465, 9465})}
TRANSPORTS = frozenset({"none", "smtp_bz_smtp", "smtp_bz_api"})


def _now() -> datetime:
    return datetime.now(UTC)


def _bounded(value: str, maximum: int) -> str:
    return value[:maximum]


def _decrypt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return get_encryptor().decrypt(value)
    except Exception:  # noqa: BLE001
        return None


def _transport_ready(transport: OperatorAlertTransport | None) -> bool:
    if transport is None or transport.transport not in TRANSPORTS:
        return False
    if transport.transport == "smtp_bz_smtp":
        return bool(
            transport.sender_email
            and transport.smtp_port in SMTP_BZ_SMTP_PORTS.get(transport.smtp_tls_mode or "", ())
            and transport.smtp_username_enc
            and transport.smtp_password_enc
        )
    if transport.transport == "smtp_bz_api":
        return bool(transport.sender_email and transport.api_authorization_enc)
    return False


async def _transport_for_tenant(
    db: AsyncSession, tenant_id: UUID, *, lock: bool = False
) -> OperatorAlertTransport | None:
    statement = select(OperatorAlertTransport).where(OperatorAlertTransport.tenant_id == tenant_id)
    if lock:
        statement = statement.with_for_update()
    return (await db.execute(statement)).scalar_one_or_none()


def _transport_status(
    transport: OperatorAlertTransport | None,
) -> dict[str, str | bool | int | None]:
    return {
        "selected": transport.transport if transport else "none",
        "configured": _transport_ready(transport),
        "smtp_configured": bool(
            transport
            and transport.sender_email
            and transport.smtp_username_enc
            and transport.smtp_password_enc
        ),
        "api_configured": bool(
            transport and transport.sender_email and transport.api_authorization_enc
        ),
        "sender_email": mask_email(transport.sender_email)
        if transport and transport.sender_email
        else None,
        "revision": transport.revision if transport else None,
        "updated_at": transport.updated_at.isoformat()
        if transport and transport.updated_at
        else None,
    }


async def alert_transport_status(
    db: AsyncSession, tenant_id: UUID
) -> dict[str, str | bool | int | None]:
    return _transport_status(await _transport_for_tenant(db, tenant_id))


async def alert_channel_status(
    db: AsyncSession, tenant_id: UUID
) -> dict[str, str | bool | int | None]:
    settings = get_settings()
    transport = await _transport_for_tenant(db, tenant_id)
    return {
        "enabled": settings.operator_alerts_enabled,
        "email": _transport_ready(transport),
        "telegram": settings.telegram_configured,
        "selected_transport": transport.transport if transport else "none",
    }


def _validate_smtp_bz_sender(sender_email: str) -> str:
    normalized = sender_email.strip().lower()
    if normalized.rsplit("@", 1)[-1] != SMTP_BZ_SENDER_DOMAIN:
        raise ValueError("SMTP.bz sender must use the verified osco-servis.ru domain")
    return normalized


async def configure_smtp_bz_smtp(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    sender_email: str,
    port: int,
    tls_mode: str,
    username: str,
    password: str,
    activate: bool,
) -> OperatorAlertTransport:
    if tls_mode not in SMTP_BZ_SMTP_PORTS or port not in SMTP_BZ_SMTP_PORTS[tls_mode]:
        raise ValueError("Unsupported SMTP.bz TLS mode or port")
    sender_email = _validate_smtp_bz_sender(sender_email)
    if not username.strip() or not password.strip():
        raise ValueError("SMTP credentials are required")
    transport = await _transport_for_tenant(db, tenant_id, lock=True)
    if transport is None:
        transport = OperatorAlertTransport(tenant_id=tenant_id)
        db.add(transport)
    transport.sender_email = sender_email
    transport.smtp_port = port
    transport.smtp_tls_mode = tls_mode
    transport.smtp_username_enc = get_encryptor().encrypt(username.strip())
    transport.smtp_password_enc = get_encryptor().encrypt(password.strip())
    if activate:
        transport.transport = "smtp_bz_smtp"
    transport.revision += 1
    return transport


async def configure_smtp_bz_api(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    sender_email: str,
    authorization: str,
    activate: bool,
) -> OperatorAlertTransport:
    if not authorization.strip():
        raise ValueError("SMTP.bz API authorization is required")
    sender_email = _validate_smtp_bz_sender(sender_email)
    transport = await _transport_for_tenant(db, tenant_id, lock=True)
    if transport is None:
        transport = OperatorAlertTransport(tenant_id=tenant_id)
        db.add(transport)
    transport.sender_email = sender_email
    transport.api_authorization_enc = get_encryptor().encrypt(authorization.strip())
    if activate:
        transport.transport = "smtp_bz_api"
    transport.revision += 1
    return transport


async def select_alert_transport(
    db: AsyncSession, *, tenant_id: UUID, transport_name: str
) -> OperatorAlertTransport:
    if transport_name not in TRANSPORTS:
        raise ValueError("Unsupported alert transport")
    transport = await _transport_for_tenant(db, tenant_id, lock=True)
    if transport is None:
        raise ValueError("Alert transport is not configured")
    current = transport.transport
    transport.transport = transport_name
    if transport_name != "none" and not _transport_ready(transport):
        transport.transport = current
        raise ValueError("Selected alert transport is incomplete")
    if current != transport_name:
        transport.revision += 1
    return transport


def mask_email(value: str) -> str:
    local, _, domain = value.partition("@")
    if not local or not domain:
        return "настроен"
    return f"{local[:1]}***@{domain}"


async def _operator_email(db: AsyncSession, tenant_id: UUID) -> str | None:
    recipient = (
        await db.execute(
            select(OperatorAlertRecipient).where(OperatorAlertRecipient.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    return _decrypt(recipient.recipient_enc) if recipient else None


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


async def create_operator_alert(
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
    settings = get_settings()
    recipient = await _operator_email(db, tenant_id)
    transport = await _transport_for_tenant(db, tenant_id)
    if settings.operator_alerts_enabled and recipient and _transport_ready(transport):
        db.add(
            AlertDelivery(
                tenant_id=tenant_id,
                notification_id=notification.id,
                channel=transport.transport,
                status="queued",
                idempotency_key=f"{notification.id}:{transport.transport}",
                transport_revision=transport.revision,
                next_attempt_at=_now(),
            )
        )
    if settings.operator_alerts_enabled and settings.telegram_configured:
        db.add(
            AlertDelivery(
                tenant_id=tenant_id,
                notification_id=notification.id,
                channel="telegram",
                status="queued",
                idempotency_key=f"{notification.id}:telegram",
                next_attempt_at=_now(),
            )
        )
    return notification


def _smtp_client(transport: OperatorAlertTransport) -> smtplib.SMTP:
    if transport.smtp_port is None or transport.smtp_tls_mode not in SMTP_BZ_SMTP_PORTS:
        raise ValueError("SMTP transport is incomplete")
    if transport.smtp_tls_mode == "implicit_tls":
        return smtplib.SMTP_SSL(SMTP_BZ_HOST, transport.smtp_port, timeout=10)
    return smtplib.SMTP(SMTP_BZ_HOST, transport.smtp_port, timeout=10)


def _smtp_login(client: smtplib.SMTP, transport: OperatorAlertTransport) -> None:
    username = _decrypt(transport.smtp_username_enc)
    password = _decrypt(transport.smtp_password_enc)
    if not username or not password:
        raise ValueError("SMTP credentials cannot be decrypted")
    client.ehlo()
    if transport.smtp_tls_mode == "starttls":
        client.starttls(context=ssl.create_default_context())
        client.ehlo()
    client.login(username, password)


def _test_smtp(transport: OperatorAlertTransport) -> None:
    with _smtp_client(transport) as client:
        _smtp_login(client, transport)


async def test_smtp_bz_transport(db: AsyncSession, tenant_id: UUID) -> str:
    transport = await _transport_for_tenant(db, tenant_id)
    if (
        transport is None
        or not _transport_ready(transport)
        or transport.transport != "smtp_bz_smtp"
    ):
        return "invalid_config"
    try:
        await asyncio.to_thread(_test_smtp, transport)
    except smtplib.SMTPAuthenticationError:
        return "authentication_failed"
    except (ssl.SSLError, smtplib.SMTPNotSupportedError):
        return "tls_failed"
    except TimeoutError:
        return "timeout"
    except (OSError, smtplib.SMTPException):
        return "unavailable"
    return "ok"


def _send_smtp(transport: OperatorAlertTransport, *, recipient: str, title: str, body: str) -> None:
    if not transport.sender_email:
        raise ValueError("SMTP sender is missing")
    message = EmailMessage()
    message["Subject"] = title
    message["From"] = transport.sender_email
    message["To"] = recipient
    message.set_content(body)
    with _smtp_client(transport) as client:
        _smtp_login(client, transport)
        client.send_message(message, from_addr=transport.sender_email, to_addrs=[recipient])


async def _dispatch_smtp(
    db: AsyncSession, notification: Notification, transport: OperatorAlertTransport
) -> tuple[bool, str | None, bool]:
    recipient = await _operator_email(db, notification.tenant_id)
    if not recipient or not _transport_ready(transport):
        return False, "invalid_config", False
    try:
        await asyncio.to_thread(
            _send_smtp,
            transport,
            recipient=recipient,
            title=notification.title,
            body=notification.body,
        )
    except smtplib.SMTPAuthenticationError:
        return False, "authentication_failed", False
    except smtplib.SMTPSenderRefused:
        return False, "sender_rejected", False
    except smtplib.SMTPRecipientsRefused:
        return False, "recipient_rejected", False
    except (ssl.SSLError, smtplib.SMTPNotSupportedError):
        return False, "tls_failed", False
    except TimeoutError:
        return False, "timeout", True
    except (OSError, smtplib.SMTPException):
        return False, "outcome_unknown", False
    return True, None, False


def _alert_html(notification: Notification) -> str:
    title = html.escape(notification.title)
    body = html.escape(notification.body).replace("\n", "<br>")
    return f"<h1>{title}</h1><p>{body}</p>"


async def test_smtp_bz_api_transport(db: AsyncSession, tenant_id: UUID) -> str:
    transport = await _transport_for_tenant(db, tenant_id)
    authorization = _decrypt(transport.api_authorization_enc) if transport else None
    if transport is None or not _transport_ready(transport) or not authorization:
        return "invalid_config"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.get(
                f"{SMTP_BZ_API_ROOT}/user", headers={"Authorization": authorization}
            )
    except httpx.TimeoutException:
        return "timeout"
    except httpx.HTTPError:
        return "unavailable"
    if response.status_code == 200:
        return "ok"
    if response.status_code in {401, 403}:
        return "authentication_failed"
    if response.status_code in {400, 404}:
        return "invalid_config"
    return "unavailable"


async def _dispatch_smtp_bz_api(
    db: AsyncSession, notification: Notification, transport: OperatorAlertTransport
) -> tuple[bool, str | None, bool]:
    recipient = await _operator_email(db, notification.tenant_id)
    authorization = _decrypt(transport.api_authorization_enc)
    if not recipient or not transport.sender_email or not authorization:
        return False, "invalid_config", False
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.post(
                f"{SMTP_BZ_API_ROOT}/smtp/send",
                headers={"Authorization": authorization},
                data={
                    "from": transport.sender_email,
                    "to": recipient,
                    "subject": notification.title,
                    "html": _alert_html(notification),
                    "text": notification.body,
                },
            )
    except httpx.TimeoutException:
        return False, "outcome_unknown", False
    except httpx.HTTPError:
        return False, "unavailable", True
    if response.is_success:
        return True, None, False
    if response.status_code in {401, 403}:
        return False, "authentication_failed", False
    if response.status_code in {400, 404, 422}:
        return False, "api_rejected", False
    if response.status_code in {408, 429}:
        return False, "unavailable", True
    return False, "outcome_unknown", False


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
    if delivery.channel == "telegram":
        return await _dispatch_telegram(notification)
    if delivery.channel not in {"smtp_bz_smtp", "smtp_bz_api"}:
        return False, "legacy_transport_retired", False
    transport = await _transport_for_tenant(db, delivery.tenant_id)
    if (
        transport is None
        or transport.transport != delivery.channel
        or transport.revision != delivery.transport_revision
    ):
        return False, "transport_changed", False
    if delivery.channel == "smtp_bz_smtp":
        return await _dispatch_smtp(db, notification, transport)
    return await _dispatch_smtp_bz_api(db, notification, transport)


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
