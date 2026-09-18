from __future__ import annotations

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.services.audit import append_audit
from app.services.block_library import preview_kit_html, sync_library_to_tenant
from fastapi import APIRouter, Depends, HTTPException
from site_panel_blocks import library_version, list_kits
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("/kits")
async def get_kits(
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
    ),
) -> dict:
    return {"library_version": library_version(), "kits": list_kits()}


@router.get("/kits/{key}/preview")
async def preview_kit(
    key: str,
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
    ),
) -> dict:
    try:
        return preview_kit_html(key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/kits/{key}/sync")
async def sync_kit(
    key: str,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    try:
        result = await sync_library_to_tenant(db, auth.tenant_id, key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await append_audit(
        db,
        action="blocks.kit_sync",
        payload=result,
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return result
