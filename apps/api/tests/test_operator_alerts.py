from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.services import operator_alerts, site_integrity, site_monitor


class FakeDatabase:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, value: object) -> None:
        self.added.append(value)


def test_operator_alert_creates_only_configured_durable_channels(monkeypatch):
    monkeypatch.setattr(
        operator_alerts,
        "get_settings",
        lambda: SimpleNamespace(
            operator_alerts_enabled=True,
            smtp_configured=True,
            telegram_configured=True,
        ),
    )
    database = FakeDatabase()
    tenant_id = uuid4()

    notification = operator_alerts.create_operator_alert(
        database,
        tenant_id=tenant_id,
        category="security",
        signal_code="security-login",
        title="Вход",
        body="Проверка",
        subject_kind="user",
        subject_key=str(uuid4()),
    )

    assert notification.tenant_id == tenant_id
    assert notification.category == "security"
    deliveries = database.added[1:]
    assert {delivery.channel for delivery in deliveries} == {"email", "telegram"}
    assert {delivery.notification_id for delivery in deliveries} == {notification.id}
    assert all(delivery.idempotency_key.endswith(f":{delivery.channel}") for delivery in deliveries)


def test_operator_alerts_never_queue_channels_when_disabled(monkeypatch):
    monkeypatch.setattr(
        operator_alerts,
        "get_settings",
        lambda: SimpleNamespace(
            operator_alerts_enabled=False,
            smtp_configured=True,
            telegram_configured=True,
        ),
    )
    database = FakeDatabase()

    operator_alerts.create_operator_alert(
        database,
        tenant_id=uuid4(),
        category="system",
        signal_code="operator-alert-test",
        title="Тест",
        body="Тест",
    )

    assert len(database.added) == 1


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
