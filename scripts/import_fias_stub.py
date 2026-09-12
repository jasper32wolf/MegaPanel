"""
FIAS/GAR importer stub.

Full ГАР dump is NOT stored in git. Place extracted CSV/XML under data/geo/
and run:

  python scripts/import_fias_stub.py --cities data/geo/cities_sample.csv

Expected CSV columns: external_id,name,parent_external_id,lat,lon,population
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from pathlib import Path


async def import_cities(csv_path: Path) -> int:
    # Lazy imports so script can show help without full app env
    import sys

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "apps" / "api"))
    sys.path.insert(0, str(root / "packages" / "shared" / "src"))
    sys.path.insert(0, str(root / "packages" / "security" / "src"))
    sys.path.insert(0, str(root / "packages" / "ssg" / "src"))

    from app.db.session import SessionLocal
    from app.services.geo import upsert_place

    count = 0
    async with SessionLocal() as session:
        country = await upsert_place(
            session, kind="country", name="Россия", external_id="ru", source="fias"
        )
        parent_map = {"ru": country.id}
        with csv_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                parent_ext = row.get("parent_external_id") or "ru"
                parent_id = parent_map.get(parent_ext, country.id)
                place = await upsert_place(
                    session,
                    kind="city",
                    name=row["name"],
                    parent_id=parent_id,
                    external_id=row["external_id"],
                    source="fias",
                    lat=float(row["lat"]) if row.get("lat") else None,
                    lon=float(row["lon"]) if row.get("lon") else None,
                    population=int(row["population"]) if row.get("population") else None,
                )
                parent_map[row["external_id"]] = place.id
                count += 1
        await session.commit()
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Import FIAS/GAR city sample CSV")
    parser.add_argument("--cities", type=Path, required=True)
    args = parser.parse_args()
    if not args.cities.exists():
        raise SystemExit(f"File not found: {args.cities}")
    n = asyncio.run(import_cities(args.cities))
    print(f"Imported {n} cities")


if __name__ == "__main__":
    main()
