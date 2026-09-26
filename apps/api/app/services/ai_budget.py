from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.config import get_settings
from app.models import AIRun
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession


def _window_start(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


async def enforce_ai_budget(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    estimated_cost_usd: float,
) -> None:
    settings = get_settings()
    if settings.ai_disabled:
        raise HTTPException(status_code=503, detail={"code": "ai_disabled"})
    rows = await db.execute(
        select(func.coalesce(func.sum(AIRun.cost_usd), 0.0)).where(
            AIRun.tenant_id == tenant_id,
            AIRun.created_at >= _window_start(1),
            AIRun.cost_usd.is_not(None),
        )
    )
    daily_total = float(rows.scalar_one() or 0)
    if daily_total + estimated_cost_usd > settings.ai_daily_budget_usd:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "ai_daily_budget_exceeded",
                "daily_total_usd": round(daily_total, 8),
                "estimated_cost_usd": round(estimated_cost_usd, 8),
                "limit_usd": settings.ai_daily_budget_usd,
            },
        )
    rows = await db.execute(
        select(func.coalesce(func.sum(AIRun.cost_usd), 0.0)).where(
            AIRun.tenant_id == tenant_id,
            AIRun.created_at >= _window_start(30),
            AIRun.cost_usd.is_not(None),
        )
    )
    monthly_total = float(rows.scalar_one() or 0)
    if monthly_total + estimated_cost_usd > settings.ai_monthly_budget_usd:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "ai_monthly_budget_exceeded",
                "monthly_total_usd": round(monthly_total, 8),
                "estimated_cost_usd": round(estimated_cost_usd, 8),
                "limit_usd": settings.ai_monthly_budget_usd,
            },
        )


async def reserve_ai_budget(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    estimated_cost_usd: float,
) -> None:
    bind = db.get_bind() if hasattr(db, "get_bind") else None
    if bind is not None and bind.dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:tenant_id))"),
            {"tenant_id": str(tenant_id)},
        )
    await enforce_ai_budget(
        db,
        tenant_id=tenant_id,
        estimated_cost_usd=estimated_cost_usd,
    )


__all__ = ["enforce_ai_budget", "reserve_ai_budget"]
