from site_panel_blocks import block_slot_schema, library_version, list_kits, load_kit


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


def test_curated_block_slot_schema_is_server_owned():
    schema = block_slot_schema("service-local-v1", "hero")
    assert schema == {"unique_core": {"type": "string", "max_length": 8000}}
    assert block_slot_schema("service-local-v1", "lead_form") == {}


def test_unknown_curated_block_has_no_slot_schema():
    import pytest

    with pytest.raises(FileNotFoundError):
        block_slot_schema("service-local-v1", "not-a-block")
