from __future__ import annotations

from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models import TaxonomyCategory
from app.schemas.phase2 import TaxonomyCreate, TaxonomyOut
from app.services.audit import append_audit
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.post("", response_model=TaxonomyOut, status_code=201)
async def create_category(
    body: TaxonomyCreate,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> TaxonomyCategory:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    existing = await db.execute(
        select(TaxonomyCategory).where(
            TaxonomyCategory.tenant_id == auth.tenant_id,
            TaxonomyCategory.slug == body.slug,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Slug exists")
    cat = TaxonomyCategory(
        tenant_id=auth.tenant_id,
        niche=body.niche,
        slug=body.slug,
        service=body.service,
        modifier=body.modifier,
        method=body.method,
        attrs=body.attrs,
        templates=body.templates
        or {
            "title": "{service} {modifier} в {city_prep}",
            "h1": "{service} {modifier} в {city_prep}",
            "meta": "Закажите {service} в {city_prep}. Тел: {phone}",
        },
    )
    db.add(cat)
    await append_audit(
        db,
        action="taxonomy.create",
        payload={"slug": cat.slug, "niche": cat.niche},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(cat)
    return cat


@router.get("", response_model=list[TaxonomyOut])
async def list_categories(
    niche: str | None = None,
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
    ),
    db: AsyncSession = Depends(get_db),
) -> list[TaxonomyCategory]:
    if not auth.tenant_id and auth.role != "superadmin":
        raise HTTPException(status_code=403, detail="Tenant required")
    stmt = select(TaxonomyCategory).order_by(TaxonomyCategory.created_at.desc())
    if auth.role != "superadmin":
        stmt = stmt.where(TaxonomyCategory.tenant_id == auth.tenant_id)
    if niche:
        stmt = stmt.where(TaxonomyCategory.niche == niche)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{category_id}", response_model=TaxonomyOut)
async def get_category(
    category_id: UUID,
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
    ),
    db: AsyncSession = Depends(get_db),
) -> TaxonomyCategory:
    result = await db.execute(select(TaxonomyCategory).where(TaxonomyCategory.id == category_id))
    cat = result.scalar_one_or_none()
    if not cat:
        raise HTTPException(status_code=404, detail="Not found")
    if auth.role != "superadmin" and cat.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return cat
