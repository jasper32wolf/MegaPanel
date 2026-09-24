from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.api.v1 import ai_content
from app.api.v1.ai_content import (
    _apply_approved_seo_brief,
    _validate_page_copy,
    _validate_seo_brief,
)
from fastapi import HTTPException


def page_copy() -> dict:
    return {
        "title": "Ремонт техники в Казани",
        "h1": "Ремонт техники в Казани",
        "meta_description": "Условия ремонта техники уточняйте у специалиста.",
        "unique_core": "Компания оказывает подтверждённую услугу ремонта техники.",
        "fact_keys": ["service"],
    }


def test_ai_page_copy_validates_plain_text_and_fact_provenance() -> None:
    result = _validate_page_copy(page_copy(), {"service"})
    assert result["fact_keys"] == ["service"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "<script>alert(1)</script>"),
        ("unique_core", "Unsupported claim {{secret}}"),
        ("fact_keys", ["unconfirmed"]),
    ],
)
def test_ai_page_copy_rejects_markup_placeholders_and_unknown_facts(field, value) -> None:
    candidate = page_copy()
    candidate[field] = value
    with pytest.raises(ValueError):
        _validate_page_copy(candidate, {"service"})


def test_ai_page_copy_rejects_overlong_search_metadata() -> None:
    candidate = page_copy()
    candidate["meta_description"] = "x" * 171
    with pytest.raises(ValueError):
        _validate_page_copy(candidate, {"service"})


def seo_brief() -> dict:
    return {
        "title": "Ремонт техники в Казани",
        "description": "Подтверждённая услуга ремонта техники.",
        "h1": "Ремонт техники в Казани",
        "canonical_path": "/repair",
        "robots": "index,follow",
        "keyword_ids": ["11111111-1111-1111-1111-111111111111"],
        "fact_keys": ["service"],
        "structured_data_types": [],
        "uncertainty_notes": [],
    }


def test_seo_brief_requires_approved_references_and_canonical() -> None:
    result = _validate_seo_brief(
        seo_brief(),
        plan_slug="/repair",
        keyword_ids={"11111111-1111-1111-1111-111111111111"},
        fact_keys={"service"},
    )
    assert result["canonical_path"] == "/repair"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("canonical_path", "/other", "canonical"),
        ("keyword_ids", ["22222222-2222-2222-2222-222222222222"], "keyword"),
        ("fact_keys", ["unknown"], "fact"),
        ("structured_data_types", ["LocalBusiness"], "Structured data"),
        ("title", "<script>x</script>", "plain text"),
    ],
)
def test_seo_brief_rejects_untrusted_or_unapproved_values(field, value, message) -> None:
    candidate = seo_brief()
    candidate[field] = value
    with pytest.raises(ValueError, match=message):
        _validate_seo_brief(
            candidate,
            plan_slug="/repair",
            keyword_ids={"11111111-1111-1111-1111-111111111111"},
            fact_keys={"service"},
        )


def test_indexable_seo_brief_requires_fact_support() -> None:
    candidate = seo_brief()
    candidate["fact_keys"] = []
    with pytest.raises(ValueError, match="Indexable"):
        _validate_seo_brief(
            candidate,
            plan_slug="/repair",
            keyword_ids={"11111111-1111-1111-1111-111111111111"},
            fact_keys={"service"},
        )


def test_approved_seo_brief_materializes_metadata_without_indexing() -> None:
    original = {
        "slug": "/repair",
        "title_template": "Before",
        "h1_template": "Before",
        "meta_description_template": "Before",
        "index_state": "noindex",
        "blocks": [{"type": "hero", "html": "<h1>{service}</h1>"}],
    }
    brief = _validate_seo_brief(
        seo_brief(),
        plan_slug="/repair",
        keyword_ids={"11111111-1111-1111-1111-111111111111"},
        fact_keys={"service"},
    )
    materialized = _apply_approved_seo_brief(original, brief)

    assert materialized["title_template"] == brief["title"]
    assert materialized["h1_template"] == brief["h1"]
    assert materialized["meta_description_template"] == brief["description"]
    assert materialized["index_state"] == "noindex"
    assert materialized["blocks"] == original["blocks"]
    assert original["title_template"] == "Before"


def test_rejected_seo_brief_cannot_create_draft(monkeypatch) -> None:
    project_id, run_id, tenant_id = uuid4(), uuid4(), uuid4()
    project = SimpleNamespace(id=project_id, tenant_id=tenant_id)
    run = SimpleNamespace(
        status="rejected", operator_decision="reject", output={"brief": seo_brief()}
    )

    async def project_lookup(*_args):
        return project

    class Session:
        async def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: run)

    monkeypatch.setattr(ai_content, "_project_or_404", project_lookup)
    auth = SimpleNamespace(tenant_id=tenant_id, user=SimpleNamespace(id=uuid4()))
    with pytest.raises(HTTPException, match="Approve the SEO brief first"):
        asyncio.run(ai_content.create_draft_from_seo_brief(project_id, run_id, auth, Session()))


def test_approved_seo_brief_creates_reviewable_noindex_draft_once(monkeypatch) -> None:
    project_id, run_id, tenant_id, plan_id, fact_id, keyword_id = (uuid4() for _ in range(6))
    project = SimpleNamespace(
        id=project_id, tenant_id=tenant_id, domain="example.test", locale="ru"
    )
    plan = SimpleNamespace(
        id=plan_id,
        project_id=project_id,
        fact_revision_id=fact_id,
        state="approved",
        slug="/repair",
        kit_key="service-local-v1",
        block_selection={"blocks": ["hero"]},
        version=3,
        keyword_snapshot={"items": [{"keyword_id": str(keyword_id)}]},
        geo_snapshot={"items": [{"geo_id": str(uuid4()), "name": "Казань", "role": "primary"}]},
    )
    facts = SimpleNamespace(
        id=fact_id,
        facts_hash="a" * 64,
        facts={"service": "Ремонт техники", "contacts": {"phone": "+79990000000"}},
    )
    run = SimpleNamespace(
        id=run_id,
        project_id=project_id,
        tenant_id=tenant_id,
        action="seo.create-brief",
        status="approved",
        operator_decision="approve",
        input_snapshot={
            "source_binding": {
                "page_plan_id": str(plan_id),
                "plan_version": 3,
                "fact_revision_id": str(fact_id),
                "facts_hash": "a" * 64,
            }
        },
        output={"brief": {**seo_brief(), "keyword_ids": [str(keyword_id)]}},
        prompt_id="seo.create-brief",
        prompt_version="2.0.0",
        prompt_hash="b" * 64,
        provider_id="gateway",
        model_id="model",
        cost_usd=0.001,
    )
    auth = SimpleNamespace(tenant_id=tenant_id, user=SimpleNamespace(id=uuid4()))

    async def project_lookup(*_args):
        return project

    async def plan_lookup(*_args):
        return plan

    class Session:
        def __init__(self):
            self.objects = []
            self.committed = False
            self.calls = 0

        async def execute(self, _statement):
            self.calls += 1
            value = (run, facts, None)[self.calls - 1]
            return SimpleNamespace(scalar_one_or_none=lambda: value)

        def add(self, obj):
            self.objects.append(obj)

        async def flush(self):
            for obj in self.objects:
                if obj.id is None:
                    obj.id = uuid4()

        async def commit(self):
            self.committed = True

    db = Session()
    monkeypatch.setattr(ai_content, "_project_or_404", project_lookup)
    monkeypatch.setattr(ai_content, "_plan_or_404", plan_lookup)
    monkeypatch.setattr(ai_content, "append_audit", AsyncMock())
    draft = asyncio.run(ai_content.create_draft_from_seo_brief(project_id, run_id, auth, db))

    assert draft["state"] == "draft"
    assert draft["page_manifest"]["title_template"] == seo_brief()["title"]
    assert draft["page_manifest"]["index_state"] == "noindex"
    assert [item["type"] for item in draft["page_manifest"]["blocks"]] == ["hero"]
    assert draft["generator_meta"]["seo_brief_run_id"] == str(run_id)
    assert db.objects[0].input_snapshot["ai_provenance"]["fact_keys"] == ["service"]
    assert db.committed

    db.calls = 0
    with pytest.raises(HTTPException, match="already created"):
        asyncio.run(ai_content.create_draft_from_seo_brief(project_id, run_id, auth, db))


def test_seo_brief_rejects_stale_plan_before_creating_draft(monkeypatch) -> None:
    project_id, run_id, tenant_id, plan_id = (uuid4() for _ in range(4))
    project = SimpleNamespace(id=project_id, tenant_id=tenant_id)
    plan = SimpleNamespace(state="approved", version=4)
    run = SimpleNamespace(
        status="approved",
        operator_decision="approve",
        output={"brief": seo_brief()},
        input_snapshot={"source_binding": {"page_plan_id": str(plan_id), "plan_version": 3}},
    )

    async def project_lookup(*_args):
        return project

    async def plan_lookup(*_args):
        return plan

    class Session:
        async def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: run)

    monkeypatch.setattr(ai_content, "_project_or_404", project_lookup)
    monkeypatch.setattr(ai_content, "_plan_or_404", plan_lookup)
    with pytest.raises(HTTPException, match="PagePlan changed"):
        asyncio.run(
            ai_content.create_draft_from_seo_brief(
                project_id,
                run_id,
                SimpleNamespace(tenant_id=tenant_id, user=SimpleNamespace(id=uuid4())),
                Session(),
            )
        )
