from __future__ import annotations

import hashlib
import re
from html import escape
from typing import Any

from site_panel_security import sanitize_html
from site_panel_shared.manifests import PageManifest, SiteManifest
from site_panel_shared.phone import normalize_phone_e164

from site_panel_ssg.phone import format_phone_display

_PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)\}", re.IGNORECASE)
_FORBIDDEN_CSS = re.compile(
    r"(?:@import|expression\s*\(|url\s*\(|-moz-binding|behavior\s*:|</style)", re.I
)


def fill_slots(template: str, context: dict[str, Any]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        value = context.get(key)
        return "" if value is None else str(value)

    return _PLACEHOLDER.sub(repl, template)


def page_url(domain: str, slug: str) -> str:
    host = domain.strip().strip("/")
    path = slug.strip("/")
    return f"https://{host}/" if not path else f"https://{host}/{path}/"


def _safe_css(value: str) -> str:
    return "" if _FORBIDDEN_CSS.search(value) else value


def render_page(
    site: SiteManifest,
    page: PageManifest,
    context: dict[str, Any] | None = None,
    media_urls: dict[str, str] | None = None,
) -> str:
    ctx = {
        "domain": site.domain,
        "locale": site.locale,
        **(site.contacts or {}),
        **(context or {}),
    }
    raw_phone = str(ctx.get("phone") or "")
    ctx["phone"] = format_phone_display(raw_phone)
    ctx["phone_href"] = normalize_phone_e164(raw_phone) or raw_phone.strip()
    title = escape(fill_slots(page.title_template, ctx))
    h1 = escape(fill_slots(page.h1_template, ctx))
    meta = escape(fill_slots(page.meta_description_template, ctx), quote=True)
    unique = escape(page.unique_core or "")

    blocks = sorted(page.blocks, key=lambda b: (b.order ^ (page.seed & 0xFF), b.type))
    body_parts: list[str] = []
    css_parts: list[str] = [":root {"]
    for key, value in site.css_vars.items():
        css_value = _safe_css(str(value))
        if css_value:
            css_parts.append(f"  --{key}: {css_value};")
    css_parts.append("}")
    css_parts.append(
        "body{margin:0;background:var(--sp-bg,var(--bg,#fff));"
        "color:var(--sp-text,var(--text,#111));"
        "font-family:var(--sp-font,system-ui,sans-serif);}"
        " main{max-width:960px;margin:0 auto;padding:1rem;}"
        " .sp-block-media{margin:1rem 0}"
        " .sp-block-media img{display:block;max-width:100%;height:auto}"
        " .sp-author{display:grid;grid-template-columns:minmax(0,1fr);gap:1rem;"
        "margin:2rem 0;padding:1rem;border:1px solid #d7dbe2;border-radius:.75rem;}"
        " .sp-author__portrait{width:112px;height:112px;object-fit:cover;border-radius:50%;}"
        " .sp-author__meta{margin:0;color:#444}"
    )

    has_hero = any(block.type == "hero" for block in blocks)
    has_h1 = False
    for block in blocks:
        slot_values = {
            key: escape(str(value or ""))
            for key, value in (page.block_slot_values.get(block.type) or {}).items()
            if key not in ctx and key != "unique_core"
        }
        html = sanitize_html(
            fill_slots(
                block.html,
                {**ctx, "unique_core": escape(page.unique_core or ""), **slot_values},
            )
        )
        has_h1 = has_h1 or re.search(r"<h1(?:\s|>)", html, re.I) is not None
        body_parts.append(
            f'<section class="{escape(block.hash_class, quote=True)}" '
            f'data-block="{escape(block.type, quote=True)}">{html}</section>'
        )
        attachment = page.block_media.get(block.type)
        if attachment and (url := (media_urls or {}).get(str(attachment.asset_id))):
            body_parts.append(
                '<figure class="sp-block-media" '
                f'data-media-for="{escape(block.type, quote=True)}">'
                f'<img src="{escape(url, quote=True)}" alt="{escape(attachment.alt, quote=True)}" '
                'loading="lazy" decoding="async"></figure>'
            )
        css = _safe_css(block.css)
        if css:
            css_parts.append(css)

    css = "\n".join(css_parts)
    media_html = "\n".join(
        (
            '<section class="sp-media-gallery">'
            f'<figure><img src="{escape(url, quote=True)}" alt="{escape(item.alt, quote=True)}" '
            'loading="lazy" decoding="async"></figure>'
            "</section>"
        )
        for item in page.media
        if (url := (media_urls or {}).get(str(item.asset_id)))
    )
    body = "\n".join([*body_parts, media_html] if media_html else body_parts)
    h1_html = "" if has_h1 else f"<h1>{h1}</h1>"
    unique_html = "" if has_hero or not unique else f'<p class="unique-core">{unique}</p>'
    author_html = ""
    if page.author:
        author = page.author
        portrait_url = (media_urls or {}).get(str(author.portrait.asset_id))
        portrait_html = (
            f'<img class="sp-author__portrait" src="{escape(portrait_url, quote=True)}" '
            f'alt="{escape(author.portrait.alt, quote=True)}" loading="lazy" decoding="async">'
            if portrait_url
            else ""
        )
        expertise = "".join(f"<li>{escape(item)}</li>" for item in author.expertise)
        evidence = "".join(f"<li>{escape(item)}</li>" for item in author.evidence)
        author_html = f'''<section class="sp-author" id="author-{escape(author.slug, quote=True)}">
      {portrait_html}
      <div>
        <h2>Автор материала: {escape(author.name)}</h2>
        <p class="sp-author__meta">{escape(author.role)}</p>
        <p>{escape(author.biography)}</p>
        <h3>Экспертиза</h3><ul>{expertise}</ul>
        <h3>Основания для публикации</h3><ul>{evidence}</ul>
      </div>
    </section>'''

    return f"""<!DOCTYPE html>
<html lang="{escape(site.locale, quote=True)}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <meta name="description" content="{meta}">
  <link rel="canonical" href="{escape(page_url(site.domain, page.slug), quote=True)}">
  <style>{css}</style>
</head>
<body>
  <main>
    {h1_html}
    {unique_html}
    {body}
    {author_html}
    <footer><a href="/sitemap/">Карта сайта</a></footer>
  </main>
</body>
</html>
"""


def content_hash(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()
