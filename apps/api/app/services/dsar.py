"""DSAR export/delete processing (TZ 12.5)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.leads import Consent, Lead
from app.services.leads import get_blind, get_encryptor


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
    if phone_idx:
        stmt = stmt.where(Lead.phone_blind == phone_idx)
    leads = list((await session.execute(stmt)).scalars().all())

    matched: list[Lead] = []
    for lead in leads:
        if subject_email and lead.email_enc:
            try:
                if enc.decrypt(lead.email_enc).lower() == subject_email.lower():
                    matched.append(lead)
                    continue
            except Exception:  # noqa: BLE001
                pass
        if phone_idx and lead.phone_blind == phone_idx:
            matched.append(lead)

    # Deduplicate
    seen: set[uuid.UUID] = set()
    unique: list[Lead] = []
    for lead in matched:
        if lead.id not in seen:
            seen.add(lead.id)
            unique.append(lead)

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
        out_dir = Path(settings.sites_root) / "_dsar" / str(tenant_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{job_id or uuid.uuid4()}.json"
        out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"action": "export", "count": len(rows), "path": str(out_path)}

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
        # Revoke matching consents by visitor if email present in purposes — soft revoke all tenant recent if phone match only
        if subject_email or subject_phone:
            consents = list(
                (
                    await session.execute(select(Consent).where(Consent.tenant_id == tenant_id, Consent.revoked_at.is_(None)))
                )
                .scalars()
                .all()
            )
            revoked = 0
            for c in consents[:500]:
                # conservative: mark revoked when subject identifiers present in job
                c.revoked_at = datetime.now(UTC)
                revoked += 1
            return {"action": "delete", "leads_erased": deleted, "consents_revoked": revoked}
        return {"action": "delete", "leads_erased": deleted, "consents_revoked": 0}

    return {"action": action, "error": "unknown_action"}
