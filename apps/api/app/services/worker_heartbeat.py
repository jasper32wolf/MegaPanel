from __future__ import annotations

from app.models.worker_heartbeat import WorkerHeartbeat
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

WORKER_KEY = "arq"


async def record_worker_heartbeat(session: AsyncSession) -> None:
    statement = (
        insert(WorkerHeartbeat)
        .values(worker_key=WORKER_KEY)
        .on_conflict_do_update(
            index_elements=[WorkerHeartbeat.worker_key],
            set_={"last_seen_at": func.now()},
        )
    )
    await session.execute(statement)
    await session.commit()
