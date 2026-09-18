from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def set_tenant_rls(session: AsyncSession, tenant_id: str | None, *, bypass: bool = False) -> None:
    """Set PostgreSQL session vars used by RLS policies (migration 0002)."""
    if bypass:
        await session.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
        return
    if tenant_id:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": tenant_id},
        )
