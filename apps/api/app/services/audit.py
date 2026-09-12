from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import sha256_hex
from app.models import AuditLog


async def append_audit(
    session: AsyncSession,
    *,
    action: str,
    payload: dict[str, Any] | None = None,
    tenant_id: UUID | None = None,
    actor_id: UUID | None = None,
) -> AuditLog:
    """Single-threaded hash-chained audit log (TZ 12.4)."""
    result = await session.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1))
    last = result.scalar_one_or_none()
    prev_hash = last.record_hash if last else "0" * 64
    body = {
        "action": action,
        "payload": payload or {},
        "tenant_id": str(tenant_id) if tenant_id else None,
        "actor_id": str(actor_id) if actor_id else None,
        "prev_hash": prev_hash,
    }
    record_hash = sha256_hex(json.dumps(body, sort_keys=True, ensure_ascii=False))
    entry = AuditLog(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        payload=payload or {},
        record_hash=record_hash,
        prev_hash=prev_hash,
    )
    session.add(entry)
    await session.flush()
    return entry
