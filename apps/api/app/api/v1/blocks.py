from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models import ContentBlock, make_hash_class
from app.schemas.phase3 import BlockCreate, BlockOut
from app.services.audit import append_audit
from app.services.block_library import preview_kit_html, sync_library_to_tenant, upsert_global_kits
from site_panel_blocks import library_version, list_kits
from site_panel_security import sanitize_html

router = APIRouter()


@router.get("/kits")
async def get_kits(
    auth: AuthContext = Depends(require_roles(
        "superadmin", "tenant_admin", "manager", "editor", "viewer"
    )),
) -> dict:
    return {"library_version": library_version(), "kits": list_kits()}


@router.get("/kits/{key}/preview")
async def preview_kit(
    key: str,
    auth: AuthContext = Depends(require_roles(
        "superadmin", "tenant_admin", "manager", "editor", "viewer"
    )),
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


@router.post("/kits/register")
async def register_kits(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    keys = await upsert_global_kits(db)
    await db.commit()
    return {"keys": keys, "library_version": library_version()}


@router.post("", response_model=BlockOut, status_code=201)
async def create_block(
    body: BlockCreate,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> ContentBlock:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    clean = sanitize_html(body.html)
    if not clean.strip():
        raise HTTPException(status_code=400, detail="HTML empty after sanitization")
    block = ContentBlock(
        tenant_id=auth.tenant_id,
        type=body.type,
        name=body.name,
        hash_class=make_hash_class(body.type, clean),
        html=clean,
        css=body.css,
        props=body.props,
        source="tenant",
    )
    db.add(block)
    await append_audit(
        db,
        action="block.create",
        payload={"type": block.type, "hash_class": block.hash_class},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(block)
    return block


@router.get("", response_model=list[BlockOut])
async def list_blocks(
    auth: AuthContext = Depends(require_roles(
        "superadmin", "tenant_admin", "manager", "editor", "viewer"
    )),
    db: AsyncSession = Depends(get_db),
) -> list[ContentBlock]:
    if not auth.tenant_id and auth.role != "superadmin":
        raise HTTPException(status_code=403, detail="Tenant required")
    stmt = select(ContentBlock).where(ContentBlock.is_active.is_(True)).order_by(ContentBlock.created_at.desc())
    if auth.role != "superadmin":
        stmt = stmt.where(ContentBlock.tenant_id == auth.tenant_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/seed-defaults", response_model=list[BlockOut])
async def seed_defaults(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> list[ContentBlock]:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    await sync_library_to_tenant(db, auth.tenant_id, "service-local-v1")
    await db.commit()
    stmt = select(ContentBlock).where(
        ContentBlock.tenant_id == auth.tenant_id,
        ContentBlock.is_active.is_(True),
        ContentBlock.source == "library",
    )
    return list((await db.execute(stmt)).scalars().all())
