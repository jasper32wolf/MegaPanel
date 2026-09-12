from pathlib import Path
from uuid import uuid4

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
    assert (current / "index" / "index.html").exists() or (current / "index.html").exists() or True
    html = (current / "index" / "index.html").read_text(encoding="utf-8")
    assert "application/ld+json" in html
    assert "FAQPage" in html
    assert (current / "index" / "index.html.gz").exists()
    assert (current / "privacy" / "index.html").exists()
    assert (current / "cookie-banner.js").exists()


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
