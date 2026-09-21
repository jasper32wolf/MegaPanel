from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from app.core.security import sha256_hex
from app.models import AuditLog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

ZERO_HASH = "0" * 64


def audit_record_hash(
    *,
    action: str,
    payload: dict[str, Any],
    tenant_id: UUID | None,
    actor_id: UUID | None,
    prev_hash: str,
) -> str:
    body = {
        "action": action,
        "payload": payload,
        "tenant_id": str(tenant_id) if tenant_id else None,
        "actor_id": str(actor_id) if actor_id else None,
        "prev_hash": prev_hash,
    }
    return sha256_hex(json.dumps(body, sort_keys=True, ensure_ascii=False))


async def append_audit(
    session: AsyncSession,
    *,
    action: str,
    payload: dict[str, Any] | None = None,
    tenant_id: UUID | None = None,
    actor_id: UUID | None = None,
) -> AuditLog:
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('site_panel_audit_log'))"))
    result = await session.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1))
    last = result.scalar_one_or_none()
    previous = last.record_hash if last else ZERO_HASH
    record_payload = payload or {}
    entry = AuditLog(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        payload=record_payload,
        record_hash=audit_record_hash(
            action=action,
            payload=record_payload,
            tenant_id=tenant_id,
            actor_id=actor_id,
            prev_hash=previous,
        ),
        prev_hash=previous,
    )
    session.add(entry)
    await session.flush()
    return entry


def verify_audit_chain(entries: list[AuditLog]) -> list[int]:
    previous = ZERO_HASH
    invalid: list[int] = []
    for entry in entries:
        expected = audit_record_hash(
            action=entry.action,
            payload=entry.payload or {},
            tenant_id=entry.tenant_id,
            actor_id=entry.actor_id,
            prev_hash=previous,
        )
        if entry.prev_hash != previous or entry.record_hash != expected:
            invalid.append(entry.id)
        previous = entry.record_hash
    return invalid
