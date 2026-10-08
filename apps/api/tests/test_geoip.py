from __future__ import annotations

from app.services.geoip import GeoIPResolver


def test_geoip_returns_no_location_without_a_local_database(tmp_path):
    resolver = GeoIPResolver(str(tmp_path / "missing.mmdb"))

    assert resolver.lookup("8.8.8.8").country is None
    assert resolver.lookup("8.8.8.8").city is None


def test_geoip_does_not_lookup_private_or_invalid_addresses(tmp_path):
    resolver = GeoIPResolver(str(tmp_path / "unused.mmdb"))

    assert resolver.lookup("127.0.0.1").country is None
    assert resolver.lookup("10.0.0.1").city is None
    assert resolver.lookup("not-an-ip").country is None
