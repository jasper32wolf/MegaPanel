from __future__ import annotations

from uuid import UUID

from app.core.config import get_settings
from arq import create_pool
from arq.connections import RedisSettings


async def enqueue_ai_run(run_id: UUID) -> None:
    """Enqueue only the durable run identifier; all request data lives in the DB."""
    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        await pool.enqueue_job("architecture_proposal_task", str(run_id))
    finally:
        await pool.aclose()


__all__ = ["enqueue_ai_run"]
