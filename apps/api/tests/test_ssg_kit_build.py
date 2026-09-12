from pathlib import Path
from uuid import uuid4

from site_panel_blocks import instantiate_blocks, load_kit
from site_panel_shared.manifests import BlockDef, PageManifest, SiteManifest
from site_panel_ssg import SiteBuilder


def test_ssg_kit_build_contains_core_blocks(tmp_path: Path):
    kit = load_kit("service-local-v1")
    site_id = uuid4()
    instances, css_vars = instantiate_blocks(kit.blocks, site_id, kit.theme)
    mapped = {
        "primary-color": css_vars["sp-primary"],
        "bg": css_vars["sp-bg"],
        "text": css_vars["sp-text"],
        **css_vars,
    }
    blocks = [
        BlockDef(
            type=b["type"],
            hash_class=b["hash_class"],
            html=b["html"],
            css=b["css"],
            order=b["order"],
        )
        for b in instances
    ]
    site = SiteManifest(
        site_id=site_id,
        tenant_id=uuid4(),
        domain="kit-demo.test",
        css_vars=mapped,
        pages=[
            PageManifest(
                slug="/",
                title_template="{service} в {city_prep}",
                h1_template="{service} в {city_prep}",
                service="Ремонт",
                blocks=blocks,
                unique_core="Уникальное ядро превью комплекта для теста сборки.",
            )
        ],
        contacts={"phone": "+7000"},
        legal={"org": "Test"},
    )
    result = SiteBuilder(tmp_path).build(
        site,
        {
            "city_prep": "Москве",
            "city_nom": "Москва",
            "city_gen": "Москвы",
            "phone": "+7000",
            "service": "Ремонт",
            "modifier": "Срочный",
            "price": "990",
        },
    )
    assert result["build_hash"]
    html = (tmp_path / str(site_id) / "current" / "index" / "index.html").read_text(encoding="utf-8")
    assert "data-block=\"hero\"" in html
    assert "data-block=\"pricing_table\"" in html
    assert "data-block=\"team\"" in html
    assert "data-block=\"faq\"" in html
    assert "--sp-primary" in html
    assert "FAQPage" in html or "application/ld+json" in html
