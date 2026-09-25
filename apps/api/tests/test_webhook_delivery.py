from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.core.config import get_settings
from app.db.session import open_db_session
from app.models import Site, Tenant
from app.models.leads import Lead, WebhookDelivery, WebhookDeliveryAttempt
from app.services import webhook_delivery
from app.services.leads import canonical_webhook_body, get_encryptor, sign_webhook
from app.services.webhook_delivery import _retryable, create_lead_delivery, enqueue_delivery
from app.worker import WorkerSettings, webhook_delivery_task
from sqlalchemy import delete, select

RUN_WEBHOOK_DELIVERY_INTEGRATION = os.getenv("WEBHOOK_DELIVERY_INTEGRATION") == "1"


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


@pytest.mark.skipif(
    not RUN_WEBHOOK_DELIVERY_INTEGRATION,
    reason="requires PostgreSQL and isolated Redis service containers",
)
def test_worker_delivery_uses_postgres_redis_and_no_external_webhook(monkeypatch):
    async def run() -> None:
        from redis.asyncio import from_url

        redis = from_url(get_settings().redis_url)
        tenant_id = uuid4()
        secret = "integration-webhook-secret"
        calls: list[tuple[str, dict, str]] = []
        dispatch_response = {"ok": True, "status": 202}

        async def fake_dispatch(url: str, payload: dict, dispatch_secret: str) -> dict:
            calls.append((url, payload, dispatch_secret))
            return dict(dispatch_response)

        monkeypatch.setattr(webhook_delivery, "dispatch_webhook", fake_dispatch)
        await redis.flushdb()
        try:
            async with open_db_session() as db:
                tenant = Tenant(
                    id=tenant_id,
                    name="Webhook integration",
                    slug=f"webhook-{tenant_id.hex}",
                    branding={},
                    quotas={},
                )
                site = Site(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    domain=f"webhook-{tenant_id.hex}.example.test",
                    manifest={
                        "contacts": {
                            "webhook_url": "https://receiver.example.test/leads",
                            "webhook_secret_enc": get_encryptor().encrypt(secret),
                        }
                    },
                )
                lead = Lead(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    site_id=site.id,
                    page_slug="/",
                    qualification="qualified",
                    idempotency_key=f"lead-{tenant_id.hex}",
                )
                db.add(tenant)
                db.add(site)
                db.add(lead)
                await db.flush()
                delivery = await create_lead_delivery(db, lead=lead, site=site)
                assert delivery is not None
                delivery_id = delivery.id
                expected_payload = dict(delivery.payload)
                await db.commit()

            assert await enqueue_delivery(delivery_id)
            result = await webhook_delivery_task({}, str(delivery_id))
            assert result["status"] == "delivered"

            async with open_db_session() as db:
                delivery = await db.get(WebhookDelivery, delivery_id)
                refreshed_lead = await db.get(Lead, lead.id)
                attempts = list(
                    (
                        await db.execute(
                            select(WebhookDeliveryAttempt).where(
                                WebhookDeliveryAttempt.delivery_id == delivery_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert delivery is not None
                assert refreshed_lead is not None
                assert delivery.status == "delivered"
                assert delivery.attempt_count == 1
                assert delivery.delivered_at is not None
                assert delivery.next_attempt_at is None
                assert delivery.locked_until is None
                assert delivery.last_http_status == 202
                assert refreshed_lead.crm_status == "sent"
                assert len(attempts) == 1
                assert attempts[0].sequence == 1
                assert attempts[0].trigger == "automatic"
                assert attempts[0].status == "delivered"
                assert attempts[0].finished_at is not None
                assert calls == [("https://receiver.example.test/leads", expected_payload, secret)]

            dispatch_response.clear()
            dispatch_response.update({"ok": False, "status": 500})
            async with open_db_session() as db:
                retry_lead = Lead(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    site_id=site.id,
                    page_slug="/retry",
                    qualification="qualified",
                    idempotency_key=f"retry-{tenant_id.hex}",
                )
                db.add(retry_lead)
                await db.flush()
                retry_delivery = await create_lead_delivery(db, lead=retry_lead, site=site)
                assert retry_delivery is not None
                retry_delivery_id = retry_delivery.id
                await db.commit()

            assert (await webhook_delivery_task({}, str(retry_delivery_id)))["status"] == "retrying"
            for _ in range(retry_delivery.max_attempts - 1):
                assert (await webhook_delivery_task({}, str(retry_delivery_id), "manual"))[
                    "status"
                ] in {"retrying", "dead_letter"}

            async with open_db_session() as db:
                retry_delivery = await db.get(WebhookDelivery, retry_delivery_id)
                retry_lead = await db.get(Lead, retry_lead.id)
                retry_attempts = list(
                    (
                        await db.execute(
                            select(WebhookDeliveryAttempt)
                            .where(WebhookDeliveryAttempt.delivery_id == retry_delivery_id)
                            .order_by(WebhookDeliveryAttempt.sequence)
                        )
                    )
                    .scalars()
                    .all()
                )
                assert retry_delivery is not None
                assert retry_lead is not None
                assert retry_delivery.status == "dead_letter"
                assert retry_delivery.attempt_count == retry_delivery.max_attempts
                assert retry_delivery.dead_lettered_at is not None
                assert retry_delivery.next_attempt_at is None
                assert retry_delivery.locked_until is None
                assert retry_delivery.last_http_status == 500
                assert retry_lead.crm_status == "dead_letter"
                assert len(retry_attempts) == retry_delivery.max_attempts
                assert [attempt.sequence for attempt in retry_attempts] == list(
                    range(1, retry_delivery.max_attempts + 1)
                )
                assert retry_attempts[0].trigger == "automatic"
                assert all(attempt.status == "retrying" for attempt in retry_attempts[:-1])
                assert retry_attempts[-1].status == "dead_letter"
        finally:
            try:
                async with open_db_session() as db:
                    await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
                    await db.commit()
            finally:
                await redis.flushdb()
                await redis.aclose()

    asyncio.run(run())
