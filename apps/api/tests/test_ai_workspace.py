from __future__ import annotations

from uuid import UUID

import pytest
from app.api.v1.ai_workspace import _validate_proposal

KEYWORD = UUID("11111111-1111-1111-1111-111111111111")
GEO = UUID("22222222-2222-2222-2222-222222222222")


def page_proposal() -> dict:
    return {
        "key": "home",
        "title": "Home",
        "purpose": "Service overview",
        "slug": "/",
        "keyword_ids": [str(KEYWORD)],
        "geo_ids": [str(GEO)],
        "fact_keys": ["service"],
        "kit_key": "kit-a",
        "block_ids": ["hero"],
        "uncertainty_notes": [],
    }


def test_architecture_output_validates_catalog_ids() -> None:
    pages = _validate_proposal(
        {"pages": [page_proposal()]},
        keyword_ids={str(KEYWORD)},
        geo_ids={str(GEO)},
        fact_keys={"service"},
        catalogs={"kit-a": {"hero"}},
    )
    assert pages[0]["slug"] == "/"


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("keyword_ids", [str(GEO)], "unselected keyword"),
        ("geo_ids", [str(KEYWORD)], "unselected place"),
        ("fact_keys", ["secret"], "unknown fact"),
        ("kit_key", "missing", "unknown kit"),
        ("block_ids", ["missing"], "unknown block"),
        ("slug", "/../admin", "invalid slug"),
    ],
)
def test_architecture_output_rejects_untrusted_references(field, value, expected) -> None:
    page = page_proposal()
    page[field] = value
    with pytest.raises(ValueError, match=expected):
        _validate_proposal(
            {"pages": [page]},
            keyword_ids={str(KEYWORD)},
            geo_ids={str(GEO)},
            fact_keys={"service"},
            catalogs={"kit-a": {"hero"}},
        )


def test_architecture_output_rejects_extra_fields() -> None:
    page = page_proposal()
    page["html"] = "<script>alert(1)</script>"
    with pytest.raises(ValueError):
        _validate_proposal(
            {"pages": [page]},
            keyword_ids={str(KEYWORD)},
            geo_ids={str(GEO)},
            fact_keys={"service"},
            catalogs={"kit-a": {"hero"}},
        )
