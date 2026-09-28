from __future__ import annotations

import uuid

import structlog
from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.db.session import open_db_session
from app.services.webhook_delivery import due_delivery_ids, process_delivery, recover_expired_leases
from app.services.worker_heartbeat import record_worker_heartbeat

logger = structlog.get_logger("worker")
settings = get_settings()


async def healthcheck_task(ctx: dict) -> dict:
    logger.info("worker_healthcheck")
    return {"ok": True}


async def worker_heartbeat_task(ctx: dict) -> dict:
    async with open_db_session() as session:
        await record_worker_heartbeat(session)
    logger.info("worker_heartbeat_recorded")
    return {"ok": True}


async def webhook_delivery_task(ctx: dict, delivery_id: str, trigger: str = "automatic") -> dict:
    try:
        async with open_db_session() as session:
            result = await process_delivery(session, uuid.UUID(delivery_id), trigger=trigger)
        logger.info("webhook_delivery_processed", delivery_id=delivery_id, status=result["status"])
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("webhook_delivery_failed", delivery_id=delivery_id, error=str(exc))
        return {"error": str(exc)}


async def webhook_delivery_sweep_task(ctx: dict) -> dict:
    try:
        async with open_db_session() as session:
            recovered = await recover_expired_leases(session)
            ids = await due_delivery_ids(session)
        results = []
        for delivery_id in ids:
            async with open_db_session() as session:
                results.append(await process_delivery(session, delivery_id))
        logger.info("webhook_delivery_sweep", recovered=recovered, processed=len(results))
        return {"recovered": recovered, "processed": len(results)}
    except Exception as exc:  # noqa: BLE001
        logger.error("webhook_delivery_sweep_failed", error=str(exc))
        return {"error": str(exc)}


class WorkerSettings:
    functions = [
        healthcheck_task,
        worker_heartbeat_task,
        webhook_delivery_task,
        webhook_delivery_sweep_task,
    ]
    cron_jobs = [
        cron(
            worker_heartbeat_task,
            second=set(range(0, 60, settings.worker_heartbeat_interval_seconds)),
            run_at_startup=True,
        ),
        cron(webhook_delivery_sweep_task, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 600
    health_check_interval = 30
