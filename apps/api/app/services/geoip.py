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
            import geoip2.database

            if not Path(self.database_path).is_file():
                return GeoLocation()
            with geoip2.database.Reader(self.database_path) as reader:
                result = reader.city(ip_address)
        except Exception:  # noqa: BLE001
            return GeoLocation()
        return GeoLocation(
            country=_clean_location(result.country.name),
            city=_clean_location(result.city.name),
        )


def _is_global_ip(value: str | None) -> bool:
    try:
        return bool(value and ipaddress.ip_address(value).is_global)
    except ValueError:
        return False


def _clean_location(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned[:128] or None


@lru_cache
def get_geoip_resolver(database_path: str) -> GeoIPResolver:
    return GeoIPResolver(database_path)
