from __future__ import annotations

import pytest

from app.api.v1.keywords import parse_keyword_csv


def test_parse_keyword_csv_maps_optional_columns():
    items, errors = parse_keyword_csv(
        "phrase,frequency,group,intent,city,priority\nремонт стиральных машин,1200,repair,commercial,Москва,high\n".encode(),
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
            "meta": {"frequency": "1200", "intent": "commercial", "city": "Москва", "priority": "high"},
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


def test_parse_keyword_csv_requires_mapped_phrase_column():
    with pytest.raises(ValueError, match="Column 'phrase' is required"):
        parse_keyword_csv(
            b"keyword\nrepair\n",
            delimiter=",",
            phrase_column="phrase",
            column_map={"group": "", "frequency": "", "intent": "", "city": "", "priority": ""},
        )
