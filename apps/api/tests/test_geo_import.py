from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app.api.v1.geo import _validate_geo_proposal, apply_geo_proposal, parse_geo_csv
from app.main import app
from app.schemas.ai import GeoAIProposalApply
from app.services import geo, nominatim
from fastapi import HTTPException
from site_panel_security import SSRFBlockedError


def test_geo_update_route_is_registered():
    assert "patch" in app.openapi()["paths"]["/api/v1/geo/{place_id}"]


def test_city_is_a_root_of_the_operator_hierarchy():
    assert (
        asyncio.run(geo.validate_parent(ParentDatabase(None), kind="city", parent_id=None)) is None
    )


def test_geo_ai_routes_are_registered():
    paths = app.openapi()["paths"]

    assert "post" in paths["/api/v1/geo/ai-proposals/quote"]
    assert "post" in paths["/api/v1/geo/ai-proposals"]
    assert "post" in paths["/api/v1/geo/ai-proposals/{run_id}/apply"]


def test_geo_ai_proposal_requires_valid_hierarchy_and_plain_names():
    proposal = _validate_geo_proposal(
        {
            "nodes": [
                {
                    "key": "district-center",
                    "kind": "district",
                    "name": "Центр",
                    "parent_key": "city",
                },
                {
                    "key": "landmark-park",
                    "kind": "landmark",
                    "name": "Парк",
                    "parent_key": "district-center",
                },
            ],
            "warnings": [],
        },
        max_places=2,
    )

    assert proposal["nodes"][1]["parent_key"] == "district-center"
    with pytest.raises(ValueError, match="invalid parent"):
        _validate_geo_proposal(
            {
                "nodes": [
                    {
                        "key": "invalid-landmark",
                        "kind": "landmark",
                        "name": "Ориентир",
                        "parent_key": "missing",
                    }
                ],
                "warnings": [],
            },
            max_places=2,
        )
    with pytest.raises(ValueError, match="plain text"):
        _validate_geo_proposal(
            {
                "nodes": [
                    {
                        "key": "unsafe-district",
                        "kind": "district",
                        "name": "<script>",
                        "parent_key": "city",
                    }
                ],
                "warnings": [],
            },
            max_places=2,
        )


def test_geo_ai_apply_requires_explicit_approval_before_writing():
    run = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        action="geo.city-hierarchy",
        status="pending_approval",
        operator_decision=None,
        output={"nodes": []},
    )

    class Session:
        async def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: run)

    auth = SimpleNamespace(tenant_id=run.tenant_id, user=SimpleNamespace(id=uuid4()))
    with pytest.raises(HTTPException, match="Approve the geography proposal first") as exc_info:
        asyncio.run(apply_geo_proposal(run.id, GeoAIProposalApply(), auth, Session()))

    assert exc_info.value.status_code == 409


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


class ParentDatabase:
    def __init__(self, parent):
        self.parent = parent

    async def get(self, _model, _identifier):
        return self.parent


@pytest.mark.parametrize(
    ("kind", "parent_kind"),
    [("district", "city"), ("metro", "city"), ("landmark", "district"), ("landmark", "metro")],
)
def test_geo_hierarchy_accepts_requested_city_tree(kind: str, parent_kind: str):
    assert (
        asyncio.run(
            geo.validate_parent(
                ParentDatabase(type("Parent", (), {"kind": parent_kind})()),
                kind=kind,
                parent_id=object(),
            )
        )
        is None
    )


@pytest.mark.parametrize(
    ("kind", "parent_kind"),
    [("district", "metro"), ("metro", "district"), ("landmark", "region")],
)
def test_geo_hierarchy_rejects_invalid_parent_kind(kind: str, parent_kind: str):
    with pytest.raises(ValueError, match="requires a parent"):
        asyncio.run(
            geo.validate_parent(
                ParentDatabase(type("Parent", (), {"kind": parent_kind})()),
                kind=kind,
                parent_id=object(),
            )
        )


class ParentChainDatabase:
    def __init__(self, items: dict[object, object]):
        self.items = items

    async def get(self, _model, identifier):
        return self.items.get(identifier)


def test_geo_parent_rejects_self_reference_cycle():
    place_id = object()
    place = type("Place", (), {"id": place_id, "kind": "city", "parent_id": None})()

    with pytest.raises(ValueError, match="create a cycle"):
        asyncio.run(
            geo.validate_parent(
                ParentChainDatabase({place_id: place}),
                kind="district",
                parent_id=place_id,
                child_id=place_id,
            )
        )


def test_geo_parent_rejects_descendant_as_new_parent():
    child_id = object()
    prospective_parent_id = object()
    prospective_parent = type(
        "Place", (), {"id": prospective_parent_id, "kind": "city", "parent_id": child_id}
    )()
    child = type("Place", (), {"id": child_id, "kind": "district", "parent_id": None})()

    with pytest.raises(ValueError, match="create a cycle"):
        asyncio.run(
            geo.validate_parent(
                ParentChainDatabase({prospective_parent_id: prospective_parent, child_id: child}),
                kind="district",
                parent_id=prospective_parent_id,
                child_id=child_id,
            )
        )


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
