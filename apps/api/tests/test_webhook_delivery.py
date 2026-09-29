from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from app.core.config import get_settings
from app.db.session import engine, open_db_session
from app.main import app
from app.models import Site, Tenant
from app.models.leads import Consent, Lead, WebhookDelivery, WebhookDeliveryAttempt
from app.services import webhook_delivery
from app.services.leads import canonical_webhook_body, get_encryptor, sign_webhook
from app.services.webhook_delivery import (
    _retryable,
    create_lead_delivery,
    enqueue_delivery,
    resend_delivery,
    wire_payload,
)
from app.worker import WorkerSettings, webhook_delivery_task, worker_heartbeat_task
from arq.constants import default_queue_name
from arq.worker import Worker
from httpx import ASGITransport, AsyncClient
from site_panel_blocks import instantiate_blocks, load_kit
from site_panel_shared.manifests import BlockDef, PageManifest, SiteManifest
from site_panel_ssg import SiteBuilder
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

    assert {
        "worker_heartbeat_task",
        "webhook_delivery_task",
        "webhook_delivery_sweep_task",
    } <= names
    heartbeat_job = next(
        job for job in WorkerSettings.cron_jobs if job.coroutine is worker_heartbeat_task
    )
    assert heartbeat_job.run_at_startup is True
    assert heartbeat_job.second == set(range(0, 60, 30))


def test_worker_heartbeat_uses_persisted_database_signal(monkeypatch):
    session = object()
    recorded: list[object] = []

    @asynccontextmanager
    async def fake_open_db_session():
        yield session

    async def fake_record(value: object) -> None:
        recorded.append(value)

    monkeypatch.setattr("app.worker.open_db_session", fake_open_db_session)
    monkeypatch.setattr("app.worker.record_worker_heartbeat", fake_record)

    assert asyncio.run(worker_heartbeat_task({})) == {"ok": True}
    assert recorded == [session]


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


def test_delivery_rejects_plaintext_legacy_secret():
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
                "webhook_secret": "legacy-plaintext",
            }
        },
    )
    db = FakeDatabase()

    delivery = asyncio.run(create_lead_delivery(db, lead=lead, site=site))

    assert delivery is db.added[0]
    assert delivery.target_secret_enc is None
    assert delivery.status == "dead_letter"
    assert delivery.last_error == "Webhook secret is not configured"


def test_wire_payload_decrypts_pii_without_mutating_delivery_payload():
    encryptor = get_encryptor()
    delivery = SimpleNamespace(payload={"lead_id": str(uuid4())})
    lead = SimpleNamespace(
        phone_enc=encryptor.encrypt("+79991234567"),
        email_enc=encryptor.encrypt("lead@example.test"),
        name_enc=encryptor.encrypt("Тест"),
        message_enc=encryptor.encrypt("Нужна консультация"),
    )

    payload = wire_payload(delivery, lead)

    assert delivery.payload == {"lead_id": delivery.payload["lead_id"]}
    assert payload == {
        "lead_id": delivery.payload["lead_id"],
        "phone": "+79991234567",
        "email": "lead@example.test",
        "name": "Тест",
        "message": "Нужна консультация",
    }


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
def test_worker_delivery_uses_postgres_redis_and_no_external_webhook(monkeypatch, tmp_path: Path):
    class LeadFormParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.attributes: dict[str, str | None] = {}

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag == "form" and any(name == "data-site-panel-lead-form" for name, _ in attrs):
                self.attributes = dict(attrs)

    def build_form(site_id: UUID, tenant_id: UUID, domain: str, token: str) -> dict:
        kit = load_kit("service-local-v1")
        lead_blocks = [block for block in kit.blocks if block.type == "lead_form"]
        assert len(lead_blocks) == 1
        instances, _ = instantiate_blocks(lead_blocks, site_id, kit.theme)
        manifest = SiteManifest(
            site_id=site_id,
            tenant_id=tenant_id,
            domain=domain,
            contacts={"phone": "+79990000000"},
            pages=[
                PageManifest(
                    slug="/",
                    title_template="Тестовая услуга",
                    h1_template="Тестовая услуга",
                    service="Тестовая услуга",
                    blocks=[BlockDef.model_validate(instances[0])],
                )
            ],
        )
        result = SiteBuilder(tmp_path).build(
            manifest,
            {
                "service": "Тестовая услуга",
                "phone": "+79990000000",
                "lead_token": token,
                "lead_api_url": "/api/v1/leads/public",
            },
            activate=False,
        )
        release = Path(result["release_path"])
        html = (release / "index.html").read_text(encoding="utf-8")
        assert (release / "site-panel-leads.js").is_file()
        assert "fetch(form.dataset.endpoint" in (release / "site-panel-leads.js").read_text(
            encoding="utf-8"
        )
        assert not (tmp_path / str(site_id) / "current").exists()
        parser = LeadFormParser()
        parser.feed(html)
        return parser.attributes

    async def run() -> None:
        from redis.asyncio import from_url

        await engine.dispose()
        redis = from_url(get_settings().redis_url)
        tenant_id = uuid4()
        secret = "integration-webhook-secret"
        calls: list[tuple[str, dict, str]] = []
        dispatch_response = {"ok": True, "status": 202}

        async def run_queued_job() -> None:
            worker = Worker(
                functions=WorkerSettings.functions,
                redis_settings=WorkerSettings.redis_settings,
                burst=True,
                max_burst_jobs=1,
                poll_delay=0.05,
                handle_signals=False,
            )
            try:
                assert await asyncio.wait_for(worker.run_check(), timeout=15) == 1
            finally:
                await worker.close()

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
                    lead_token=f"lead-token-{tenant_id.hex}",
                    manifest={
                        "contacts": {
                            "webhook_url": "https://receiver.example.test/leads",
                            "webhook_secret_enc": get_encryptor().encrypt(secret),
                        }
                    },
                )
                db.add(tenant)
                await db.flush()
                db.add(site)
                await db.commit()

            form = await asyncio.to_thread(
                build_form, site.id, tenant_id, site.domain, site.lead_token
            )
            assert form["data-endpoint"] == "/api/v1/leads/public"
            assert form["data-site-id"] == str(site.id)
            assert form["data-lead-token"] == site.lead_token

            phone = "+79991234567"
            email = "visitor@example.test"
            name = "Тестовый клиент"
            message = "Нужна консультация по услуге"
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://127.0.0.1"
            ) as client:
                response = await client.post(
                    form["data-endpoint"],
                    json={
                        "site_id": form["data-site-id"],
                        "lead_token": form["data-lead-token"],
                        "phone": phone,
                        "email": email,
                        "name": name,
                        "message": message,
                        "page_slug": "/",
                        "form_ts": time.time() - 5,
                        "idempotency_key": f"lead-{tenant_id.hex}",
                        "consent": True,
                    },
                )
            assert response.status_code == 201, response.text
            public_result = response.json()
            assert public_result["delivery_status"] == "pending"
            lead_id = UUID(public_result["id"])

            async with open_db_session() as db:
                lead = await db.get(Lead, lead_id)
                delivery = (
                    await db.execute(
                        select(WebhookDelivery).where(WebhookDelivery.lead_id == lead_id)
                    )
                ).scalar_one()
                consent = (
                    await db.execute(select(Consent).where(Consent.site_id == site.id))
                ).scalar_one()
                assert lead is not None
                assert consent.purposes == {"lead": True}
                assert lead.phone_enc != phone and get_encryptor().decrypt(lead.phone_enc) == phone
                assert lead.email_enc != email and get_encryptor().decrypt(lead.email_enc) == email
                assert lead.name_enc != name and get_encryptor().decrypt(lead.name_enc) == name
                assert lead.message_enc != message
                assert get_encryptor().decrypt(lead.message_enc) == message
                delivery_id = delivery.id
                expected_payload = wire_payload(delivery, lead)
                assert not {"phone", "email", "name", "message"}.intersection(delivery.payload)

            assert await redis.zcard(default_queue_name) == 1
            await run_queued_job()
            assert await redis.zcard(default_queue_name) == 0

            async with open_db_session() as db:
                delivery = await db.get(WebhookDelivery, delivery_id)
                refreshed_lead = await db.get(Lead, lead_id)
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
                await db.flush()
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
                await resend_delivery(db, retry_delivery)
                await db.commit()
                assert retry_delivery.status == "queued"
                assert retry_delivery.attempt_count == 0
                assert retry_delivery.dead_lettered_at is None
                assert retry_delivery.next_attempt_at is not None
                assert retry_lead.crm_status == "queued"

            dispatch_response.clear()
            dispatch_response.update({"ok": True, "status": 202})
            assert await enqueue_delivery(retry_delivery_id, trigger="manual")
            assert await redis.zcard(default_queue_name) == 1
            await run_queued_job()
            assert await redis.zcard(default_queue_name) == 0

            async with open_db_session() as db:
                recovered_delivery = await db.get(WebhookDelivery, retry_delivery_id)
                recovered_lead = await db.get(Lead, retry_lead.id)
                recovered_attempts = list(
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
                assert recovered_delivery is not None
                assert recovered_lead is not None
                assert recovered_delivery.status == "delivered"
                assert recovered_delivery.attempt_count == 1
                assert recovered_delivery.dead_lettered_at is None
                assert recovered_delivery.delivered_at is not None
                assert recovered_delivery.next_attempt_at is None
                assert recovered_delivery.last_http_status == 202
                assert recovered_lead.crm_status == "sent"
                assert len(recovered_attempts) == retry_delivery.max_attempts + 1
                assert recovered_attempts[-1].sequence == retry_delivery.max_attempts + 1
                assert recovered_attempts[-1].trigger == "manual"
                assert recovered_attempts[-1].status == "delivered"
                assert calls[-1] == (
                    "https://receiver.example.test/leads",
                    wire_payload(recovered_delivery, recovered_lead),
                    secret,
                )
        finally:
            try:
                async with open_db_session() as db:
                    await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
                    await db.commit()
            finally:
                try:
                    await redis.flushdb()
                    await redis.aclose()
                finally:
                    await engine.dispose()

    asyncio.run(run())
