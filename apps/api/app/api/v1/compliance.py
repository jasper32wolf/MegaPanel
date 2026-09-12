from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.services.hardening import egress, finops, finops_summary, record_finops

router = APIRouter()

_PROJECT_ROOT = Path(__file__).resolve().parents[5]


class EgressCheck(BaseModel):
    url: str


class FinOpsEntry(BaseModel):
    kind: str
    amount_usd: float
    meta: dict | None = None


@router.get("/security.txt", response_class=PlainTextResponse)
async def security_txt() -> str:
    path = _PROJECT_ROOT / "docs" / "security" / "security.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "Contact: security@example.com\nPreferred-Languages: ru, en\n"


@router.get("/legal/subprocessors")
async def subprocessors() -> dict:
    return {
        "subprocessors": [
            {"name": "VPS Hosting", "purpose": "compute/storage", "region": "RF"},
            {"name": "LLM Provider", "purpose": "content generation", "region": "per DPA"},
            {"name": "Email SMTP", "purpose": "transactional mail", "region": "RF preferred"},
        ],
        "note": "Public registry — update before production",
    }


@router.post("/egress/check")
async def egress_check(
    body: EgressCheck,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin")),
) -> dict:
    return {"url": body.url, "allowed": egress.check(body.url)}


@router.post("/finops")
async def add_finops(
    body: FinOpsEntry,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    tid = auth.tenant_id or auth.user.id
    await record_finops(db, tid, body.kind, body.amount_usd, body.meta)
    await db.commit()
    return await finops_summary(db, tid)


@router.get("/finops")
async def get_finops(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    tid = auth.tenant_id or auth.user.id
    try:
        return await finops_summary(db, tid)
    except Exception:  # noqa: BLE001 — table may be missing pre-migration
        return {
            "tenant_id": str(tid),
            "total_usd": finops.total_for(str(tid)),
            "by_kind": finops.by_kind(str(tid)),
            "alerts": ["llm_spike"] if finops.by_kind(str(tid)).get("llm", 0) > 100 else [],
        }


@router.get("/dr/status")
async def disaster_recovery_status(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin")),
) -> dict:
    return {
        "manifest_rebuild": "supported",
        "pitr": "configure PostgreSQL WAL",
        "one_click": False,
        "runbook": "docs/runbooks/disaster-recovery.md",
        "atomic_builds": True,
        "rollback": "POST /api/v1/sites/{id}/rollback",
    }
