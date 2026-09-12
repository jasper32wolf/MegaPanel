from site_panel_blocks import apply_theme, hash_class_for, instantiate_blocks, load_kit


def test_theme_deterministic():
    a = apply_theme("site-aaa")
    b = apply_theme("site-aaa")
    c = apply_theme("site-bbb")
    assert a == b
    assert a["sp-primary"] != c["sp-primary"] or a["sp-radius"] != c["sp-radius"]


def test_hash_class_differs_by_site():
    html = "<p>x</p>"
    h1 = hash_class_for("hero", html, "site-1")
    h2 = hash_class_for("hero", html, "site-2")
    assert h1 != h2
    assert h1.startswith("blk-hero-")


def test_instantiate_rewrites_css():
    kit = load_kit("service-local-v1")
    blocks, vars_ = instantiate_blocks(kit.blocks, "seed-42", kit.theme)
    assert "sp-primary" in vars_
    hero = next(b for b in blocks if b["type"] == "hero")
    assert hero["hash_class"] in hero["css"] or not hero["css"]
    assert "blk-hero-" in hero["hash_class"]
