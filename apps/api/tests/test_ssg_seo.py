from pathlib import Path
from uuid import uuid4

import pytest
from site_panel_shared.manifests import BlockDef, PageManifest, SiteManifest
from site_panel_ssg import SiteBuilder, is_thin, render_robots_txt, render_sitemap


def test_ssg_writes_seo_artifacts(tmp_path: Path):
    site = SiteManifest(
        site_id=uuid4(),
        tenant_id=uuid4(),
        domain="example.test",
        css_vars={"primary-color": "#111"},
        pages=[
            PageManifest(
                slug="/",
                title_template="Главная",
                h1_template="Услуги в {city_prep}",
                service="Услуги",
                blocks=[
                    BlockDef(
                        type="hero",
                        hash_class="blk-x",
                        html="<p>" + ("текст " * 80) + "</p><p>{phone}</p>",
                        order=0,
                    )
                ],
                unique_core="Уникальное ядро для страницы услуг в городе",
            )
        ],
        contacts={"phone": "+7000"},
    )
    builder = SiteBuilder(tmp_path)
    result = builder.build(site, {"city_prep": "Москве", "city_nom": "Москва", "phone": "+7000"})
    assert result["build_hash"]
    current = tmp_path / str(site.site_id) / "current"
    assert (current / "robots.txt").exists()
    assert (current / "sitemap.xml").exists()
    assert (current / "index.html").exists()
    html = (current / "index.html").read_text(encoding="utf-8")
    assert "application/ld+json" in html
    assert "FAQPage" in html
    assert (current / "index.html.gz").exists()
    assert (current / "privacy" / "index.html").exists()
    assert (current / "cookie-banner.js").exists()
    assert '<script src="cookie-banner.js" defer></script>' in html


def test_thin_guard():
    assert is_thin("<p>hi</p>") is True
    assert is_thin("<p>" + ("word " * 100) + "</p>") is False


def test_robots_and_sitemap():
    robots = render_robots_txt()
    assert "GPTBot" in robots
    assert "Disallow: /" in robots
    sm = render_sitemap("ex.test", ["/", "/a/"])
    assert "ex.test" in sm
    assert "<urlset" in sm


def test_ssg_build_keeps_previous_release_for_rollback(tmp_path: Path):
    site_id = uuid4()
    builder = SiteBuilder(tmp_path)

    def site_for(title: str) -> SiteManifest:
        return SiteManifest(
            site_id=site_id,
            tenant_id=uuid4(),
            domain="rollback.test",
            pages=[
                PageManifest(
                    slug="/",
                    title_template=title,
                    h1_template=title,
                    service="Услуги",
                    blocks=[BlockDef(type="hero", hash_class="blk-x", html="<p>content</p>")],
                )
            ],
        )

    first = builder.build(site_for("Первая версия"))
    second = builder.build(site_for("Вторая версия"))
    current = tmp_path / str(site_id) / "current"
    previous = tmp_path / str(site_id) / "previous"

    assert first["build_hash"] != second["build_hash"]
    assert "Вторая версия" in (current / "index.html").read_text(encoding="utf-8")
    assert "Первая версия" in (previous / "index.html").read_text(encoding="utf-8")
    assert builder.rollback(str(site_id), first["build_hash"])
    assert "Первая версия" in (current / "index.html").read_text(encoding="utf-8")


def test_activation_compensation_restores_previous_release(tmp_path: Path):
    site_id = uuid4()
    builder = SiteBuilder(tmp_path)

    def site_for(title: str) -> SiteManifest:
        return SiteManifest(
            site_id=site_id,
            tenant_id=uuid4(),
            domain="restore.test",
            pages=[
                PageManifest(
                    slug="/",
                    title_template=title,
                    h1_template=title,
                    service="Услуги",
                    blocks=[BlockDef(type="hero", hash_class="blk-x", html="<p>content</p>")],
                )
            ],
        )

    first = builder.build(site_for("Первая версия"))
    second = builder.build(site_for("Вторая версия"))
    root = tmp_path / str(site_id)

    assert builder.restore_activation(str(site_id), second["build_hash"], first["build_hash"])
    assert (root / "current" / "BUILD_HASH").read_text(encoding="utf-8").strip() == first[
        "build_hash"
    ]
    assert (root / "previous" / "BUILD_HASH").read_text(encoding="utf-8").strip() == second[
        "build_hash"
    ]


@pytest.mark.parametrize("broken_topology", ["marker", "missing_previous"])
def test_activation_compensation_rejects_changed_topology(tmp_path: Path, broken_topology: str):
    site_id = uuid4()
    builder = SiteBuilder(tmp_path)

    def site_for(title: str) -> SiteManifest:
        return SiteManifest(
            site_id=site_id,
            tenant_id=uuid4(),
            domain="restore.test",
            pages=[
                PageManifest(
                    slug="/",
                    title_template=title,
                    h1_template=title,
                    service="Услуги",
                    blocks=[BlockDef(type="hero", hash_class="blk-x", html="<p>content</p>")],
                )
            ],
        )

    first = builder.build(site_for("Первая версия"))
    second = builder.build(site_for("Вторая версия"))
    root = tmp_path / str(site_id)
    current = root / "current"
    previous = root / "previous"
    expected_current_marker = second["build_hash"]

    if broken_topology == "marker":
        expected_current_marker = "f" * 64
        (current / "BUILD_HASH").write_text(expected_current_marker, encoding="utf-8")
    else:
        previous.rename(root / "unexpected-previous")

    assert not builder.restore_activation(str(site_id), second["build_hash"], first["build_hash"])
    assert (current / "BUILD_HASH").read_text(encoding="utf-8").strip() == expected_current_marker
    expected_previous = previous if broken_topology == "marker" else root / "unexpected-previous"
    assert (expected_previous / "BUILD_HASH").read_text(encoding="utf-8").strip() == first[
        "build_hash"
    ]


def test_directory_activation_compensation_restores_topology(tmp_path: Path, monkeypatch):
    site_id = uuid4()
    builder = SiteBuilder(tmp_path)
    monkeypatch.setattr(builder, "_replace_link", lambda *_args: False)

    def site_for(title: str) -> SiteManifest:
        return SiteManifest(
            site_id=site_id,
            tenant_id=uuid4(),
            domain="restore.test",
            pages=[
                PageManifest(
                    slug="/",
                    title_template=title,
                    h1_template=title,
                    service="Услуги",
                    blocks=[BlockDef(type="hero", hash_class="blk-x", html="<p>content</p>")],
                )
            ],
        )

    first = builder.build(site_for("Первая версия"))
    second = builder.build(site_for("Вторая версия"))
    root = tmp_path / str(site_id)
    temporary = root / ".restore-activation"
    temporary.mkdir()

    assert not (root / "current").is_symlink()
    assert not builder.restore_activation(str(site_id), second["build_hash"], first["build_hash"])
    assert (root / "current" / "BUILD_HASH").read_text(encoding="utf-8").strip() == second[
        "build_hash"
    ]
    assert (root / "previous" / "BUILD_HASH").read_text(encoding="utf-8").strip() == first[
        "build_hash"
    ]
    temporary.rmdir()

    assert builder.restore_activation(str(site_id), second["build_hash"], first["build_hash"])
    assert (root / "current" / "BUILD_HASH").read_text(encoding="utf-8").strip() == first[
        "build_hash"
    ]
    assert (root / "previous" / "BUILD_HASH").read_text(encoding="utf-8").strip() == second[
        "build_hash"
    ]


def test_sitemap_escapes_xml_locations():
    sitemap = render_sitemap("example.test", ["/service?a=1&b=2"])

    assert "&amp;" in sitemap
    assert "a=1&b=2" not in sitemap


def test_ssg_artifacts_match_index_policy_and_legal_pages_are_noindex(tmp_path: Path):
    site = SiteManifest(
        site_id=uuid4(),
        tenant_id=uuid4(),
        domain="example.test",
        pages=[
            PageManifest(
                slug="/",
                title_template="Главная",
                h1_template="Главная",
                service="Услуги",
                index_state="indexed",
                blocks=[
                    BlockDef(
                        type="hero", hash_class="blk-root", html="<p>" + "текст " * 90 + "</p>"
                    )
                ],
            ),
            PageManifest(
                slug="/draft",
                title_template="Черновик",
                h1_template="Черновик",
                service="Услуги",
                blocks=[
                    BlockDef(
                        type="hero", hash_class="blk-draft", html="<p>" + "текст " * 90 + "</p>"
                    )
                ],
            ),
        ],
        legal={"org": "ООО Тест", "privacy_email": "privacy@example.com"},
    )

    SiteBuilder(tmp_path).build(site)
    current = tmp_path / str(site.site_id) / "current"
    sitemap = (current / "sitemap.xml").read_text(encoding="utf-8")
    root = (current / "index.html").read_text(encoding="utf-8")
    draft = (current / "draft" / "index.html").read_text(encoding="utf-8")
    privacy = (current / "privacy" / "index.html").read_text(encoding="utf-8")

    assert "https://example.test/" in sitemap
    assert "https://example.test/draft/" not in sitemap
    assert '<link rel="canonical" href="https://example.test/">' in root
    assert '<link rel="canonical" href="https://example.test/draft/">' in draft
    assert '<meta name="robots" content="noindex, follow">' in draft
    assert '<meta name="robots" content="noindex, follow">' in privacy
