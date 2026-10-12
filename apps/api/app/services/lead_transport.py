from __future__ import annotations

import asyncio
import re
import smtplib
import ssl
from email.message import EmailMessage
from uuid import UUID

from app.models.leads import CentralLeadSmtpTransport, WebhookDelivery
from app.services.leads import get_encryptor
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_TLS_MODES = frozenset({"starttls", "implicit_tls"})


def _decrypt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return get_encryptor().decrypt(value)
    except Exception:  # noqa: BLE001
        return None


def _validate_host(value: str) -> str:
    normalized = value.strip().lower().rstrip(".")
    if not _HOST.fullmatch(normalized) or ".." in normalized:
        raise ValueError("SMTP host is invalid")
    return normalized


def _mask_email(value: str) -> str:
    local, _, domain = value.partition("@")
    return f"{local[:1]}***@{domain}" if local and domain else "настроен"


def _transport_ready(transport: CentralLeadSmtpTransport | None) -> bool:
    return bool(
        transport
        and transport.enabled
        and transport.host
        and transport.port
        and transport.tls_mode in _TLS_MODES
        and transport.sender_email
        and transport.recipient_enc
        and bool(transport.smtp_username_enc) == bool(transport.smtp_password_enc)
    )


async def lead_transport_for_tenant(
    db: AsyncSession, tenant_id: UUID, *, lock: bool = False
) -> CentralLeadSmtpTransport | None:
    statement = select(CentralLeadSmtpTransport).where(
        CentralLeadSmtpTransport.tenant_id == tenant_id
    )
    if lock:
        statement = statement.with_for_update()
    return (await db.execute(statement)).scalar_one_or_none()


async def central_lead_transport_ready(db: AsyncSession, tenant_id: UUID) -> bool:
    transport = await lead_transport_for_tenant(db, tenant_id)
    return _transport_ready(transport) and _decrypt(transport.recipient_enc) is not None


async def lead_transport_status(db: AsyncSession, tenant_id: UUID) -> dict:
    transport = await lead_transport_for_tenant(db, tenant_id)
    recipient = _decrypt(transport.recipient_enc) if transport else None
    return {
        "configured": _transport_ready(transport) and recipient is not None,
        "enabled": bool(transport and transport.enabled),
        "sender": _mask_email(transport.sender_email) if transport else None,
        "recipient": _mask_email(recipient) if recipient else None,
        "host": transport.host if transport else None,
        "port": transport.port if transport else None,
        "tls_mode": transport.tls_mode if transport else None,
        "revision": transport.revision if transport else None,
        "updated_at": (
            transport.updated_at.isoformat() if transport and transport.updated_at else None
        ),
    }


async def configure_lead_transport(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    host: str,
    port: int,
    tls_mode: str,
    sender_email: str,
    recipient: str,
    username: str | None,
    password: str | None,
) -> CentralLeadSmtpTransport:
    if tls_mode not in _TLS_MODES:
        raise ValueError("Unsupported SMTP TLS mode")
    if not 1 <= port <= 65535:
        raise ValueError("SMTP port is invalid")
    if bool(username and username.strip()) != bool(password and password.strip()):
        raise ValueError("SMTP username and password must be configured together")
    transport = await lead_transport_for_tenant(db, tenant_id, lock=True)
    if transport is None:
        transport = CentralLeadSmtpTransport(
            tenant_id=tenant_id,
            host="pending",
            port=1,
            tls_mode="starttls",
            sender_email="pending@example.invalid",
            recipient_enc=get_encryptor().encrypt("pending@example.invalid"),
        )
        db.add(transport)
    transport.host = _validate_host(host)
    transport.port = port
    transport.tls_mode = tls_mode
    transport.sender_email = sender_email.strip().lower()
    transport.recipient_enc = get_encryptor().encrypt(recipient.strip().lower())
    transport.smtp_username_enc = (
        get_encryptor().encrypt(username.strip()) if username and username.strip() else None
    )
    transport.smtp_password_enc = (
        get_encryptor().encrypt(password.strip()) if password and password.strip() else None
    )
    transport.enabled = True
    transport.revision = (transport.revision or 0) + 1
    return transport


async def disable_lead_transport(
    db: AsyncSession, tenant_id: UUID
) -> CentralLeadSmtpTransport | None:
    transport = await lead_transport_for_tenant(db, tenant_id, lock=True)
    if transport is None:
        return None
    if transport.enabled:
        transport.enabled = False
        transport.revision += 1
    return transport


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


def _connect(transport: CentralLeadSmtpTransport) -> smtplib.SMTP:
    client_class = smtplib.SMTP_SSL if transport.tls_mode == "implicit_tls" else smtplib.SMTP
    return client_class(transport.host, transport.port, timeout=10)


def _authenticate(client: smtplib.SMTP, transport: CentralLeadSmtpTransport) -> None:
    client.ehlo()
    if transport.tls_mode == "starttls":
        client.starttls(context=ssl.create_default_context())
        client.ehlo()
    if transport.smtp_username_enc:
        username = _decrypt(transport.smtp_username_enc)
        password = _decrypt(transport.smtp_password_enc)
        if not username or not password:
            raise ValueError("SMTP credentials cannot be decrypted")
        client.login(username, password)


def _test_connection(transport: CentralLeadSmtpTransport) -> None:
    with _connect(transport) as client:
        _authenticate(client, transport)


async def test_lead_transport(db: AsyncSession, tenant_id: UUID) -> str:
    transport = await lead_transport_for_tenant(db, tenant_id)
    if not _transport_ready(transport) or not _decrypt(transport.recipient_enc):
        return "invalid_config"
    try:
        await asyncio.to_thread(_test_connection, transport)
    except Exception:  # SMTP details can contain sensitive infrastructure context.
        return "connection_failed"
    return "connected"


async def bind_delivery_transport(
    db: AsyncSession, delivery: WebhookDelivery, *, rebind_recipient: bool = False
) -> bool:
    transport = await lead_transport_for_tenant(db, delivery.tenant_id)
    if not _transport_ready(transport):
        return False
    recipient = _decrypt(transport.recipient_enc)
    if not recipient:
        return False
    if rebind_recipient:
        delivery.target_recipient_enc = get_encryptor().encrypt(recipient)
    delivery.lead_transport_revision = transport.revision
    return True


async def dispatch_lead_smtp(
    db: AsyncSession, *, delivery: WebhookDelivery, recipient: str, payload: dict
) -> dict:
    transport = await lead_transport_for_tenant(db, delivery.tenant_id)
    if not _transport_ready(transport) or not _decrypt(transport.recipient_enc):
        return {"ok": False, "error": "Lead SMTP transport is not configured", "retryable": False}
    if delivery.lead_transport_revision != transport.revision:
        return {"ok": False, "error": "Lead SMTP transport was reconfigured", "retryable": False}
    try:
        message = _smtp_message(payload, transport.sender_email, recipient)

        def send() -> None:
            with _connect(transport) as client:
                _authenticate(client, transport)
                client.send_message(
                    message,
                    from_addr=transport.sender_email,
                    to_addrs=[recipient],
                )

        await asyncio.to_thread(send)
    except ValueError:
        return {
            "ok": False,
            "error": "Lead SMTP credentials cannot be decrypted",
            "retryable": False,
        }
    except Exception:  # SMTP details can contain sensitive infrastructure context.
        return {"ok": False, "error": "Lead SMTP delivery failed", "retryable": True}
    return {"ok": True}
