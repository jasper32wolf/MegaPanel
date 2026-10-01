from __future__ import annotations

from uuid import UUID

from app.core.config import get_settings
from arq import create_pool
from arq.connections import RedisSettings


async def enqueue_competitor_crawl(crawl_id: UUID) -> None:
    """Enqueue only the durable crawl identifier; URLs remain in the database."""
    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        await pool.enqueue_job("competitor_crawl_task", str(crawl_id))
    finally:
        await pool.aclose()
