from types import SimpleNamespace

import pytest
from site_panel_blocks import block_slot_schema, catalog, library_version, list_kits, load_kit


def test_library_version_and_kits():
    assert library_version() == "1.0.0"
    kits = list_kits()
    keys = {k["key"] for k in kits}
    assert "service-local-v1" in keys
    assert "home-repair-v1" in keys


def test_kit_loads_all_service_blocks():
    kit = load_kit("service-local-v1")
    types = [b.type for b in kit.blocks]
    for needed in ("hero", "pricing_table", "team", "faq", "lead_form", "footer"):
        assert needed in types
    assert len(kit.blocks) >= 12


def test_curated_block_slot_schema_is_explicit_and_server_owned():
    schema = block_slot_schema("service-local-v1", "hero")

    assert schema == {
        "unique_core": {"type": "string", "max_length": 8000},
        "hero_supporting_text": {"type": "string", "max_length": 280},
    }
    assert "service" not in schema
    assert "city_prep" not in schema
    assert block_slot_schema("service-local-v1", "lead_form") == {}


@pytest.mark.parametrize(
    "editable_slots",
    [
        {"missing": SimpleNamespace(type="string", max_length=100)},
        {"service": SimpleNamespace(type="string", max_length=100)},
    ],
)
def test_curated_slot_schema_rejects_missing_or_reserved_placeholders(monkeypatch, editable_slots):
    block = SimpleNamespace(
        type="hero",
        html="<p>{unique_core}</p>",
        editable_slots=editable_slots,
    )
    monkeypatch.setattr(catalog, "load_kit", lambda _: SimpleNamespace(blocks=[block]))

    with pytest.raises(ValueError):
        catalog.block_slot_schema("test-kit", "hero")


def test_curated_slot_schema_rejects_placeholder_in_attribute_context(monkeypatch):
    block = SimpleNamespace(
        type="hero",
        html='<p>{hero_supporting_text}</p><a href="{hero_supporting_text}">x</a>',
        editable_slots={"hero_supporting_text": SimpleNamespace(type="string", max_length=100)},
    )
    monkeypatch.setattr(catalog, "load_kit", lambda _: SimpleNamespace(blocks=[block]))

    with pytest.raises(ValueError, match="text-node"):
        catalog.block_slot_schema("test-kit", "hero")


def test_unknown_curated_block_has_no_slot_schema():
    with pytest.raises(FileNotFoundError):
        block_slot_schema("service-local-v1", "not-a-block")
