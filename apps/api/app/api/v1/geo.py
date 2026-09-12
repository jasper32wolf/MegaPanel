from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models import GeoPlace
from app.schemas.phase2 import GeoCreate, GeoOut, MorphOut, MorphRequest, ToponymValidateRequest
from app.services.audit import append_audit
from app.services.geo import place_context, seed_demo_geo, upsert_place, validate_toponym
from app.services.morph import city_placeholders, inflect_cases

router = APIRouter()


@router.post("", response_model=GeoOut, status_code=201)
async def create_geo(
    body: GeoCreate,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> GeoPlace:
    place = await upsert_place(
        db,
        kind=body.kind,
        name=body.name,
        parent_id=body.parent_id,
        external_id=body.external_id,
        source="manual",
        lat=body.lat,
        lon=body.lon,
        timezone=body.timezone,
        population=body.population,
        attrs=body.attrs,
    )
    await append_audit(
        db,
        action="geo.create",
        payload={"kind": place.kind, "name": place.name},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(place)
    return place


@router.get("", response_model=list[GeoOut])
async def list_geo(
    kind: str | None = None,
    q: str | None = Query(None, min_length=1),
    parent_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(require_roles(
        "superadmin", "tenant_admin", "manager", "editor", "viewer"
    )),
    db: AsyncSession = Depends(get_db),
) -> list[GeoPlace]:
    stmt = select(GeoPlace).order_by(GeoPlace.name).limit(limit)
    if kind:
        stmt = stmt.where(GeoPlace.kind == kind)
    if q:
        stmt = stmt.where(GeoPlace.name.ilike(f"%{q}%"))
    if parent_id:
        from uuid import UUID

        stmt = stmt.where(GeoPlace.parent_id == UUID(parent_id))
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/validate")
async def validate_geo(
    body: ToponymValidateRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    place = await validate_toponym(db, body.name, body.kind)
    if place:
        return {
            "valid": True,
            "id": str(place.id),
            "name": place.name,
            "forms": place.name_forms,
            "placeholders": place_context(place),
            "source": "local",
        }
    # Enrichment via Nominatim when missing from local geo (TZ 3.1)
    try:
        from app.services.nominatim import NominatimClient

        hits = await NominatimClient().search(body.name)
    except Exception:  # noqa: BLE001
        hits = []
    if hits:
        hit = hits[0]
        return {
            "valid": False,
            "name": body.name,
            "message": "Toponym not in geo reference — Nominatim suggestion available",
            "nominatim": {
                "display_name": hit.get("display_name"),
                "lat": hit.get("lat"),
                "lon": hit.get("lon"),
                "osm_type": hit.get("osm_type"),
                "osm_id": hit.get("osm_id"),
            },
        }
    return {"valid": False, "name": body.name, "message": "Toponym not in geo reference"}


@router.post("/morph", response_model=MorphOut)
async def morph_word(
    body: MorphRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
) -> MorphOut:
    forms = inflect_cases(body.word)
    return MorphOut(word=body.word, forms=forms, placeholders=city_placeholders(body.word, forms))


@router.post("/seed-demo")
async def seed_geo(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    created = await seed_demo_geo(db)
    await append_audit(
        db,
        action="geo.seed_demo",
        payload=created,
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return created
