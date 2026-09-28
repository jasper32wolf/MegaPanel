from __future__ import annotations

import asyncio

from app.models.worker_heartbeat import WorkerHeartbeat
from app.services.worker_heartbeat import record_worker_heartbeat
from sqlalchemy.dialects import postgresql


class Session:
    def __init__(self):
        self.statements: list[object] = []
        self.committed = False

    async def execute(self, statement: object) -> None:
        self.statements.append(statement)

    async def commit(self) -> None:
        self.committed = True


def test_worker_heartbeat_is_global_and_uses_atomic_upsert():
    session = Session()

    asyncio.run(record_worker_heartbeat(session))

    assert {column.name for column in WorkerHeartbeat.__table__.columns} == {
        "worker_key",
        "last_seen_at",
    }
    assert session.committed is True
    statement = session.statements[0].compile(dialect=postgresql.dialect())
    assert "INSERT INTO worker_heartbeats" in str(statement)
    assert "ON CONFLICT (worker_key) DO UPDATE" in str(statement)
    assert "last_seen_at = now()" in str(statement)
