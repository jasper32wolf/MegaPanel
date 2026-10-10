from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1.panel import _serialize_notification
from app.core.config import Settings
from app.models import AlertDelivery, OperatorAlertTransport
from app.services import operator_alerts, site_integrity, site_monitor


class FakeDatabase:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, value: object) -> None:
        self.added.append(value)


def test_operator_alert_creates_only_selected_durable_email_channel(monkeypatch):
    monkeypatch.setattr(
        operator_alerts,
        "get_settings",
        lambda: SimpleNamespace(operator_alerts_enabled=True, telegram_configured=True),
    )
    transport = OperatorAlertTransport(
        transport="smtp_bz_smtp",
        revision=3,
        sender_email="alerts@osco-servis.ru",
        smtp_port=587,
        smtp_tls_mode="starttls",
        smtp_username_enc="encrypted-login",
        smtp_password_enc="encrypted-password",
    )

    async def recipient(*_args):
        return "operator@example.test"

    async def configured_transport(*_args, **_kwargs):
        return transport

    monkeypatch.setattr(operator_alerts, "_operator_email", recipient)
    monkeypatch.setattr(operator_alerts, "_transport_for_tenant", configured_transport)
    database = FakeDatabase()
    tenant_id = uuid4()

    notification = asyncio.run(
        operator_alerts.create_operator_alert(
            database,
            tenant_id=tenant_id,
            category="security",
            signal_code="security-login",
            title="Вход",
            body="Проверка",
            subject_kind="user",
            subject_key=str(uuid4()),
        )
    )

    assert notification.tenant_id == tenant_id
    assert notification.category == "security"
    deliveries = database.added[1:]
    assert {delivery.channel for delivery in deliveries} == {"smtp_bz_smtp", "telegram"}
    smtp_delivery = next(delivery for delivery in deliveries if delivery.channel == "smtp_bz_smtp")
    assert smtp_delivery.transport_revision == 3
    assert {delivery.notification_id for delivery in deliveries} == {notification.id}
    assert all(delivery.idempotency_key.endswith(f":{delivery.channel}") for delivery in deliveries)


def test_operator_alerts_never_queue_channels_when_disabled(monkeypatch):
    monkeypatch.setattr(
        operator_alerts,
        "get_settings",
        lambda: SimpleNamespace(operator_alerts_enabled=False, telegram_configured=True),
    )

    async def no_recipient(*_args):
        return None

    async def no_transport(*_args, **_kwargs):
        return None

    monkeypatch.setattr(operator_alerts, "_operator_email", no_recipient)
    monkeypatch.setattr(operator_alerts, "_transport_for_tenant", no_transport)
    database = FakeDatabase()

    asyncio.run(
        operator_alerts.create_operator_alert(
            database,
            tenant_id=uuid4(),
            category="system",
            signal_code="operator-alert-test",
            title="Тест",
            body="Тест",
        )
    )

    assert len(database.added) == 1


def test_alert_transport_status_never_discloses_credentials():
    transport = OperatorAlertTransport(
        transport="smtp_bz_smtp",
        revision=1,
        sender_email="alerts@osco-servis.ru",
        smtp_port=587,
        smtp_tls_mode="starttls",
        smtp_username_enc="encrypted-login-value",
        smtp_password_enc="encrypted-password-value",
        api_authorization_enc="encrypted-api-authorization",
    )

    status = operator_alerts._transport_status(transport)

    assert status["configured"] is True
    assert status["sender_email"] == "a***@osco-servis.ru"
    assert "encrypted-login-value" not in str(status)
    assert "encrypted-password-value" not in str(status)
    assert "encrypted-api-authorization" not in str(status)


def test_smtp_bz_sender_is_limited_to_the_verified_domain():
    with pytest.raises(ValueError, match="verified osco-servis.ru domain"):
        operator_alerts._validate_smtp_bz_sender("alerts@example.test")


def test_first_smtp_configuration_is_encrypted_and_has_a_revision(monkeypatch):
    class Encryptor:
        @staticmethod
        def encrypt(value: str) -> str:
            return f"ciphertext:{value}"

    async def no_transport(*_args, **_kwargs):
        return None

    database = FakeDatabase()
    monkeypatch.setattr(operator_alerts, "get_encryptor", lambda: Encryptor())
    monkeypatch.setattr(operator_alerts, "_transport_for_tenant", no_transport)

    configured = asyncio.run(
        operator_alerts.configure_smtp_bz_smtp(
            database,
            tenant_id=uuid4(),
            sender_email="alerts@osco-servis.ru",
            port=587,
            tls_mode="starttls",
            username="smtp-login",
            password="smtp-password",
            activate=True,
        )
    )

    assert database.added == [configured]
    assert configured.transport == "smtp_bz_smtp"
    assert configured.revision == 1
    assert configured.smtp_username_enc == "ciphertext:smtp-login"
    assert configured.smtp_password_enc == "ciphertext:smtp-password"
    assert "smtp-password" not in str(operator_alerts._transport_status(configured))


def test_first_api_configuration_has_a_revision(monkeypatch):
    class Encryptor:
        @staticmethod
        def encrypt(value: str) -> str:
            return f"ciphertext:{value}"

    async def no_transport(*_args, **_kwargs):
        return None

    monkeypatch.setattr(operator_alerts, "get_encryptor", lambda: Encryptor())
    monkeypatch.setattr(operator_alerts, "_transport_for_tenant", no_transport)

    configured = asyncio.run(
        operator_alerts.configure_smtp_bz_api(
            FakeDatabase(),
            tenant_id=uuid4(),
            sender_email="alerts@osco-servis.ru",
            authorization="API Authorization",
            activate=False,
        )
    )

    assert configured.revision == 1
    assert configured.api_authorization_enc == "ciphertext:API Authorization"
    assert "API Authorization" not in str(operator_alerts._transport_status(configured))


def test_changed_transport_terminalizes_an_old_delivery(monkeypatch):
    delivery = AlertDelivery(
        tenant_id=uuid4(),
        notification_id=uuid4(),
        channel="smtp_bz_smtp",
        idempotency_key="old-delivery",
        transport_revision=1,
    )
    transport = OperatorAlertTransport(transport="smtp_bz_smtp", revision=2)

    async def current_transport(*_args, **_kwargs):
        return transport

    monkeypatch.setattr(operator_alerts, "_transport_for_tenant", current_transport)

    outcome = asyncio.run(operator_alerts._dispatch(FakeDatabase(), delivery, SimpleNamespace()))

    assert outcome == (False, "transport_changed", False)


def test_smtp_bz_api_uses_fixed_endpoint_and_exact_authorization(monkeypatch):
    transport = OperatorAlertTransport(
        transport="smtp_bz_api",
        revision=1,
        sender_email="alerts@osco-servis.ru",
        api_authorization_enc="encrypted-authorization",
    )
    captured: dict[str, object] = {}

    async def recipient(*_args):
        return "operator@example.test"

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return SimpleNamespace(is_success=True)

    monkeypatch.setattr(operator_alerts, "_operator_email", recipient)
    monkeypatch.setattr(operator_alerts, "_decrypt", lambda _value: "Raw SMTP.bz value")
    monkeypatch.setattr(operator_alerts.httpx, "AsyncClient", lambda **_kwargs: Client())

    outcome = asyncio.run(
        operator_alerts._dispatch_smtp_bz_api(
            FakeDatabase(),
            SimpleNamespace(tenant_id=uuid4(), title="Тест", body="Тело"),
            transport,
        )
    )

    assert outcome == (True, None, False)
    assert captured["url"] == "https://api.smtp.bz/v1/smtp/send"
    assert captured["headers"] == {"Authorization": "Raw SMTP.bz value"}
    assert captured["data"] == {
        "from": "alerts@osco-servis.ru",
        "to": "operator@example.test",
        "subject": "Тест",
        "html": "<h1>Тест</h1><p>Тело</p>",
        "text": "Тело",
    }


def test_api_timeout_is_not_retried_after_an_ambiguous_send(monkeypatch):
    transport = OperatorAlertTransport(
        transport="smtp_bz_api",
        revision=1,
        sender_email="alerts@osco-servis.ru",
        api_authorization_enc="encrypted-authorization",
    )

    async def recipient(*_args):
        return "operator@example.test"

    class FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            raise operator_alerts.httpx.TimeoutException("timeout")

    monkeypatch.setattr(operator_alerts, "_operator_email", recipient)
    monkeypatch.setattr(operator_alerts, "_decrypt", lambda _value: "authorization")
    monkeypatch.setattr(operator_alerts.httpx, "AsyncClient", lambda **_kwargs: FailingClient())

    outcome = asyncio.run(
        operator_alerts._dispatch_smtp_bz_api(
            FakeDatabase(),
            SimpleNamespace(tenant_id=uuid4(), title="Тест", body="Тело"),
            transport,
        )
    )

    assert outcome == (False, "outcome_unknown", False)


def test_operator_alert_recipient_is_masked_without_disclosure():
    assert operator_alerts.mask_email("alerts@example.test") == "a***@example.test"
    assert operator_alerts.mask_email("invalid") == "настроен"


def test_alert_projection_exposes_only_safe_delivery_error_code():
    notification = SimpleNamespace(
        id=uuid4(),
        category="security",
        signal_code="security-login",
        subject_kind="user",
        subject_key="operator",
        priority="high",
        title="Вход",
        body="Проверка",
        read=False,
        created_at=None,
    )
    delivery = SimpleNamespace(
        channel="smtp_bz_smtp",
        status="dead_letter",
        error_code="authentication_failed",
    )

    payload = _serialize_notification(notification, [delivery])

    assert payload["deliveries"] == {"smtp_bz_smtp": "dead_letter"}
    assert payload["delivery_errors"] == {"smtp_bz_smtp": "authentication_failed"}
    assert "password" not in str(payload).lower()


def test_telegram_credentials_must_be_configured_together():
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        Settings(telegram_bot_token="token")


def test_site_monitor_rejects_private_resolution(monkeypatch):
    monkeypatch.setattr(
        site_monitor.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),
        ],
    )

    assert site_monitor._global_addresses("example.test") == []
    probe = asyncio.run(site_monitor.probe_published_domain("example.test"))
    assert probe.signal_code == "site-dns-failure"


def test_release_hash_changes_when_a_published_file_changes(tmp_path):
    release = tmp_path / "release"
    release.mkdir()
    artifact = release / "index.html"
    artifact.write_text("approved", encoding="utf-8")
    before = site_integrity._release_hash(release)

    artifact.write_text("changed outside the approved release", encoding="utf-8")

    assert before is not None
    assert site_integrity._release_hash(release) != before


def test_worker_registers_alert_and_site_monitor_sweeps():
    from app import worker

    assert worker.operator_alert_delivery_sweep_task in worker.WorkerSettings.functions
    assert worker.site_monitor_sweep_task in worker.WorkerSettings.functions
    assert worker.site_integrity_sweep_task in worker.WorkerSettings.functions
