from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1.keywords import parse_keyword_csv, preview_keywords
from app.main import app


def test_parse_keyword_csv_maps_optional_columns():
    items, errors = parse_keyword_csv(
        (
            "phrase,frequency,group,intent,city,priority\n"
            "ремонт стиральных машин,1200,repair,commercial,Москва,high\n"
        ).encode(),
        delimiter=",",
        phrase_column="phrase",
        column_map={
            "group": "group",
            "frequency": "frequency",
            "intent": "intent",
            "city": "city",
            "priority": "priority",
        },
    )

    assert errors == []
    assert items == [
        {
            "phrase": "ремонт стиральных машин",
            "category": "repair",
            "meta": {
                "frequency": "1200",
                "intent": "commercial",
                "city": "Москва",
                "priority": "high",
            },
        }
    ]


def test_parse_keyword_csv_reports_empty_phrase_rows():
    items, errors = parse_keyword_csv(
        b"phrase,group\n,repair\ncleaning,cleaning\n",
        delimiter=",",
        phrase_column="phrase",
        column_map={"group": "group", "frequency": "", "intent": "", "city": "", "priority": ""},
    )

    assert len(items) == 1
    assert errors == [{"line": 2, "error": "Empty phrase"}]


def test_preview_keywords_reports_existing_and_in_file_duplicates_without_writes():
    class Session:
        async def execute(self, _statement):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: ["already exists"]))

    result = asyncio.run(
        preview_keywords(
            Session(),
            tenant_id=uuid4(),
            items=[
                {"phrase": "Already exists", "category": None, "meta": {}},
                {"phrase": "New phrase", "category": "repair", "meta": {"intent": "commercial"}},
                {"phrase": " new   phrase ", "category": None, "meta": {}},
            ],
            errors=[{"line": 5, "error": "Empty phrase"}],
        )
    )

    assert result["created"] == 1
    assert result["skipped"] == 2
    assert result["invalid_rows"] == 1
    assert result["preview"] == [
        {"phrase": "New phrase", "category": "repair", "meta": {"intent": "commercial"}}
    ]


def test_keyword_csv_preview_route_is_registered():
    assert "post" in app.openapi()["paths"]["/api/v1/keywords/import-csv/preview"]


def test_parse_keyword_csv_requires_mapped_phrase_column():
    with pytest.raises(ValueError, match="Column 'phrase' is required"):
        parse_keyword_csv(
            b"keyword\nrepair\n",
            delimiter=",",
            phrase_column="phrase",
            column_map={"group": "", "frequency": "", "intent": "", "city": "", "priority": ""},
        )
