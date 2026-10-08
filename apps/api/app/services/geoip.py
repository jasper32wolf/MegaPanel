from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class GeoLocation:
    country: str | None = None
    city: str | None = None


class GeoIPResolver:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path

    def lookup(self, ip_address: str | None) -> GeoLocation:
        if not self.database_path or not _is_global_ip(ip_address):
            return GeoLocation()
        try:
            import maxminddb

            if not Path(self.database_path).is_file():
                return GeoLocation()
            with maxminddb.open_database(self.database_path) as reader:
                record = reader.get(ip_address)
        except Exception:  # noqa: BLE001
            return GeoLocation()
        return _location_from_record(record)


def _is_global_ip(value: str | None) -> bool:
    try:
        return bool(value and ipaddress.ip_address(value).is_global)
    except ValueError:
        return False


def _location_from_record(record: object) -> GeoLocation:
    if not isinstance(record, dict):
        return GeoLocation()
    return GeoLocation(
        country=_location_field(record.get("country")),
        city=_location_field(record.get("city")),
    )


def _location_field(value: object) -> str | None:
    if isinstance(value, dict):
        names = value.get("names")
        if isinstance(names, dict):
            value = names.get("en") or names.get("ru")
        else:
            value = value.get("name") or value.get("iso_code") or value.get("code")
    return _clean_location(value)


def _clean_location(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned[:128] or None


@lru_cache
def get_geoip_resolver(database_path: str) -> GeoIPResolver:
    return GeoIPResolver(database_path)
