from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1.projects import _selection_snapshots
from app.schemas.workflow import (
    LeadOutcomeIn,
    PagePlanCreate,
    ProjectGeoUpdate,
    ProjectKeywordsUpdate,
)
from app.services.generation import create_page_draft
from app.services.qa import run_page_qa
from pydantic import ValidationError


class SelectionDatabase:
    def __init__(self, keyword_rows: list[object], geo_rows: list[object]):
        self.rows = [keyword_rows, geo_rows]

    async def execute(self, _: object) -> object:
        rows = self.rows.pop(0)
        return SimpleNamespace(all=lambda: rows)


def test_page_plan_normalizes_root_slug():
    assert (
        PagePlanCreate(slug=" / ", objective="Главная страница", kit_key="service-local-v1").slug
        == "/"
    )


def test_project_selection_requires_keywords_and_primary_geo():
    project = SimpleNamespace(id=uuid4())
    _, _, blockers = __import__("asyncio").run(
        _selection_snapshots(SelectionDatabase([], []), project)
    )

    assert blockers == [
        "Select at least one project keyword",
        "Select one primary geographic place",
    ]


def test_project_selection_snapshots_operator_choices():
    keyword_id = uuid4()
    geo_id = uuid4()
    keyword = SimpleNamespace(id=keyword_id, phrase="ремонт машин", meta={"intent": "order"})
    place = SimpleNamespace(id=geo_id, name="Казань", kind="city", name_forms={"prep": "Казани"})
    keyword_binding = SimpleNamespace(cluster="repair", intent=None, priority=10)
    geo_binding = SimpleNamespace(role="primary", position=0, morph_overrides={})
    project = SimpleNamespace(id=uuid4())

    keywords, geo, blockers = __import__("asyncio").run(
        _selection_snapshots(
            SelectionDatabase([(keyword_binding, keyword)], [(geo_binding, place)]), project
        )
    )

    assert blockers == []
    assert keywords["items"][0]["keyword_id"] == str(keyword_id)
    assert geo["items"][0]["forms"]["prep"] == "Казани"


def test_project_selection_models_reject_duplicates():
    identifier = uuid4()

    with pytest.raises(ValidationError, match="duplicates"):
        ProjectKeywordsUpdate(items=[{"keyword_id": identifier}, {"keyword_id": identifier}])
    with pytest.raises(ValidationError, match="only one primary"):
        ProjectGeoUpdate(
            items=[{"geo_id": uuid4(), "role": "primary"}, {"geo_id": uuid4(), "role": "primary"}]
        )


def test_terminal_lead_outcomes_require_a_reason():
    with pytest.raises(ValidationError, match="Provide a reason"):
        LeadOutcomeIn(outcome="lost")

    assert LeadOutcomeIn(outcome="lost", reason="price").reason == "price"


def test_generation_uses_confirmed_snapshot_only():
    project = SimpleNamespace(id=uuid4(), domain="example.test")
    plan = SimpleNamespace(
        slug="/repair",
        kit_key="service-local-v1",
        version=3,
        keyword_snapshot={"items": []},
        geo_snapshot={
            "items": [
                {
                    "geo_id": str(uuid4()),
                    "name": "Казань",
                    "forms": {"prep": "Казани"},
                    "role": "primary",
                }
            ]
        },
    )
    facts = SimpleNamespace(
        id=uuid4(),
        facts_hash="a" * 64,
        facts={"service": "Ремонт техники", "contacts": {"phone": "+79990000000"}},
    )

    page, snapshot, _ = create_page_draft(project=project, plan=plan, facts=facts)

    assert snapshot["facts"] == facts.facts
    assert page["slug"] == "/repair"
    assert snapshot["generator_meta"]["tokens"] == 0


def test_qa_blocks_missing_confirmed_facts_and_warns_on_thin_candidate():
    page = {
        "slug": "/repair",
        "title_template": "Ремонт",
        "h1_template": "Ремонт",
        "meta_description_template": "Ремонт",
        "service": "Ремонт",
    }

    blocked = run_page_qa(
        page_manifest=page,
        input_snapshot={
            "facts": {},
            "project_domain": "example.test",
            "geo_snapshot": {"items": [{}]},
        },
        existing_texts=[],
    )
    warned = run_page_qa(
        page_manifest=page,
        input_snapshot={
            "facts": {"service": "Ремонт", "contacts": {"phone": "+79990000000"}},
            "project_domain": "example.test",
            "geo_snapshot": {"items": [{}]},
            "keyword_snapshot": {"items": []},
        },
        existing_texts=[],
    )

    assert blocked["verdict"] == "block"
    assert warned["verdict"] == "warn"
