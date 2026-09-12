from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models import Tenant
from app.schemas.common import TenantCreate, TenantOut
from app.services.audit import append_audit

router = APIRouter()


@router.post("", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    body: TenantCreate,
    auth: AuthContext = Depends(require_roles("superadmin")),
    db: AsyncSession = Depends(get_db),
) -> Tenant:
    existing = await db.execute(select(Tenant).where(Tenant.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Slug taken")
    tenant = Tenant(
        name=body.name,
        slug=body.slug,
        quotas={
            "pages": 10000,
            "llm_tokens": 1_000_000,
            "leads": 10000,
            "domains": 100,
        },
        branding={},
    )
    db.add(tenant)
    await db.flush()
    await append_audit(
        db,
        action="tenant.create",
        payload={"slug": tenant.slug, "name": tenant.name},
        tenant_id=tenant.id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(tenant)
    return tenant


@router.get("", response_model=list[TenantOut])
async def list_tenants(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin")),
    db: AsyncSession = Depends(get_db),
) -> list[Tenant]:
    stmt = select(Tenant).order_by(Tenant.created_at.desc())
    if auth.role != "superadmin" and auth.tenant_id:
        stmt = stmt.where(Tenant.id == auth.tenant_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())
