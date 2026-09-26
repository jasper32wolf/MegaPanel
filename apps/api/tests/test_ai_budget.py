import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1 import ai_workspace
from app.core.config import Settings
from app.providers import Usage
from app.services import ai_budget
from fastapi import HTTPException


class ScalarResult:
    def __init__(self, value: float):
        self.value = value

    def scalar_one(self) -> float:
        return self.value


class Session:
    def __init__(self, daily_total: float, monthly_total: float):
        self.totals = iter((daily_total, monthly_total))
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return ScalarResult(next(self.totals))


def settings(*, disabled=False, daily=10.0, monthly=100.0):
    return SimpleNamespace(
        ai_disabled=disabled,
        ai_daily_budget_usd=daily,
        ai_monthly_budget_usd=monthly,
    )


def test_ai_budget_allows_under_limit(monkeypatch) -> None:
    monkeypatch.setattr(ai_budget, "get_settings", lambda: settings())
    db = Session(1.0, 15.0)

    asyncio.run(ai_budget.enforce_ai_budget(db, tenant_id=uuid4(), estimated_cost_usd=2.0))

    assert len(db.statements) == 2
    assert all("ai_runs.tenant_id" in str(statement) for statement in db.statements)


def test_unknown_provider_usage_keeps_the_conservative_estimate() -> None:
    pricing = {
        "input_price_usd_per_million": 1.0,
        "output_price_usd_per_million": 2.0,
    }

    assert ai_workspace._actual_cost(Usage(), pricing, 0.6) == 0.6
    assert ai_workspace._usage_payload(Usage()) == {}
    assert ai_workspace._actual_cost(Usage(3, 4, reported=True), pricing, 0.6) == pytest.approx(
        0.000011
    )


def test_failed_run_keeps_the_reservation_when_provider_usage_is_unknown(monkeypatch) -> None:
    tenant_id = uuid4()
    run = ai_workspace.AIRun(
        tenant_id=tenant_id,
        project_id=None,
        action="test.reservation",
        status="reserved",
        provider_id="test-provider",
        model_id="test-model",
        prompt_id="test",
        prompt_version="1",
        prompt_hash="a" * 64,
        input_snapshot_hash="b" * 64,
        input_snapshot={},
        output={},
        usage={},
        cost_usd=0.6,
        error_code="budget_reserved",
    )

    class Database:
        def __init__(self) -> None:
            self.added: list[object] = []
            self.commits = 0

        def add(self, value: object) -> None:
            self.added.append(value)

        async def flush(self) -> None:
            return None

        async def commit(self) -> None:
            self.commits += 1

    database = Database()

    async def append_audit(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(ai_workspace, "append_audit", append_audit)

    asyncio.run(
        ai_workspace._record_failed_run(
            db=database,
            auth=SimpleNamespace(tenant_id=tenant_id, user=SimpleNamespace(id=uuid4())),
            project_id=uuid4(),
            provider_id="test-provider",
            model_id="test-model",
            prompt=SimpleNamespace(prompt_id="test", version="1", content_hash="a" * 64),
            snapshot={},
            error_code="network_error",
            reservation=run,
        )
    )

    assert database.added == []
    assert database.commits == 1
    assert run.status == "failed"
    assert run.cost_usd == 0.6
    assert run.error_code == "network_error"


def test_ai_budget_kill_switch_blocks_before_provider_call(monkeypatch) -> None:
    monkeypatch.setattr(ai_budget, "get_settings", lambda: settings(disabled=True))
    db = Session(0, 0)

    with pytest.raises(HTTPException) as error:
        asyncio.run(ai_budget.enforce_ai_budget(db, tenant_id=uuid4(), estimated_cost_usd=0.01))

    assert error.value.status_code == 503
    assert error.value.detail["code"] == "ai_disabled"
    assert not db.statements


def test_ai_budget_daily_limit_blocks(monkeypatch) -> None:
    monkeypatch.setattr(ai_budget, "get_settings", lambda: settings(daily=1.0))
    db = Session(0.9, 0.9)

    with pytest.raises(HTTPException) as error:
        asyncio.run(ai_budget.enforce_ai_budget(db, tenant_id=uuid4(), estimated_cost_usd=0.2))

    assert error.value.status_code == 429
    assert error.value.detail["code"] == "ai_daily_budget_exceeded"
    assert len(db.statements) == 1


def test_ai_budget_rolling_month_limit_blocks(monkeypatch) -> None:
    monkeypatch.setattr(ai_budget, "get_settings", lambda: settings(monthly=5.0))
    db = Session(0.5, 4.9)

    with pytest.raises(HTTPException) as error:
        asyncio.run(ai_budget.enforce_ai_budget(db, tenant_id=uuid4(), estimated_cost_usd=0.2))

    assert error.value.status_code == 429
    assert error.value.detail["code"] == "ai_monthly_budget_exceeded"
    assert len(db.statements) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("ai_daily_budget_usd", -1),
        ("ai_monthly_budget_usd", float("nan")),
        ("ai_daily_budget_usd", float("inf")),
    ],
)
def test_budget_settings_reject_invalid_values(field, value) -> None:
    with pytest.raises(ValueError):
        Settings.model_validate({field: value})
