from uuid import uuid4

from site_panel_security import sanitize_html
from site_panel_shared.manifests import BlockDef, PageManifest, SiteManifest
from site_panel_ssg.templates import fill_slots, render_page


def test_sanitize_strips_script():
    dirty = '<p>Hi</p><script>alert(1)</script><a href="javascript:alert(1)">x</a>'
    clean = sanitize_html(dirty)
    assert "script" not in clean.lower()
    assert "javascript:" not in clean.lower()


def test_fill_slots():
    assert fill_slots("в {city_prep}", {"city_prep": "Москве"}) == "в Москве"


def test_render_page_canonical():
    site = SiteManifest(
        site_id=uuid4(),
        tenant_id=uuid4(),
        domain="example.test",
        css_vars={"primary-color": "#111"},
        pages=[],
        contacts={"phone": "+7000"},
    )
    page = PageManifest(
        slug="/remont/",
        title_template="Ремонт",
        h1_template="Ремонт",
        service="Ремонт",
        blocks=[
            BlockDef(type="hero", hash_class="blk-hero-a17", html="<p>{phone}</p>", order=0),
        ],
        unique_core="Оффер",
    )
    html = render_page(site, page)
    assert 'rel="canonical"' in html
    assert "example.test" in html
    assert "+7000" in html


def test_render_page_escapes_text_slots_and_normalizes_root_url():
    site = SiteManifest(
        site_id=uuid4(),
        tenant_id=uuid4(),
        domain="example.test",
        pages=[],
        contacts={},
    )
    page = PageManifest(
        slug="/",
        title_template="{service}",
        h1_template="{service}",
        meta_description_template="{service}",
        service="<script>alert(1)</script>",
        unique_core="<img src=x onerror=alert(1)>",
    )

    html = render_page(site, page, {"service": page.service})

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert 'href="https://example.test/"' in html
