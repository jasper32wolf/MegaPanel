from __future__ import annotations

import asyncio

import httpx
import pytest
from app.api.v1.geo import parse_geo_csv
from app.services import nominatim
from site_panel_security import SSRFBlockedError


def test_parse_geo_csv_accepts_parent_first_hierarchy():
    rows, errors = parse_geo_csv(
        b"kind,external_id,name,parent_external_id,lat,lon,timezone,population\ncity,moscow,\xd0\x9c\xd0\xbe\xd1\x81\xd0\xba\xd0\xb2\xd0\xb0,,55.7558,37.6173,Europe/Moscow,12600000\ndistrict,sokol,\xd0\xa1\xd0\xbe\xd0\xba\xd0\xbe\xd0\xbb,moscow,,,,\n",
        delimiter=",",
    )

    assert errors == []
    assert rows[0]["external_id"] == "moscow"
    assert rows[1]["parent_external_id"] == "moscow"
    assert rows[1]["line"] == 3


def test_parse_geo_csv_rejects_invalid_rows():
    rows, errors = parse_geo_csv(
        b"kind,external_id,name\ninvalid,id,Name\ncity,,Name\n",
        delimiter=",",
    )

    assert rows == []
    assert len(errors) == 2


def test_parse_geo_csv_requires_core_columns():
    with pytest.raises(ValueError, match="kind, external_id, and name"):
        parse_geo_csv(b"name\nMoscow\n", delimiter=",")


def test_nominatim_does_not_fall_back_to_unpinned_request(monkeypatch):
    class FailingGuard:
        async def fetch(self, _: str):
            raise SSRFBlockedError("blocked")

    def unpinned_client(*_args, **_kwargs):
        raise AssertionError("Nominatim must not use an unpinned fallback request")

    monkeypatch.setattr(httpx, "AsyncClient", unpinned_client)
    client = nominatim.NominatimClient()
    client._guard = FailingGuard()

    with pytest.raises(SSRFBlockedError, match="blocked"):
        asyncio.run(client.search("Moscow"))
