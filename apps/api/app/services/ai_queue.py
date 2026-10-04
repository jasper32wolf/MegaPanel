from __future__ import annotations

from uuid import UUID

from app.core.config import get_settings
from arq import create_pool
from arq.connections import RedisSettings


async def _enqueue_ai_run(run_id: UUID, task_name: str) -> None:
    """Enqueue only the durable run identifier; all request data lives in the DB."""
    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        await pool.enqueue_job(task_name, str(run_id))
    finally:
        await pool.aclose()


async def enqueue_ai_run(run_id: UUID) -> None:
    await _enqueue_ai_run(run_id, "architecture_proposal_task")


async def enqueue_intent_generation_run(run_id: UUID) -> None:
    await _enqueue_ai_run(run_id, "intent_page_proposal_task")


__all__ = ["enqueue_ai_run", "enqueue_intent_generation_run"]
