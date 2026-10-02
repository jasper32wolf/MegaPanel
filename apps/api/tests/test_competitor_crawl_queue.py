from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from app.services import research_queue
from app.worker import WorkerSettings, competitor_crawl_task


def test_crawler_queue_enqueues_only_durable_id_and_closes_pool(monkeypatch):
    crawl_id = uuid4()
    calls: list[tuple[str, str]] = []

    class Pool:
        closed = False

        async def enqueue_job(self, name: str, value: str):
            calls.append((name, value))

        async def aclose(self):
            self.closed = True

    pool = Pool()

    async def create_pool_stub(_settings):
        return pool

    monkeypatch.setattr(research_queue, "create_pool", create_pool_stub)

    asyncio.run(research_queue.enqueue_competitor_crawl(crawl_id))

    assert calls == [("competitor_crawl_task", str(crawl_id))]
    assert pool.closed is True


def test_crawler_queue_closes_pool_when_enqueue_fails(monkeypatch):
    class Pool:
        closed = False

        async def enqueue_job(self, _name: str, _value: str):
            raise RuntimeError("redis unavailable")

        async def aclose(self):
            self.closed = True

    pool = Pool()

    async def create_pool_stub(_settings):
        return pool

    monkeypatch.setattr(research_queue, "create_pool", create_pool_stub)

    with pytest.raises(RuntimeError, match="redis unavailable"):
        asyncio.run(research_queue.enqueue_competitor_crawl(uuid4()))
    assert pool.closed is True


def test_competitor_crawl_worker_is_registered_and_uses_controlled_runner(monkeypatch):
    crawl_id = uuid4()
    received: list[object] = []

    @asynccontextmanager
    async def session_stub():
        yield object()

    async def runner_stub(session, received_crawl_id):
        received.extend([session, received_crawl_id])
        return {"status": "done", "pages": 1}

    monkeypatch.setattr("app.worker.open_db_session", session_stub)
    monkeypatch.setattr("app.worker.run_competitor_crawl", runner_stub)

    result = asyncio.run(competitor_crawl_task({}, str(crawl_id)))

    assert competitor_crawl_task in WorkerSettings.functions
    assert result == {"status": "done", "pages": 1}
    assert received[1] == crawl_id
