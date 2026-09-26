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
            "lead_token": "t" * 32,
            "lead_api_url": "/api/v1/leads/public",
        },
    )
    assert result["build_hash"]
    html = (tmp_path / str(site_id) / "current" / "index.html").read_text(encoding="utf-8")
    assert 'data-block="hero"' in html
    assert 'data-block="pricing_table"' in html
    assert 'data-block="team"' in html
    assert 'data-block="faq"' in html
    assert "--sp-primary" in html
    assert "FAQPage" in html or "application/ld+json" in html
    assert f'data-site-id="{site_id}"' in html
    assert 'data-lead-token="tttttttttttttttttttttttttttttttt"' in html
    assert 'data-endpoint="/api/v1/leads/public"' in html
    assert 'href="/cookie-policy/"' in html
    assert (tmp_path / str(site_id) / "current" / "cookie-policy" / "index.html").exists()
    assert (tmp_path / str(site_id) / "current" / "site-panel-leads.js").exists()
    lead_script = (tmp_path / str(site_id) / "current" / "site-panel-leads.js").read_text(
        encoding="utf-8"
    )
    assert "form.dataset.idempotencyKey" in lead_script


def test_candidate_build_does_not_activate_until_requested(tmp_path: Path):
    site_id = uuid4()
    site = SiteManifest(
        site_id=site_id,
        tenant_id=uuid4(),
        domain="candidate.test",
        pages=[
            PageManifest(
                slug="/",
                title_template="{service}",
                h1_template="{service}",
                service="Ремонт",
                unique_core="Достаточно длинный черновик для отдельной candidate сборки.",
            )
        ],
    )
    builder = SiteBuilder(tmp_path)

    result = builder.build(site, {"service": "Ремонт"}, activate=False)

    release = tmp_path / str(site_id) / "releases" / result["build_hash"]
    assert release.is_dir()
    assert not (tmp_path / str(site_id) / "current").exists()
    assert result["activated"] is False
    assert builder.activate(str(site_id), result["build_hash"])
    marker = tmp_path / str(site_id) / "current" / "BUILD_HASH"
    assert marker.read_text(encoding="utf-8").strip() == result["build_hash"]
