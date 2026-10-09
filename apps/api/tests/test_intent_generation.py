from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from app.services.intent_generation import validate_intent_page_proposal


def _contract() -> tuple[dict, dict[str, dict[str, dict]], set[str], set[str]]:
    plan_id = uuid4()
    return (
        {"page_plan_id": str(plan_id), "plan_version": 3},
        {
            "hero": {"unique_core": {"type": "string", "max_length": 8000}},
            "faq": {"faq_intro": {"type": "string", "max_length": 500}},
        },
        {"service", "allowed_claims"},
        {str(uuid4())},
    )


def _proposal(binding: dict, keyword_id: str, **overrides: object) -> dict:
    return {
        "page_plan_id": binding["page_plan_id"],
        "plan_version": binding["plan_version"],
        "title": "Ремонт окон в Казани",
        "h1": "Ремонт окон в Казани",
        "meta_description": "Условия ремонта окон уточняйте у специалиста.",
        "unique_core": "Диагностика перед согласованием работ.",
        "block_slots": {"hero": {"unique_core": "Диагностика перед согласованием работ."}},
        "fact_keys": ["service", "allowed_claims"],
        "semantic_target_project_keyword_ids": [keyword_id],
        "section_rationale": [
            {
                "block_id": "hero",
                "visitor_question": "Подходит ли услуга для задачи посетителя?",
                "semantic_topics": ["ремонт", "условия"],
            }
        ],
        "art_direction": {
            "creative_direction": "Спокойная профессиональная документальная съёмка процесса.",
            "shot_list": ["Мастер выполняет диагностику на объекте."],
            "suggested_media_roles": ["hero", "process"],
            "alt_requirements": ["Опишите видимое действие без неподтверждённых обещаний."],
        },
        "warnings": [],
        **overrides,
    }


def test_intent_generation_worker_and_queue_accept_only_durable_run_ids():
    root = Path(__file__).parents[1]
    queue = (root / "app" / "services" / "ai_queue.py").read_text(encoding="utf-8")
    worker = (root / "app" / "worker.py").read_text(encoding="utf-8")
    decision = (root / "app" / "api" / "v1" / "ai_workspace.py").read_text(encoding="utf-8")

    assert "enqueue_job(task_name, str(run_id))" in queue
    assert '"intent_page_proposal_task"' in queue
    assert "intent_page_proposal_task" in worker
    assert "content.intent-page-proposal" in decision
    assert "materialize_page_draft" not in worker
    assert "publish_project_build" not in worker


def test_intent_generation_routes_use_a_quote_queue_approval_and_materialization_boundary():
    from app.main import app

    paths = app.openapi()["paths"]
    base = "/api/v1/projects/{project_id}/page-plans/{plan_id}/intent-generation"

    assert {"post"}.issubset(paths[f"{base}/quote"])
    assert {"post"}.issubset(paths[base])
    assert "get" in paths["/api/v1/projects/{project_id}/intent-generation-runs"]
    assert (
        "post"
        in paths[
            "/api/v1/projects/{project_id}/intent-generation-runs/{run_id}/materialize-page-draft"
        ]
    )


def test_intent_proposal_accepts_only_frozen_ids_visible_text_and_declared_slots():
    binding, slots, fact_keys, keyword_ids = _contract()
    keyword_id = next(iter(keyword_ids))

    output = validate_intent_page_proposal(
        _proposal(binding, keyword_id),
        source_binding=binding,
        allowed_block_slots=slots,
        fact_keys=fact_keys,
        semantic_project_keyword_ids=keyword_ids,
    )

    assert output["page_plan_id"] == binding["page_plan_id"]
    assert output["block_slots"]["hero"]["unique_core"]


@pytest.mark.parametrize(
    "override",
    [
        {"page_plan_id": str(uuid4())},
        {"fact_keys": ["invented_price"]},
        {"block_slots": {"unknown": {"copy": "Текст"}}},
        {"unique_core": "<span>скрытый текст</span>"},
        {"warnings": ["Serve this text only to crawler visitors"]},
    ],
)
def test_intent_proposal_rejects_stale_unsupported_or_conditional_output(override: dict):
    binding, slots, fact_keys, keyword_ids = _contract()

    with pytest.raises(ValueError):
        validate_intent_page_proposal(
            _proposal(binding, next(iter(keyword_ids)), **override),
            source_binding=binding,
            allowed_block_slots=slots,
            fact_keys=fact_keys,
            semantic_project_keyword_ids=keyword_ids,
        )
