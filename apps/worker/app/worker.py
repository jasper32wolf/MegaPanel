from __future__ import annotations

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.tasks.demo import drip_promote_task, dsar_process_task, healthcheck_task

settings = get_settings()


class WorkerSettings:
    functions = [healthcheck_task, drip_promote_task, dsar_process_task]
    cron_jobs = [
        cron(drip_promote_task, hour=3, minute=0),
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 600
