from __future__ import annotations

import asyncio
import os
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1 import leads
from app.api.v1.leads import LeadStatusUpdate, PublicLeadIn, _csv_cell
from app.core import rate_limit
from app.models import Site, Tenant
from app.models.leads import Lead
from app.services.ab import assign_variant, significant
from app.services.leads import (
    check_honeypot,
    check_time_lock,
    cro_score,
    dispatch_webhook,
    qualify_lead_local,
    sign_webhook,
)
from pydantic import ValidationError
from site_panel_security import SSRFGuard
from sqlalchemy import delete, func, select

RUN_LEAD_IDEMPOTENCY_INTEGRATION = os.getenv("LEAD_IDEMPOTENCY_INTEGRATION") == "1"


def test_honeypot_and_timelock():
    now = time.time()
    assert check_honeypot("http://spam") is True
    assert check_honeypot("") is False
    assert check_time_lock(now, now=now) is True
    assert check_time_lock(now - 5, now=now) is False
    assert check_time_lock(now + 1, now=now) is True
    assert check_time_lock(now - 24 * 60 * 60 - 1, now=now) is True
    assert check_time_lock(float("nan"), now=now) is True


async def _check_rate_limit(limiter, key: str) -> None:
    await limiter.check(key)


def test_shared_lead_limiter_uses_atomic_redis_counter(monkeypatch):
    class Redis:
        def __init__(self, hits: int) -> None:
            self.hits = hits
            self.calls: list[tuple] = []
            self.closed = False

        async def eval(self, *args):
            self.calls.append(args)
            return self.hits

        async def aclose(self):
            self.closed = True

    allowed = Redis(hits=30)
    monkeypatch.setattr(rate_limit, "from_url", lambda _: allowed)
    asyncio.run(_check_rate_limit(rate_limit.shared_lead_limiter, "lead:hashed-ip"))
    assert allowed.calls[0][1:] == (1, "lead:hashed-ip", 60)
    assert allowed.closed is True

    blocked = Redis(hits=31)
    monkeypatch.setattr(rate_limit, "from_url", lambda _: blocked)
    with pytest.raises(Exception, match="Too many requests"):
        asyncio.run(_check_rate_limit(rate_limit.shared_lead_limiter, "lead:hashed-ip"))
    assert blocked.closed is True


def test_client_ip_uses_the_proxy_appended_address():
    request = SimpleNamespace(
        headers={"X-Forwarded-For": "forged, 203.0.113.10"},
        client=SimpleNamespace(host="172.20.0.2"),
    )

    assert rate_limit.client_ip(request) == "203.0.113.10"


def test_public_lead_requires_site_token_and_idempotency_key():
    with pytest.raises(ValidationError, match="form_ts"):
        PublicLeadIn(
            site_id=uuid4(),
            lead_token="t" * 32,
            phone="+79991234567",
            consent=True,
            idempotency_key="1750000000000-0.123456789",
        )
    with pytest.raises(ValidationError, match="lead_token"):
        PublicLeadIn(
            site_id=uuid4(),
            phone="+79991234567",
            form_ts=time.time() - 5,
            consent=True,
        )
    with pytest.raises(ValidationError, match="idempotency_key"):
        PublicLeadIn(
            site_id=uuid4(),
            lead_token="t" * 32,
            phone="+79991234567",
            form_ts=time.time() - 5,
            consent=True,
        )


@pytest.mark.parametrize("key", ["", "short", "space key", "x" * 129])
def test_public_lead_rejects_invalid_idempotency_keys(key: str):
    with pytest.raises(ValidationError, match="idempotency_key"):
        PublicLeadIn(
            site_id=uuid4(),
            lead_token="t" * 32,
            phone="+79991234567",
            form_ts=time.time() - 5,
            consent=True,
            idempotency_key=key,
        )


@pytest.mark.parametrize("timestamp", [float("nan"), float("inf"), float("-inf")])
def test_public_lead_rejects_non_finite_form_timestamp(timestamp: float):
    with pytest.raises(ValidationError, match="form_ts"):
        PublicLeadIn(
            site_id=uuid4(),
            lead_token="t" * 32,
            phone="+79991234567",
            form_ts=timestamp,
            consent=True,
            idempotency_key="1750000000000-0.123456789",
        )


def test_public_lead_rejects_stale_or_future_form_timestamp(monkeypatch):
    async def allow(*_args):
        return None

    monkeypatch.setattr(leads.shared_lead_limiter, "check", allow)
    request = SimpleNamespace(headers={}, client=SimpleNamespace(host="203.0.113.10"))
    body = PublicLeadIn(
        site_id=uuid4(),
        lead_token="t" * 32,
        phone="+79991234567",
        form_ts=time.time() + 1,
        consent=True,
        idempotency_key="1750000000000-0.123456789",
    )

    with pytest.raises(leads.HTTPException, match="Invalid form timestamp") as exc_info:
        asyncio.run(leads.create_public_lead(body, request, SimpleNamespace()))
    assert exc_info.value.status_code == 400


def test_public_lead_accepts_generated_form_idempotency_fallback():
    lead = PublicLeadIn(
        site_id=uuid4(),
        lead_token="t" * 32,
        phone="+79991234567",
        form_ts=time.time() - 5,
        consent=True,
        idempotency_key="1750000000000-0.123456789",
    )

    assert lead.idempotency_key == "1750000000000-0.123456789"


@pytest.mark.skipif(
    not RUN_LEAD_IDEMPOTENCY_INTEGRATION,
    reason="requires a PostgreSQL service container with migrations applied",
)
def test_public_lead_idempotency_is_site_scoped_and_atomic(monkeypatch) -> None:
    async def run() -> None:
        from app.db.session import open_db_session

        tenant_id = uuid4()
        first_site_id = uuid4()
        second_site_id = uuid4()
        key = f"lead-{uuid4().hex}"
        request = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))

        async def append_audit(*_args, **_kwargs) -> None:
            return None

        monkeypatch.setattr(leads, "append_audit", append_audit)
        try:
            async with open_db_session() as db:
                db.add_all(
                    [
                        Tenant(
                            id=tenant_id,
                            name="Lead idempotency integration",
                            slug=f"lead-idempotency-{tenant_id.hex}",
                            branding={},
                            quotas={},
                        ),
                        Site(
                            id=first_site_id,
                            tenant_id=tenant_id,
                            domain=f"lead-first-{first_site_id.hex}.example.test",
                            lead_token=f"lead-token-{first_site_id.hex}",
                            manifest={},
                        ),
                        Site(
                            id=second_site_id,
                            tenant_id=tenant_id,
                            domain=f"lead-second-{second_site_id.hex}.example.test",
                            lead_token=f"lead-token-{second_site_id.hex}",
                            manifest={},
                        ),
                    ]
                )
                await db.commit()

            def input_for(site_id, lead_token: str) -> PublicLeadIn:
                return PublicLeadIn(
                    site_id=site_id,
                    lead_token=lead_token,
                    phone="+79991234567",
                    form_ts=time.time() - 5,
                    consent=True,
                    idempotency_key=key,
                )

            gate = asyncio.Event()

            async def submit_first_site() -> dict:
                async with open_db_session() as db:
                    await gate.wait()
                    return await leads.create_public_lead(
                        input_for(first_site_id, f"lead-token-{first_site_id.hex}"), request, db
                    )

            first = asyncio.create_task(submit_first_site())
            second = asyncio.create_task(submit_first_site())
            gate.set()
            results = await asyncio.gather(first, second)

            assert len({item["id"] for item in results}) == 1
            assert sorted(item.get("deduped", False) for item in results) == [False, True]

            async with open_db_session() as db:
                second_site_result = await leads.create_public_lead(
                    input_for(second_site_id, f"lead-token-{second_site_id.hex}"), request, db
                )
                assert second_site_result.get("deduped") is None
                assert second_site_result["id"] != results[0]["id"]
                count = await db.scalar(
                    select(func.count())
                    .select_from(Lead)
                    .where(
                        Lead.tenant_id == tenant_id,
                        Lead.idempotency_key == key,
                    )
                )
                assert count == 2
        finally:
            async with open_db_session() as db:
                await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
                await db.commit()

    asyncio.run(run())


def test_lead_status_update_tracks_explicit_note_clear():
    update = LeadStatusUpdate(status="new", notes=None)
    assert "notes" in update.model_fields_set


@pytest.mark.parametrize("raw", ["=1+1", "+value", "-value", "@value", "\tvalue"])
def test_csv_cells_neutralize_spreadsheet_formulas(raw: str):
    assert _csv_cell(raw) == f"'{raw}"


def test_csv_cells_keep_normal_values():
    assert _csv_cell("plain text") == "plain text"


def test_qualify():
    assert qualify_lead_local("купить ссылки seo", "+79991234567") == "spam"
    assert qualify_lead_local("Нужен ремонт", "+79991234567") == "qualified"


def test_webhook_sign_stable():
    s1 = sign_webhook({"a": 1}, "secret")
    s2 = sign_webhook({"a": 1}, "secret")
    assert s1 == s2


def test_pinned_url_preserves_host_and_sni(monkeypatch):
    monkeypatch.setattr(SSRFGuard, "resolve_safe", lambda *_: "203.0.113.10")

    pinned = SSRFGuard().pin_url("https://receiver.example.test:8443/leads?source=panel")

    assert pinned.transport_url == "https://203.0.113.10:8443/leads?source=panel"
    assert pinned.host_header == "receiver.example.test:8443"
    assert pinned.sni_hostname == "receiver.example.test"


def test_dispatch_webhook_connects_to_pinned_ip(monkeypatch):
    from app.services import leads as leads_service

    sent: list[object] = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def build_request(self, method, url, **kwargs):
            return leads_service.httpx.Request(method, url, **kwargs)

        async def send(self, request):
            sent.append(request)
            return leads_service.httpx.Response(202, request=request)

    monkeypatch.setattr(SSRFGuard, "resolve_safe", lambda *_: "203.0.113.10")
    monkeypatch.setattr(leads_service.httpx, "AsyncClient", lambda **_: Client())

    result = asyncio.run(
        dispatch_webhook(
            "https://receiver.example.test/leads",
            {"idempotency_key": "lead-123"},
            "secret",
        )
    )

    assert result == {"status": 202, "ok": True}
    request = sent[0]
    assert str(request.url) == "https://203.0.113.10/leads"
    assert request.headers["Host"] == "receiver.example.test"
    assert request.extensions["sni_hostname"] == "receiver.example.test"


def test_cro_score():
    result = cro_score("hero", ["offer", "cta_above_fold"])
    assert result["score"] < 100
    assert "utp" in result["missing"]


def test_ab_deterministic():
    a = assign_variant("visitor-1", "hero", ["A", "B"])
    b = assign_variant("visitor-1", "hero", ["A", "B"])
    assert a == b
    assert significant(30, 100, 50, 100) is False
    assert significant(20, 200, 40, 200) is True
