from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models import Keyword
from app.schemas.common import KeywordImportRequest
from app.services.audit import append_audit

router = APIRouter()

_WS = re.compile(r"\s+")


def normalize_phrase(phrase: str) -> str:
    return _WS.sub(" ", phrase.strip().lower())


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def import_keywords(
    body: KeywordImportRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    tenant_id: UUID = auth.tenant_id
    seen: set[str] = set()
    created = 0
    skipped = 0
    for item in body.items:
        norm = normalize_phrase(item.phrase)
        if not norm or norm in seen:
            skipped += 1
            continue
        seen.add(norm)
        db.add(
            Keyword(
                tenant_id=tenant_id,
                phrase=item.phrase.strip(),
                normalized=norm,
                category=item.category,
            )
        )
        created += 1
    await append_audit(
        db,
        action="keywords.import",
        payload={"created": created, "skipped": skipped},
        tenant_id=tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"created": created, "skipped": skipped}
