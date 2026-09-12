from __future__ import annotations

import hashlib
import re
from typing import Any

from site_panel_security import sanitize_html
from site_panel_shared.manifests import PageManifest, SiteManifest

_PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)\}", re.IGNORECASE)


def fill_slots(template: str, context: dict[str, Any]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        value = context.get(key)
        return "" if value is None else str(value)

    return _PLACEHOLDER.sub(repl, template)


def render_page(site: SiteManifest, page: PageManifest, context: dict[str, Any] | None = None) -> str:
    ctx = {
        "domain": site.domain,
        "locale": site.locale,
        **(site.contacts or {}),
        **(context or {}),
    }
    title = fill_slots(page.title_template, ctx)
    h1 = fill_slots(page.h1_template, ctx)
    meta = fill_slots(page.meta_description_template, ctx)
    unique = page.unique_core or ""

    blocks = sorted(page.blocks, key=lambda b: (b.order ^ (page.seed & 0xFF), b.type))
    body_parts: list[str] = []
    css_parts: list[str] = [":root {"]
    for k, v in site.css_vars.items():
        css_parts.append(f"  --{k}: {v};")
    css_parts.append("}")
    css_parts.append(
        "body{margin:0;background:var(--sp-bg,var(--bg,#fff));color:var(--sp-text,var(--text,#111));"
        "font-family:var(--sp-font,system-ui,sans-serif);} main{max-width:960px;margin:0 auto;padding:1rem;}"
    )

    has_hero = any(b.type == "hero" for b in blocks)
    for block in blocks:
        html = sanitize_html(fill_slots(block.html, {**ctx, "unique_core": unique}))
        body_parts.append(f'<section class="{block.hash_class}" data-block="{block.type}">{html}</section>')
        if block.css:
            css_parts.append(block.css)

    css = "\n".join(css_parts)
    body = "\n".join(body_parts)
    h1_html = "" if has_hero else f"<h1>{h1}</h1>"
    unique_html = "" if has_hero or not unique else f'<p class="unique-core">{unique}</p>'

    return f"""<!DOCTYPE html>
<html lang="{site.locale}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <meta name="description" content="{meta}">
  <link rel="canonical" href="https://{site.domain}/{page.slug.strip('/')}/">
  <style>{css}</style>
</head>
<body>
  <main>
    {h1_html}
    {unique_html}
    {body}
  </main>
</body>
</html>
"""


def content_hash(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()
