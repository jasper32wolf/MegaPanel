"""PostgreSQL test sessions without pooled connections across asyncio.run loops."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from app.core.config import get_settings
from app.db.rls import set_tenant_rls
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


@asynccontextmanager
async def isolated_db_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await set_tenant_rls(session, None, bypass=True)
            yield session
    finally:
        await engine.dispose()
