from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.services import webhook_delivery
from app.services.leads import canonical_webhook_body, sign_webhook
from app.services.webhook_delivery import _retryable, create_lead_delivery
from app.worker import WorkerSettings


class FakeDatabase:
    def __init__(self):
        self.added: list[object] = []

    def add(self, value: object) -> None:
        self.added.append(value)


def test_webhook_signature_matches_the_wire_body():
    payload = {"z": "тест", "a": 1}

    assert canonical_webhook_body(payload) == b'{"a":1,"z":"\xd1\x82\xd0\xb5\xd1\x81\xd1\x82"}'
    assert sign_webhook(payload, "secret") == sign_webhook(payload, "secret")


def test_delivery_retry_policy_distinguishes_permanent_failures():
    assert _retryable({"ok": False, "error": "timeout"}) is True
    assert _retryable({"ok": False, "status": 408}) is True
    assert _retryable({"ok": False, "status": 429}) is True
    assert _retryable({"ok": False, "status": 500}) is True
    assert _retryable({"ok": False, "status": 400}) is False


def test_worker_registers_durable_delivery_tasks():
    names = {task.__name__ for task in WorkerSettings.functions}

    assert {"webhook_delivery_task", "webhook_delivery_sweep_task"} <= names


def test_delivery_reuses_encrypted_site_secret():
    lead = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        page_slug="/",
        qualification="new",
        idempotency_key="lead-1",
        crm_status=None,
    )
    site = SimpleNamespace(
        id=uuid4(),
        domain="example.test",
        manifest={
            "contacts": {
                "webhook_url": "https://hooks.example.test/lead",
                "webhook_secret_enc": "already-encrypted",
            }
        },
    )
    db = FakeDatabase()

    delivery = asyncio.run(create_lead_delivery(db, lead=lead, site=site))

    assert delivery is db.added[0]
    assert delivery.target_secret_enc == "already-encrypted"
    assert delivery.status == "queued"


def test_delivery_handles_an_undecryptable_secret(monkeypatch):
    class Encryptor:
        def decrypt(self, _: str) -> str:
            raise ValueError("bad ciphertext")

    monkeypatch.setattr(webhook_delivery, "get_encryptor", lambda: Encryptor())

    assert webhook_delivery._decrypt_secret("corrupted") is None
    assert not webhook_delivery._retryable({"error": "Webhook secret cannot be decrypted"})
