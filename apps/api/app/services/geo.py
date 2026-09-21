from __future__ import annotations

from uuid import UUID, uuid4

from app.models import GeoPlace, MorphCache
from app.services.morph import city_placeholders, inflect_cases, word_hash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def get_or_compute_forms(session: AsyncSession, word: str) -> dict[str, str]:
    h = word_hash(word)
    result = await session.execute(select(MorphCache).where(MorphCache.word_hash == h))
    cached = result.scalar_one_or_none()
    if cached:
        return cached.forms
    forms = inflect_cases(word)
    session.add(MorphCache(word_hash=h, lemma=word, forms=forms))
    await session.flush()
    return forms


async def upsert_place(
    session: AsyncSession,
    *,
    kind: str,
    name: str,
    parent_id: UUID | None = None,
    external_id: str | None = None,
    source: str = "manual",
    lat: float | None = None,
    lon: float | None = None,
    timezone: str | None = None,
    population: int | None = None,
    attrs: dict | None = None,
    compute_morph: bool = True,
) -> GeoPlace:
    if external_id:
        existing = await session.execute(
            select(GeoPlace).where(GeoPlace.kind == kind, GeoPlace.external_id == external_id)
        )
        place = existing.scalar_one_or_none()
        if place:
            place.name = name
            if compute_morph:
                place.name_forms = await get_or_compute_forms(session, name)
            return place

    forms = await get_or_compute_forms(session, name) if compute_morph else {}
    place = GeoPlace(
        id=uuid4(),
        kind=kind,
        name=name,
        name_forms=forms,
        parent_id=parent_id,
        external_id=external_id,
        source=source,
        lat=lat,
        lon=lon,
        timezone=timezone,
        population=population,
        attrs=attrs or {},
        is_validated=True,
    )
    session.add(place)
    await session.flush()
    return place


async def validate_toponym(
    session: AsyncSession, name: str, kind: str | None = None
) -> GeoPlace | None:
    stmt = select(GeoPlace).where(
        GeoPlace.name.ilike(name.strip()), GeoPlace.is_validated.is_(True)
    )
    if kind:
        stmt = stmt.where(GeoPlace.kind == kind)
    result = await session.execute(stmt.limit(1))
    return result.scalar_one_or_none()


def place_context(place: GeoPlace) -> dict[str, str]:
    return city_placeholders(place.name, place.name_forms or None)


# Seed subset for local/demo (not full FIAS)
DEMO_GEO = [
    {"kind": "country", "name": "Россия", "external_id": "ru", "timezone": "Europe/Moscow"},
    {
        "kind": "city",
        "name": "Москва",
        "external_id": "ru-msk",
        "lat": 55.7558,
        "lon": 37.6173,
        "timezone": "Europe/Moscow",
        "population": 12600000,
        "districts": ["Сокол", "Хамовники", "Арбат"],
        "metro": ["Сокол", "Маяковская", "Киевская"],
    },
    {
        "kind": "city",
        "name": "Санкт-Петербург",
        "external_id": "ru-spb",
        "lat": 59.9343,
        "lon": 30.3351,
        "timezone": "Europe/Moscow",
        "population": 5600000,
        "districts": ["Приморский", "Василеостровский"],
        "metro": ["Невский проспект", "Приморская"],
    },
    {
        "kind": "city",
        "name": "Казань",
        "external_id": "ru-kzn",
        "lat": 55.7961,
        "lon": 49.1064,
        "timezone": "Europe/Moscow",
        "population": 1300000,
        "districts": ["Вахитовский"],
        "metro": ["Кремлёвская"],
    },
]


async def seed_demo_geo(session: AsyncSession) -> dict:
    country = await upsert_place(
        session,
        kind="country",
        name="Россия",
        external_id="ru",
        source="seed",
        timezone="Europe/Moscow",
    )
    created = {"cities": 0, "districts": 0, "metro": 0}
    for item in DEMO_GEO:
        if item["kind"] != "city":
            continue
        city = await upsert_place(
            session,
            kind="city",
            name=item["name"],
            parent_id=country.id,
            external_id=item["external_id"],
            source="seed",
            lat=item.get("lat"),
            lon=item.get("lon"),
            timezone=item.get("timezone"),
            population=item.get("population"),
        )
        created["cities"] += 1
        for d in item.get("districts", []):
            await upsert_place(
                session,
                kind="district",
                name=d,
                parent_id=city.id,
                external_id=f"{item['external_id']}-d-{d}",
                source="seed",
            )
            created["districts"] += 1
        for m in item.get("metro", []):
            await upsert_place(
                session,
                kind="metro",
                name=m,
                parent_id=city.id,
                external_id=f"{item['external_id']}-m-{m}",
                source="seed",
            )
            created["metro"] += 1
    return created
