from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from site_panel_shared.enums import IndexState
from site_panel_shared.manifests import PageManifest, SiteManifest
from site_panel_ssg.legal import write_legal_pack
from site_panel_ssg.templates import content_hash, fill_slots, render_page


THIN_CONTENT_MIN_CHARS = 350


def is_thin(html: str) -> bool:
    # Rough text length without tags
    import re

    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return len(text) < THIN_CONTENT_MIN_CHARS


def schema_org_jsonld(site: SiteManifest, page: PageManifest, context: dict[str, Any]) -> dict:
    phone = context.get("phone") or (site.contacts or {}).get("phone", "")
    graph: list[dict[str, Any]] = [
        {
            "@type": "LocalBusiness",
            "name": fill_slots(page.h1_template, context) or site.domain,
            "url": f"https://{site.domain}/",
            "telephone": phone,
            "address": {"@type": "PostalAddress", "addressLocality": context.get("city_nom", "")},
        },
        {
            "@type": "Service",
            "name": page.service,
            "areaServed": context.get("city_nom", ""),
        },
        {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Главная", "item": f"https://{site.domain}/"},
                {
                    "@type": "ListItem",
                    "position": 2,
                    "name": fill_slots(page.h1_template, context),
                    "item": f"https://{site.domain}/{page.slug.strip('/')}/",
                },
            ],
        },
    ]
    faq = _extract_faq(page, context)
    if faq:
        graph.append(
            {
                "@type": "FAQPage",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": item["q"],
                        "acceptedAnswer": {"@type": "Answer", "text": item["a"]},
                    }
                    for item in faq
                ],
            }
        )
    return {"@context": "https://schema.org", "@graph": graph}


def _extract_faq(page: PageManifest, context: dict[str, Any]) -> list[dict[str, str]]:
    """Pull FAQ from schema_org, block props, or generate from service/city."""
    custom = (page.schema_org or {}).get("faq")
    if isinstance(custom, list) and custom:
        out = []
        for item in custom[:12]:
            if isinstance(item, dict) and item.get("q") and item.get("a"):
                out.append({"q": str(item["q"]), "a": str(item["a"])})
        if out:
            return out
    for block in page.blocks:
        if block.type == "faq":
            items = (block.props or {}).get("items") or []
            out = []
            for item in items[:12]:
                if isinstance(item, dict) and item.get("q") and item.get("a"):
                    out.append({"q": fill_slots(str(item["q"]), context), "a": fill_slots(str(item["a"]), context)})
            if out:
                return out
    city = context.get("city_prep") or context.get("city_nom") or ""
    service = page.service or "услуга"
    return [
        {
            "q": f"Сколько стоит {service} в {city}?" if city else f"Сколько стоит {service}?",
            "a": f"Стоимость {service} зависит от объёма работ. Оставьте заявку — рассчитаем за 15 минут.",
        },
        {
            "q": f"Как быстро выполнить {service}?",
            "a": "Обычно выезд в день обращения. Точные сроки согласуем после короткой диагностики.",
        },
    ]


def render_robots_txt(*, allow_ai_search: bool = True) -> str:
    lines = ["User-agent: *", "Allow: /", "Sitemap: /sitemap.xml"]
    # Training crawlers blocked; AI search optional (TZ 7.4)
    for bot in ("GPTBot", "CCBot", "Google-Extended"):
        lines += [f"User-agent: {bot}", "Disallow: /"]
    if allow_ai_search:
        lines += ["User-agent: OAI-SearchBot", "Allow: /"]
    return "\n".join(lines) + "\n"


def render_sitemap(domain: str, urls: list[str]) -> str:
    # Chunking by 10k handled by caller; this builds one sitemap body
    items = []
    for u in urls:
        loc = u if u.startswith("http") else f"https://{domain}/{u.strip('/')}/"
        items.append(f"  <url><loc>{loc}</loc></url>")
    body = "\n".join(items)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{body}\n</urlset>\n"
    )


def write_precompressed(path: Path) -> None:
    data = path.read_bytes()
    gz = path.with_suffix(path.suffix + ".gz")
    with gzip.open(gz, "wb", compresslevel=6) as f:
        f.write(data)


class SiteBuilder:
    """Disk-backed SSG with SEO artifacts, thin-content guard, precompress."""

    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root

    def site_dir(self, site_id: str) -> Path:
        return self.output_root / site_id / "current"

    def build(
        self,
        site: SiteManifest,
        context: dict | None = None,
        *,
        index_states: dict[str, str] | None = None,
        compress: bool = True,
    ) -> dict[str, Any]:
        site_dir = self.site_dir(str(site.site_id))
        site_dir.mkdir(parents=True, exist_ok=True)
        hashes: list[str] = []
        indexed_urls: list[str] = []
        page_meta: list[dict[str, Any]] = []
        ctx = {**(context or {})}

        for page in site.pages:
            html = render_page(site, page, ctx)
            schema = schema_org_jsonld(site, page, ctx)
            # Inject JSON-LD before </head>
            ld = f'<script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script>'
            html = html.replace("</head>", f"  {ld}\n</head>")

            thin = is_thin(html)
            slug = page.slug.strip("/") or ""
            rel = slug or "index"
            state = (index_states or {}).get(page.slug, page.index_state.value if hasattr(page.index_state, "value") else str(page.index_state))
            if thin:
                state = IndexState.NOINDEX.value

            # X-Robots via meta for static hosting fallback (Caddy map is primary)
            if state != IndexState.INDEXED.value:
                robots_meta = '<meta name="robots" content="noindex, follow">'
                html = html.replace("</head>", f"  {robots_meta}\n</head>")

            hashes.append(content_hash(html))
            out = site_dir / rel / "index.html"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(html, encoding="utf-8")
            if compress:
                write_precompressed(out)

            url_path = f"/{slug}/" if slug else "/"
            if state == IndexState.INDEXED.value:
                indexed_urls.append(url_path)
            page_meta.append(
                {
                    "slug": page.slug,
                    "path": url_path,
                    "index_state": state,
                    "thin": thin,
                    "content_chars": len(html),
                    "hash": hashes[-1],
                }
            )

        (site_dir / "robots.txt").write_text(render_robots_txt(), encoding="utf-8")
        (site_dir / "sitemap.xml").write_text(
            render_sitemap(site.domain, indexed_urls), encoding="utf-8"
        )
        write_legal_pack(site_dir, site.legal or {})
        # IndexNow key file placeholder written by domain service
        build_hash = hashlib.sha256("".join(hashes).encode()).hexdigest()
        (site_dir / "BUILD_HASH").write_text(build_hash, encoding="utf-8")
        (site_dir / "pages_meta.json").write_text(
            json.dumps(page_meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {
            "build_hash": build_hash,
            "pages": page_meta,
            "indexed_count": len(indexed_urls),
        }

    def rollback(self, site_id: str, previous_hash: str) -> bool:
        """Atomic swap current ↔ previous; verify BUILD_HASH matches."""
        root = self.output_root / site_id
        prev = root / "previous"
        current = root / "current"
        if not prev.exists():
            return False
        # Swap
        backup = root / "rollback_tmp"
        if current.exists():
            current.rename(backup)
        prev.rename(current)
        if backup.exists():
            backup.rename(prev)
        marker = current / "BUILD_HASH"
        return marker.exists() and marker.read_text(encoding="utf-8").strip() == previous_hash
