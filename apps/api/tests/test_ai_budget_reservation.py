from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1 import ai_workspace
from app.models import Tenant
from app.services import ai_budget
from fastapi import HTTPException
from sqlalchemy import delete, select

RUN_AI_BUDGET_INTEGRATION = os.getenv("AI_BUDGET_INTEGRATION") == "1"


@pytest.mark.skipif(
    not RUN_AI_BUDGET_INTEGRATION,
    reason="requires a PostgreSQL service container with migrations applied",
)
def test_concurrent_ai_budget_reservations_allow_only_one(monkeypatch) -> None:
    async def run() -> None:
        from app.db.session import engine, open_db_session

        tenant_id = uuid4()
        prompt = SimpleNamespace(prompt_id="test", version="1", content_hash="a" * 64)
        auth = SimpleNamespace(tenant_id=tenant_id, user=SimpleNamespace(id=uuid4()))

        async def append_audit(*_args, **_kwargs) -> None:
            return None

        monkeypatch.setattr(ai_workspace, "append_audit", append_audit)
        monkeypatch.setattr(
            ai_budget,
            "get_settings",
            lambda: SimpleNamespace(
                ai_disabled=False,
                ai_daily_budget_usd=1.0,
                ai_monthly_budget_usd=1.0,
            ),
        )
        try:
            async with open_db_session() as db:
                db.add(
                    Tenant(
                        id=tenant_id,
                        name="AI budget integration",
                        slug=f"ai-budget-{tenant_id.hex}",
                        branding={},
                        quotas={},
                    )
                )
                await db.commit()

            gate = asyncio.Event()

            async def reserve() -> object:
                async with open_db_session() as db:
                    await gate.wait()
                    return await ai_workspace._reserve_ai_run(
                        db=db,
                        auth=auth,
                        project_id=None,
                        provider_id="test-provider",
                        model_id="test-model",
                        prompt=prompt,
                        snapshot={"test": True},
                        estimated_cost_usd=0.6,
                        action="test.budget-reservation",
                    )

            first = asyncio.create_task(reserve())
            second = asyncio.create_task(reserve())
            gate.set()
            results = await asyncio.gather(first, second, return_exceptions=True)

            successes = [item for item in results if not isinstance(item, BaseException)]
            failures = [item for item in results if isinstance(item, BaseException)]
            assert len(successes) == 1
            assert len(failures) == 1
            assert isinstance(failures[0], HTTPException)
            assert failures[0].detail["code"] == "ai_daily_budget_exceeded"

            async with open_db_session() as db:
                total = float(
                    (
                        await db.execute(
                            select(ai_workspace.AIRun.cost_usd).where(
                                ai_workspace.AIRun.tenant_id == tenant_id
                            )
                        )
                    )
                    .scalars()
                    .one()
                )
                assert total == 0.6
        finally:
            try:
                async with open_db_session() as db:
                    await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
                    await db.commit()
            finally:
                await engine.dispose()

    asyncio.run(run())
