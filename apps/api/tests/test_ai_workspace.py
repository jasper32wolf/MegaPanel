from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from app.api.v1 import ai_workspace
from app.api.v1.ai_workspace import _summary_out, _validate_proposal, list_ai_runs
from app.main import app
from app.schemas.ai import ArchitectureProposalRequest
from fastapi import HTTPException

KEYWORD = UUID("11111111-1111-1111-1111-111111111111")
GEO = UUID("22222222-2222-2222-2222-222222222222")


def page_proposal() -> dict:
    return {
        "key": "home",
        "title": "Home",
        "purpose": "Service overview",
        "slug": "/",
        "keyword_ids": [str(KEYWORD)],
        "geo_ids": [str(GEO)],
        "fact_keys": ["service"],
        "kit_key": "kit-a",
        "block_ids": ["hero"],
        "uncertainty_notes": [],
    }


def test_architecture_output_validates_catalog_ids() -> None:
    pages = _validate_proposal(
        {"pages": [page_proposal()]},
        keyword_ids={str(KEYWORD)},
        geo_ids={str(GEO)},
        fact_keys={"service"},
        catalogs={"kit-a": {"hero"}},
    )
    assert pages[0]["slug"] == "/"


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("keyword_ids", [str(GEO)], "unselected keyword"),
        ("geo_ids", [str(KEYWORD)], "unselected place"),
        ("fact_keys", ["secret"], "unknown fact"),
        ("kit_key", "missing", "unknown kit"),
        ("block_ids", ["missing"], "unknown block"),
        ("slug", "/../admin", "invalid slug"),
    ],
)
def test_architecture_output_rejects_untrusted_references(field, value, expected) -> None:
    page = page_proposal()
    page[field] = value
    with pytest.raises(ValueError, match=expected):
        _validate_proposal(
            {"pages": [page]},
            keyword_ids={str(KEYWORD)},
            geo_ids={str(GEO)},
            fact_keys={"service"},
            catalogs={"kit-a": {"hero"}},
        )


def test_architecture_output_rejects_extra_fields() -> None:
    page = page_proposal()
    page["html"] = "<script>alert(1)</script>"
    with pytest.raises(ValueError):
        _validate_proposal(
            {"pages": [page]},
            keyword_ids={str(KEYWORD)},
            geo_ids={str(GEO)},
            fact_keys={"service"},
            catalogs={"kit-a": {"hero"}},
        )


def test_run_history_response_excludes_input_output_and_secrets() -> None:
    run = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        action="seo.create-brief",
        status="failed",
        provider_id="gateway",
        model_id="model-a",
        prompt_id="seo.create-brief",
        prompt_version="2.0.0",
        prompt_hash="a" * 64,
        input_snapshot_hash="b" * 64,
        output={"brief": {"title": "secret-value"}},
        usage={"input_tokens": 12, "output_tokens": 3},
        cost_usd=0.001,
        error_code="upstream_timeout",
        created_at=None,
        input_snapshot={"api_key": "secret-value"},
        encrypted_api_key="secret-value",
    )
    payload = _summary_out(run).model_dump(mode="json")
    assert payload["status"] == "failed" and payload["usage"]["input_tokens"] == 12
    assert (
        "input_snapshot" not in payload
        and "output" not in payload
        and "encrypted_api_key" not in payload
    )
    assert "secret-value" not in str(payload)


def test_run_history_filters_on_tenant_project_action_and_status() -> None:
    tenant_id, project_id = uuid4(), uuid4()

    class Session:
        def __init__(self):
            self.statement = None

        async def execute(self, statement):
            self.statement = statement
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    db = Session()
    rows = asyncio.run(
        list_ai_runs(
            project_id=project_id,
            action="seo.create-brief",
            status="failed",
            auth=SimpleNamespace(tenant_id=tenant_id),
            db=db,
        )
    )
    assert rows == []
    compiled = db.statement.compile()
    conditions = str(compiled)
    assert all(
        key in conditions
        for key in ("ai_runs.tenant_id", "ai_runs.project_id", "ai_runs.action", "ai_runs.status")
    )
    assert (
        compiled.params["tenant_id_1"] == tenant_id
        and compiled.params["project_id_1"] == project_id
    )
    assert (
        compiled.params["action_1"] == "seo.create-brief"
        and compiled.params["status_1"] == "failed"
    )


def test_run_history_rejects_unknown_tenant() -> None:
    class Session:
        async def execute(self, _statement):
            raise AssertionError("No query should run without tenant scope")

    with pytest.raises(HTTPException) as error:
        asyncio.run(list_ai_runs(auth=SimpleNamespace(tenant_id=None), db=Session()))
    assert error.value.status_code == 403


def test_run_history_and_detail_routes_are_registered() -> None:
    paths = app.openapi()["paths"]
    assert "get" in paths["/api/v1/ai/runs"] and "get" in paths["/api/v1/ai/runs/{run_id}"]
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from app.api.v1 import ai_workspace
from app.api.v1.ai_workspace import _summary_out, _validate_proposal, list_ai_runs
from app.main import app
from fastapi import HTTPException

KEYWORD = UUID("11111111-1111-1111-1111-111111111111")
GEO = UUID("22222222-2222-2222-2222-222222222222")


def page_proposal() -> dict:
    return {
        "key": "home", "title": "Home", "purpose": "Service overview", "slug": "/",
        "keyword_ids": [str(KEYWORD)], "geo_ids": [str(GEO)], "fact_keys": ["service"],
        "kit_key": "kit-a", "block_ids": ["hero"], "uncertainty_notes": [],
    }


def test_architecture_output_validates_catalog_ids() -> None:
    pages = _validate_proposal({"pages": [page_proposal()]}, keyword_ids={str(KEYWORD)}, geo_ids={str(GEO)}, fact_keys={"service"}, catalogs={"kit-a": {"hero"}})
    assert pages[0]["slug"] == "/"


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [("keyword_ids", [str(GEO)], "unselected keyword"), ("geo_ids", [str(KEYWORD)], "unselected place"), ("fact_keys", ["secret"], "unknown fact"), ("kit_key", "missing", "unknown kit"), ("block_ids", ["missing"], "unknown block"), ("slug", "/../admin", "invalid slug")],
)
def test_architecture_output_rejects_untrusted_references(field, value, expected) -> None:
    page = page_proposal(); page[field] = value
    with pytest.raises(ValueError, match=expected):
        _validate_proposal({"pages": [page]}, keyword_ids={str(KEYWORD)}, geo_ids={str(GEO)}, fact_keys={"service"}, catalogs={"kit-a": {"hero"}})


def test_architecture_output_rejects_extra_fields() -> None:
    page = page_proposal(); page["html"] = "<script>alert(1)</script>"
    with pytest.raises(ValueError):
        _validate_proposal({"pages": [page]}, keyword_ids={str(KEYWORD)}, geo_ids={str(GEO)}, fact_keys={"service"}, catalogs={"kit-a": {"hero"}})


def test_run_history_response_excludes_input_output_and_secrets() -> None:
    run = SimpleNamespace(id=uuid4(), project_id=uuid4(), action="seo.create-brief", status="failed", provider_id="gateway", model_id="model-a", prompt_id="seo.create-brief", prompt_version="2.0.0", prompt_hash="a" * 64, input_snapshot_hash="b" * 64, output={"brief": {"title": "secret-value"}}, usage={"input_tokens": 12, "output_tokens": 3}, cost_usd=0.001, error_code="upstream_timeout", created_at=None, input_snapshot={"api_key": "secret-value"}, encrypted_api_key="secret-value")
    payload = _summary_out(run).model_dump(mode="json")
    assert payload["status"] == "failed" and payload["usage"]["input_tokens"] == 12
    assert "input_snapshot" not in payload and "output" not in payload and "encrypted_api_key" not in payload
    assert "secret-value" not in str(payload)


def test_run_history_filters_on_tenant_project_action_and_status() -> None:
    tenant_id, project_id = uuid4(), uuid4()
    class Session:
        def __init__(self): self.statement = None
        async def execute(self, statement):
            self.statement = statement
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
    db = Session(); rows = asyncio.run(list_ai_runs(project_id=project_id, action="seo.create-brief", status="failed", auth=SimpleNamespace(tenant_id=tenant_id), db=db))
    assert rows == []
    compiled = db.statement.compile(); conditions = str(compiled)
    assert all(key in conditions for key in ("ai_runs.tenant_id", "ai_runs.project_id", "ai_runs.action", "ai_runs.status"))
    assert compiled.params["tenant_id_1"] == tenant_id and compiled.params["project_id_1"] == project_id
    assert compiled.params["action_1"] == "seo.create-brief" and compiled.params["status_1"] == "failed"


def test_run_history_rejects_unknown_tenant() -> None:
    class Session:
        async def execute(self, _statement): raise AssertionError("No query should run without tenant scope")
    with pytest.raises(HTTPException) as error: asyncio.run(list_ai_runs(auth=SimpleNamespace(tenant_id=None), db=Session()))
    assert error.value.status_code == 403


def test_run_history_and_detail_routes_are_registered() -> None:
    paths = app.openapi()["paths"]
    assert "get" in paths["/api/v1/ai/runs"] and "get" in paths["/api/v1/ai/runs/{run_id}"]

__all__ = []

    project_id, tenant_id, connection_id, fact_id = (uuid4() for _ in range(4))
    project = SimpleNamespace(id=project_id, tenant_id=tenant_id, name="Service", locale="ru", niche="repair", current_fact_revision_id=fact_id)
    connection = SimpleNamespace(
        id=connection_id,
        enabled=True,
        provider_id="gateway",
        model_ids=["model"],
        metadata_json={
            "model_pricing": {
                "model": {
                    "observed_at": "2026-09-24T00:00:00+00:00",
                    "input_price_usd_per_million": 1.0,
                    "output_price_usd_per_million": 1.0,
                    "source": "operator",
                }
            }
        },
    )
    facts = SimpleNamespace(id=fact_id, facts={"service": "Repair"}, facts_hash="a" * 64)
    plan = SimpleNamespace(
        id=uuid4(), slug="/repair", objective="Repair", intent="service", state="approved"
    )

    class Result:
        def __init__(self, value=None):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

        def scalars(self):
            return self

        def all(self):
            return [plan]

    class Session:
        def __init__(self):
            self.i = 0

        async def execute(self, _statement):
            self.i += 1
            if self.i == 1:
                return Result(project)
            if self.i == 2:
                return Result(facts)
            if self.i in {3, 4}:
                return SimpleNamespace(all=lambda: [])
            if self.i == 5:
                return SimpleNamespace(all=lambda: [(SimpleNamespace(role="primary", morph_overrides={}), SimpleNamespace(id=uuid4(), name="City", kind="city", name_forms={}))])
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [plan]))

        async def get(self, _model, _key):
            return connection

    async def budget(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        ai_workspace, "list_kits", lambda: [{"key": "service-local-v1", "blocks": ["hero"]}]
    )
    monkeypatch.setattr(ai_workspace, "enforce_ai_budget", budget, raising=False)
    body = ArchitectureProposalRequest(
        provider_connection_id=connection_id,
        model="model",
        max_cost_usd=1,
        confirm_external_processing=True,
        confirm_provider_budget=True,
    )
    context = asyncio.run(
        ai_workspace._prepare_architecture_context(
            project_id, body, SimpleNamespace(tenant_id=tenant_id), Session()
        )
    )
    assert context["estimated_cost"] >= 0


__all__ = []
