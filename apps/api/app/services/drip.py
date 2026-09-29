"""Legacy index drip hooks retained without automatic promotion."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


async def promote_drip(
    session: AsyncSession,
    *,
    tenant_id: UUID | None = None,
    limit: int = 40,
) -> dict:
    del session, tenant_id, limit
    return {"promoted": 0, "urls": []}


async def queue_for_index(session: AsyncSession, page_ids: list[UUID]) -> int:
    del session, page_ids
    return 0
