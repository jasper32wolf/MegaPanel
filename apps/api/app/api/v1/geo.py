from __future__ import annotations

import csv
import io

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models import GeoPlace
from app.schemas.phase2 import GeoCreate, GeoOut, MorphOut, MorphRequest, ToponymValidateRequest
from app.services.audit import append_audit
from app.services.geo import place_context, upsert_place, validate_toponym
from app.services.morph import city_placeholders, inflect_cases
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()

GEO_KINDS = {"country", "region", "city", "district", "street", "metro", "landmark"}
MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_IMPORT_ROWS = 50_000
TEMPLATE = (
    "kind,external_id,name,parent_external_id,lat,lon,timezone,population\n"
    "city,manual-moscow,Москва,,55.7558,37.6173,Europe/Moscow,12600000\n"
    "district,manual-moscow-sokol,Сокол,manual-moscow,,,,\n"
)


def parse_geo_csv(raw: bytes, *, delimiter: str) -> tuple[list[dict], list[dict]]:
    if len(raw) > MAX_IMPORT_BYTES:
        raise ValueError("File exceeds 10 MB")
    if delimiter not in {",", ";", "\t"}:
        raise ValueError("Unsupported CSV delimiter")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV must be UTF-8 encoded") from exc

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    required = {"kind", "external_id", "name"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError("CSV must include kind, external_id, and name columns")

    rows: list[dict] = []
    errors: list[dict] = []
    for line, source in enumerate(reader, start=2):
        if line > MAX_IMPORT_ROWS + 1:
            raise ValueError(f"CSV exceeds {MAX_IMPORT_ROWS} rows")
        kind = (source.get("kind") or "").strip().lower()
        external_id = (source.get("external_id") or "").strip()
        name = (source.get("name") or "").strip()
        if kind not in GEO_KINDS or not external_id or not name:
            if len(errors) < 100:
                errors.append({"line": line, "error": "kind, external_id, and name are required"})
            continue
        try:
            rows.append(
                {
                    "kind": kind,
                    "line": line,
                    "external_id": external_id,
                    "name": name,
                    "parent_external_id": (source.get("parent_external_id") or "").strip() or None,
                    "lat": float(source["lat"]) if (source.get("lat") or "").strip() else None,
                    "lon": float(source["lon"]) if (source.get("lon") or "").strip() else None,
                    "timezone": (source.get("timezone") or "").strip() or None,
                    "population": int(source["population"])
                    if (source.get("population") or "").strip()
                    else None,
                }
            )
        except ValueError:
            if len(errors) < 100:
                errors.append({"line": line, "error": "Invalid coordinate or population"})
    return rows, errors


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


@router.get("/template.csv", response_class=PlainTextResponse)
async def geo_template(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
) -> PlainTextResponse:
    return PlainTextResponse(
        TEMPLATE,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=site-panel-geo-template.csv"},
    )


@router.post("/import-csv", status_code=201)
async def import_geo_csv(
    file: UploadFile = File(...),
    delimiter: str = Form(","),
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    try:
        rows, errors = parse_geo_csv(raw, delimiter=delimiter)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    known = {
        row.external_id: row.id
        for row in (await db.execute(select(GeoPlace).where(GeoPlace.external_id.is_not(None))))
        .scalars()
        .all()
    }
    created_or_updated = 0
    for row in rows:
        parent_external_id = row["parent_external_id"]
        parent_id = known.get(parent_external_id) if parent_external_id else None
        if parent_external_id and parent_id is None:
            if len(errors) < 100:
                errors.append(
                    {
                        "line": row["line"],
                        "error": f"Unknown parent_external_id: {parent_external_id}",
                    }
                )
            continue
        place = await upsert_place(
            db,
            kind=row["kind"],
            name=row["name"],
            external_id=row["external_id"],
            parent_id=parent_id,
            source="csv",
            lat=row["lat"],
            lon=row["lon"],
            timezone=row["timezone"],
            population=row["population"],
        )
        known[row["external_id"]] = place.id
        created_or_updated += 1

    await append_audit(
        db,
        action="geo.import_csv",
        payload={"created_or_updated": created_or_updated, "invalid_rows": len(errors)},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"created_or_updated": created_or_updated, "invalid_rows": len(errors), "errors": errors}


@router.get("", response_model=list[GeoOut])
async def list_geo(
    kind: str | None = None,
    q: str | None = Query(None, min_length=1),
    parent_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
    ),
    db: AsyncSession = Depends(get_db),
) -> list[GeoPlace]:
    statement = select(GeoPlace).order_by(GeoPlace.name).limit(limit)
    if kind:
        statement = statement.where(GeoPlace.kind == kind)
    if q:
        statement = statement.where(GeoPlace.name.ilike(f"%{q}%"))
    if parent_id:
        from uuid import UUID

        statement = statement.where(GeoPlace.parent_id == UUID(parent_id))
    return list((await db.execute(statement)).scalars().all())


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
    return {"valid": False, "name": body.name, "message": "Toponym not in local geo reference"}


@router.post("/morph", response_model=MorphOut)
async def morph_word(
    body: MorphRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
) -> MorphOut:
    forms = inflect_cases(body.word)
    return MorphOut(word=body.word, forms=forms, placeholders=city_placeholders(body.word, forms))
