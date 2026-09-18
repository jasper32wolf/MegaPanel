from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings
from app.db.rls import set_tenant_rls


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def open_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        await set_tenant_rls(session, None, bypass=True)
        yield session


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with open_db_session() as session:
        yield session
