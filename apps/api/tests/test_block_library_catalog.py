from site_panel_blocks import library_version, list_kits, load_kit


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
