import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from site_panel_blocks import instantiate_blocks, load_kit
from site_panel_shared.manifests import BlockDef, PageManifest, SiteManifest
from site_panel_ssg import BuildAsset, SiteBuilder


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
            ),
            PageManifest(
                slug="/district",
                title_template="{service} в {city_prep}",
                h1_template="{service} в {city_prep}",
                service="Ремонт",
                blocks=blocks,
                unique_core="Уникальное ядро вложенной страницы для теста сборки.",
            ),
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
    assert '<textarea name="message" rows="3"></textarea>' in html
    assert 'href="/cookie-policy/"' in html
    assert 'src="site-panel-leads.js"' in html
    assert 'src="cookie-banner.js"' in html
    root = tmp_path / str(site_id) / "current"
    nested = root / "district"
    assert (root / "cookie-policy" / "index.html").exists()
    assert (root / "site-panel-leads.js").exists()
    assert (root / "cookie-banner.js").exists()
    nested_html = (nested / "index.html").read_text(encoding="utf-8")
    assert 'src="site-panel-leads.js"' in nested_html
    assert 'src="cookie-banner.js"' in nested_html
    assert (nested / "site-panel-leads.js").exists()
    assert (nested / "cookie-banner.js").exists()
    lead_script = (root / "site-panel-leads.js").read_text(encoding="utf-8")
    assert "form.dataset.idempotencyKey" in lead_script
    assert "const minFormAge = 2500;" in lead_script
    assert "button.disabled = true;" in lead_script
    assert "prepareForm(form);" in lead_script


def test_candidate_build_copies_hashed_local_media_for_root_and_nested_pages(tmp_path: Path):
    asset_id = uuid4()
    media_path = tmp_path / "uploaded.webp"
    media_bytes = b"local-webp-fixture"
    media_path.write_bytes(media_bytes)
    digest = hashlib.sha256(media_bytes).hexdigest()
    site_id = uuid4()
    media = [{"asset_id": asset_id, "stored_sha256": digest, "alt": "Проверенное фото"}]
    site = SiteManifest(
        site_id=site_id,
        tenant_id=uuid4(),
        domain="candidate.test",
        pages=[
            PageManifest(
                slug="/",
                title_template="Ремонт",
                h1_template="Ремонт",
                service="Ремонт",
                media=media,
            ),
            PageManifest(
                slug="/district",
                title_template="Ремонт",
                h1_template="Ремонт",
                service="Ремонт",
                media=media,
            ),
        ],
    )

    result = SiteBuilder(tmp_path).build(
        site,
        assets=[BuildAsset(asset_id=asset_id, source_path=media_path, stored_sha256=digest)],
        activate=False,
    )

    release = tmp_path / str(site_id) / "releases" / result["build_hash"]
    assert (release / "assets" / f"{digest}.webp").read_bytes() == media_bytes
    assert f'src="assets/{digest}.webp"' in (release / "index.html").read_text(encoding="utf-8")
    assert f'src="../assets/{digest}.webp"' in (release / "district" / "index.html").read_text(
        encoding="utf-8"
    )
    assert not (tmp_path / str(site_id) / "current").exists()


def test_candidate_build_copies_block_media_for_the_selected_block(tmp_path: Path):
    asset_id = uuid4()
    media_path = tmp_path / "uploaded.webp"
    media_bytes = b"local-block-media-fixture"
    media_path.write_bytes(media_bytes)
    digest = hashlib.sha256(media_bytes).hexdigest()
    site_id = uuid4()
    blocks = [BlockDef(type="hero", hash_class="hero", html="<p>Hero</p>", order=0)]
    site = SiteManifest(
        site_id=site_id,
        tenant_id=uuid4(),
        domain="candidate.test",
        pages=[
            PageManifest(
                slug="/",
                title_template="Ремонт",
                h1_template="Ремонт",
                service="Ремонт",
                blocks=blocks,
                block_media={
                    "hero": {"asset_id": asset_id, "stored_sha256": digest, "alt": "Фото hero"}
                },
            ),
            PageManifest(
                slug="/district",
                title_template="Ремонт",
                h1_template="Ремонт",
                service="Ремонт",
                blocks=blocks,
                block_media={
                    "hero": {"asset_id": asset_id, "stored_sha256": digest, "alt": "Фото hero"}
                },
            ),
        ],
    )

    result = SiteBuilder(tmp_path).build(
        site,
        assets=[BuildAsset(asset_id=asset_id, source_path=media_path, stored_sha256=digest)],
        activate=False,
    )

    release = tmp_path / str(site_id) / "releases" / result["build_hash"]
    assert (release / "assets" / f"{digest}.webp").read_bytes() == media_bytes
    assert 'data-media-for="hero"' in (release / "index.html").read_text(encoding="utf-8")
    assert f'src="assets/{digest}.webp"' in (release / "index.html").read_text(encoding="utf-8")
    assert f'src="../assets/{digest}.webp"' in (release / "district" / "index.html").read_text(
        encoding="utf-8"
    )


def test_candidate_build_rejects_tampered_local_media(tmp_path: Path):
    asset_id = uuid4()
    media_path = tmp_path / "uploaded.webp"
    media_path.write_bytes(b"tampered")
    site = SiteManifest(
        site_id=uuid4(),
        tenant_id=uuid4(),
        domain="candidate.test",
        pages=[
            PageManifest(
                slug="/",
                title_template="Ремонт",
                h1_template="Ремонт",
                service="Ремонт",
                media=[{"asset_id": asset_id, "stored_sha256": "a" * 64, "alt": "Фото"}],
            )
        ],
    )

    with pytest.raises(ValueError, match="hash"):
        SiteBuilder(tmp_path).build(
            site,
            assets=[BuildAsset(asset_id=asset_id, source_path=media_path, stored_sha256="a" * 64)],
            activate=False,
        )

    assert not list((tmp_path / str(site.site_id) / "releases").glob(".building-*"))


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


@pytest.mark.parametrize("force_directory_fallback", [False, True])
def test_first_activation_compensation_restores_candidate_release(
    tmp_path: Path, monkeypatch, force_directory_fallback: bool
):
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
                unique_core="Достаточно длинный черновик для проверки компенсации активации.",
            )
        ],
    )
    builder = SiteBuilder(tmp_path)
    result = builder.build(site, {"service": "Ремонт"}, activate=False)
    root = tmp_path / str(site_id)
    if force_directory_fallback:
        monkeypatch.setattr(builder, "_replace_link", lambda *_args: False)

    assert builder.activate(str(site_id), result["build_hash"])
    if force_directory_fallback:
        assert not (root / "current").is_symlink()
    assert builder.restore_activation(str(site_id), result["build_hash"], None)

    assert not (root / "current").exists()
    assert not (root / "previous").exists()
    assert (root / "releases" / result["build_hash"] / "BUILD_HASH").read_text(
        encoding="utf-8"
    ).strip() == result["build_hash"]


def test_first_activation_compensation_rejects_unexpected_previous(tmp_path: Path):
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
                unique_core="Достаточно длинный черновик для проверки защитного условия.",
            )
        ],
    )
    builder = SiteBuilder(tmp_path)
    result = builder.build(site, {"service": "Ремонт"}, activate=False)
    root = tmp_path / str(site_id)

    assert builder.activate(str(site_id), result["build_hash"])
    (root / "previous").mkdir()

    assert not builder.restore_activation(str(site_id), result["build_hash"], None)
    assert (root / "current" / "BUILD_HASH").read_text(encoding="utf-8").strip() == result[
        "build_hash"
    ]
    assert (root / "previous").is_dir()
