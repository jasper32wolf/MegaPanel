"""DSAR export/delete processing (TZ 12.5)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.models.leads import Consent, Lead
from app.services.leads import get_blind, get_encryptor
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _matches_subject(
    lead: Lead,
    encryptor: Any,
    subject_email: str | None,
    phone_idx: str | None,
) -> bool:
    if subject_email and lead.email_enc:
        try:
            if encryptor.decrypt(lead.email_enc).lower() == subject_email.lower():
                return True
        except Exception:  # noqa: BLE001
            pass
    return bool(phone_idx and lead.phone_blind == phone_idx)


async def process_dsar_job(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    action: str,
    subject_email: str | None = None,
    subject_phone: str | None = None,
    job_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    enc = get_encryptor()
    blind = get_blind()
    phone_idx = blind.index(subject_phone) if subject_phone else None

    stmt = select(Lead).where(Lead.tenant_id == tenant_id)
    if phone_idx and not subject_email:
        stmt = stmt.where(Lead.phone_blind == phone_idx)
    leads = list((await session.execute(stmt)).scalars().all())

    matched = [lead for lead in leads if _matches_subject(lead, enc, subject_email, phone_idx)]

    # Deduplicate
    seen: set[uuid.UUID] = set()
    unique: list[Lead] = []
    for lead in matched:
        if lead.id not in seen:
            seen.add(lead.id)
            unique.append(lead)

    consent_visitor_ids = [blind.index(lead.idempotency_key or str(lead.id)) for lead in unique]
    consents = []
    if consent_visitor_ids:
        consents = list(
            (
                await session.execute(
                    select(Consent).where(
                        Consent.tenant_id == tenant_id,
                        Consent.visitor_id.in_(consent_visitor_ids),
                        Consent.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )

    if action == "export":
        rows = []
        for lead in unique:
            rows.append(
                {
                    "id": str(lead.id),
                    "site_id": str(lead.site_id),
                    "page_slug": lead.page_slug,
                    "status": lead.status,
                    "phone": enc.decrypt(lead.phone_enc) if lead.phone_enc else None,
                    "email": enc.decrypt(lead.email_enc) if lead.email_enc else None,
                    "name": enc.decrypt(lead.name_enc) if lead.name_enc else None,
                    "message": lead.message,
                    "created_at": lead.created_at.isoformat() if lead.created_at else None,
                }
            )
        settings = get_settings()
        out_dir = Path(settings.dsar_exports_root) / str(tenant_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{job_id or uuid.uuid4()}.json"
        out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "action": "export",
            "count": len(rows),
            "active_consents": len(consents),
            "path": str(out_path),
        }

    if action == "delete":
        deleted = 0
        for lead in unique:
            lead.phone_enc = None
            lead.email_enc = None
            lead.name_enc = None
            lead.message = None
            lead.phone_blind = None
            lead.status = "erased"
            lead.meta = {**(lead.meta or {}), "dsar_erased_at": datetime.now(UTC).isoformat()}
            deleted += 1
        revoked_at = datetime.now(UTC)
        for consent in consents:
            consent.revoked_at = revoked_at
        return {
            "action": "delete",
            "leads_erased": deleted,
            "consents_revoked": len(consents),
        }

    raise ValueError("Unknown DSAR action")


def purge_expired_exports(now: datetime | None = None) -> int:
    settings = get_settings()
    root = Path(settings.dsar_exports_root)
    if not root.exists():
        return 0
    cutoff = (now or datetime.now(UTC)).timestamp() - settings.dsar_export_ttl_hours * 3600
    deleted = 0
    for path in root.rglob("*.json"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink()
            deleted += 1
    return deleted
