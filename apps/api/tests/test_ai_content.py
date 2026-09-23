from __future__ import annotations

import pytest
from app.api.v1.ai_content import _validate_page_copy


def page_copy() -> dict:
    return {
        "title": "Ремонт техники в Казани",
        "h1": "Ремонт техники в Казани",
        "meta_description": "Условия ремонта техники уточняйте у специалиста.",
        "unique_core": "Компания оказывает подтверждённую услугу ремонта техники.",
        "fact_keys": ["service"],
    }


def test_ai_page_copy_validates_plain_text_and_fact_provenance() -> None:
    result = _validate_page_copy(page_copy(), {"service"})
    assert result["fact_keys"] == ["service"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "<script>alert(1)</script>"),
        ("unique_core", "Unsupported claim {{secret}}"),
        ("fact_keys", ["unconfirmed"]),
    ],
)
def test_ai_page_copy_rejects_markup_placeholders_and_unknown_facts(field, value) -> None:
    candidate = page_copy()
    candidate[field] = value
    with pytest.raises(ValueError):
        _validate_page_copy(candidate, {"service"})


def test_ai_page_copy_rejects_overlong_search_metadata() -> None:
    candidate = page_copy()
    candidate["meta_description"] = "x" * 171
    with pytest.raises(ValueError):
        _validate_page_copy(candidate, {"service"})
