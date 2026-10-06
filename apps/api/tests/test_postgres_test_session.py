from __future__ import annotations

import asyncio

import postgres_test_session as postgres_session
from sqlalchemy.pool import NullPool


def test_postgres_test_sessions_use_independent_unpooled_engines(monkeypatch):
    engines = []
    bypasses = []

    class FakeEngine:
        disposed = False

        async def dispose(self):
            self.disposed = True

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    def engine_factory(_url, *, poolclass):
        assert poolclass is NullPool
        engine = FakeEngine()
        engines.append(engine)
        return engine

    def session_factory(_engine, *, expire_on_commit):
        assert not expire_on_commit
        return FakeSession

    async def record_rls(session, tenant_id, *, bypass):
        bypasses.append((session, tenant_id, bypass))

    monkeypatch.setattr(postgres_session, "create_async_engine", engine_factory)
    monkeypatch.setattr(postgres_session, "async_sessionmaker", session_factory)
    monkeypatch.setattr(postgres_session, "set_tenant_rls", record_rls)

    async def use_session():
        async with postgres_session.isolated_db_session() as db:
            assert isinstance(db, FakeSession)

    asyncio.run(use_session())
    asyncio.run(use_session())

    assert len(engines) == 2
    assert engines[0] is not engines[1]
    assert all(engine.disposed for engine in engines)
    assert len(bypasses) == 2
    assert all(tenant_id is None and bypass for _, tenant_id, bypass in bypasses)
