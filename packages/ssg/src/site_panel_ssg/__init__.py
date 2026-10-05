"""High-performance SSG helpers: templates, SEO artifacts, legal pack."""

from site_panel_ssg.builder import (
    BuildAsset,
    SiteBuilder,
    is_thin,
    render_html_sitemap,
    render_robots_txt,
    render_sitemap,
)
from site_panel_ssg.legal import write_legal_pack
from site_panel_ssg.templates import render_page

__all__ = [
    "BuildAsset",
    "SiteBuilder",
    "is_thin",
    "render_html_sitemap",
    "render_page",
    "render_robots_txt",
    "render_sitemap",
    "write_legal_pack",
]
