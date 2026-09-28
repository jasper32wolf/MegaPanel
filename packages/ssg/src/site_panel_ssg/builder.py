from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any
from uuid import UUID

from site_panel_shared.enums import IndexState
from site_panel_shared.manifests import PageManifest, SiteManifest

from site_panel_ssg.legal import COOKIE_BANNER_JS, write_legal_pack
from site_panel_ssg.templates import content_hash, fill_slots, page_url, render_page

THIN_CONTENT_MIN_CHARS = 350
LEAD_FORM_MIN_AGE_MS = 2500


@dataclass(frozen=True)
class BuildAsset:
    asset_id: UUID
    source_path: Path
    stored_sha256: str
    content_type: str = "image/webp"


LEAD_FORM_SCRIPT = """(() => {
  const query = new URLSearchParams(window.location.search);
  const minFormAge = 2500;
  const setIdempotencyKey = (form) => {
    form.dataset.idempotencyKey = window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  };
  const prepareForm = (form) => {
    const input = form.elements.namedItem("form_ts");
    const button = form.querySelector('button[type="submit"]');
    const readyAt = Date.now() + minFormAge;
    if (input) input.value = String(Date.now() / 1000);
    form.dataset.readyAt = String(readyAt);
    setIdempotencyKey(form);
    if (button) {
      button.disabled = true;
      window.setTimeout(() => { button.disabled = false; }, minFormAge);
    }
  };
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("form[data-site-panel-lead-form]").forEach((form) => {
      prepareForm(form);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const status = form.querySelector(".sp-lead-status");
        if (Date.now() < Number(form.dataset.readyAt || 0)) {
          if (status) status.textContent = "Подождите несколько секунд перед отправкой.";
          return;
        }
        const data = new FormData(form);
        const button = form.querySelector('button[type="submit"]');
        const utm = Object.fromEntries(
          [...query.entries()].filter(([key]) => key.startsWith("utm_"))
        );
        const payload = {
          site_id: form.dataset.siteId,
          lead_token: form.dataset.leadToken,
          phone: data.get("phone"),
          email: data.get("email") || null,
          name: data.get("name") || null,
          message: data.get("message") || null,
          page_slug: window.location.pathname,
          website: data.get("website") || null,
          form_ts: Number(data.get("form_ts")),
          idempotency_key: form.dataset.idempotencyKey,
          utm,
          consent: data.has("consent"),
        };
        if (button) button.disabled = true;
        if (status) status.textContent = "Отправляем заявку…";
        try {
          const response = await fetch(form.dataset.endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
          if (!response.ok) throw new Error("lead_submit_failed");
          form.reset();
          prepareForm(form);
          if (status) status.textContent = "Заявка отправлена. Мы скоро свяжемся с вами.";
        } catch {
          if (status) status.textContent = "Не удалось отправить заявку. Попробуйте ещё раз.";
        } finally {
          if (button) button.disabled = false;
        }
      });
    });
  });
})();
"""


def is_thin(html: str) -> bool:
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
            "url": page_url(site.domain, "/"),
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
                {
                    "@type": "ListItem",
                    "position": 1,
                    "name": "Главная",
                    "item": page_url(site.domain, "/"),
                },
                {
                    "@type": "ListItem",
                    "position": 2,
                    "name": fill_slots(page.h1_template, context),
                    "item": page_url(site.domain, page.slug),
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
                    out.append(
                        {
                            "q": fill_slots(str(item["q"]), context),
                            "a": fill_slots(str(item["a"]), context),
                        }
                    )
            if out:
                return out
    city = context.get("city_prep") or context.get("city_nom") or ""
    service = page.service or "услуга"
    return [
        {
            "q": f"Сколько стоит {service} в {city}?" if city else f"Сколько стоит {service}?",
            "a": (
                f"Стоимость {service} зависит от объёма работ. "
                "Оставьте заявку, чтобы уточнить условия."
            ),
        },
        {
            "q": f"Как быстро выполнить {service}?",
            "a": ("Сроки зависят от задачи. Точные условия согласуем после уточнения деталей."),
        },
    ]


def render_robots_txt(*, allow_ai_search: bool = True) -> str:
    lines = ["User-agent: *", "Allow: /", "Sitemap: /sitemap.xml"]
    for bot in ("GPTBot", "CCBot", "Google-Extended"):
        lines += [f"User-agent: {bot}", "Disallow: /"]
    if allow_ai_search:
        lines += ["User-agent: OAI-SearchBot", "Allow: /"]
    return "\n".join(lines) + "\n"


def render_sitemap(domain: str, urls: list[str]) -> str:
    items = []
    for url in urls:
        loc = url if url.startswith("http") else page_url(domain, url)
        items.append(f"  <url><loc>{escape(loc)}</loc></url>")
    body = "\n".join(items)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{body}\n</urlset>\n"
    )


def _validate_release_artifacts(staging: Path, site: SiteManifest, page_meta: list[dict]) -> None:
    expected_indexed_urls = {
        page_url(site.domain, item["path"])
        for item in page_meta
        if item["index_state"] == IndexState.INDEXED.value
    }
    sitemap = ET.fromstring((staging / "sitemap.xml").read_text(encoding="utf-8"))
    sitemap_urls = {
        item.text or ""
        for item in sitemap.findall(
            "{http://www.sitemaps.org/schemas/sitemap/0.9}url/{http://www.sitemaps.org/schemas/sitemap/0.9}loc"
        )
    }
    if sitemap_urls != expected_indexed_urls or len(sitemap_urls) != len(expected_indexed_urls):
        raise ValueError("Sitemap URLs do not match indexed page artifacts")
    for item in page_meta:
        slug = item["slug"].strip("/")
        output = staging / "index.html" if not slug else staging / slug / "index.html"
        html = output.read_text(encoding="utf-8")
        canonical = page_url(site.domain, item["slug"])
        if f'<link rel="canonical" href="{canonical}">' not in html:
            raise ValueError("Page canonical does not match its release path")
        has_noindex = '<meta name="robots" content="noindex, follow">' in html
        if item["index_state"] == IndexState.INDEXED.value and has_noindex:
            raise ValueError("Indexed page artifact contains noindex")
        if item["index_state"] != IndexState.INDEXED.value and not has_noindex:
            raise ValueError("Noindex page artifact is missing robots policy")


def write_precompressed(path: Path) -> None:
    data = path.read_bytes()
    with gzip.open(path.with_suffix(path.suffix + ".gz"), "wb", compresslevel=6) as file:
        file.write(data)


def _exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


class SiteBuilder:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root

    def site_dir(self, site_id: str) -> Path:
        return self.output_root / site_id / "current"

    def _replace_link(self, path: Path, target: Path) -> bool:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
        try:
            if _exists(temporary):
                temporary.unlink()
            os.symlink(os.path.relpath(target, path.parent), temporary, target_is_directory=True)
            os.replace(temporary, path)
            return True
        except OSError:
            if _exists(temporary):
                temporary.unlink()
            return False

    def _activate_release(self, root: Path, release: Path) -> None:
        current = root / "current"
        previous = root / "previous"
        if not _exists(current) and self._replace_link(current, release):
            return
        if current.is_symlink():
            previous_target = current.resolve()
            if previous.is_dir() and not previous.is_symlink():
                shutil.rmtree(previous)
            self._replace_link(previous, previous_target)
            if self._replace_link(current, release):
                return

        if _exists(previous):
            if previous.is_dir() and not previous.is_symlink():
                shutil.rmtree(previous)
            else:
                previous.unlink()
        if _exists(current):
            current.rename(previous)
        release.rename(current)

    def activate(self, site_id: str, build_hash: str) -> bool:
        root = self.output_root / str(site_id)
        release = root / "releases" / build_hash
        marker = release / "BUILD_HASH"
        if (
            not release.is_dir()
            or not marker.is_file()
            or marker.read_text(encoding="utf-8").strip() != build_hash
        ):
            return False
        self._activate_release(root, release)
        current_marker = root / "current" / "BUILD_HASH"
        return (
            current_marker.is_file()
            and current_marker.read_text(encoding="utf-8").strip() == build_hash
        )

    def release_path(self, site_id: str, build_hash: str) -> Path:
        return self.output_root / str(site_id) / "releases" / build_hash

    @staticmethod
    def _copy_assets(
        staging: Path, assets: list[BuildAsset]
    ) -> tuple[dict[str, str], list[dict[str, str]]]:
        urls: dict[str, str] = {}
        metadata: list[dict[str, str]] = []
        assets_dir = staging / "assets"
        for asset in sorted(assets, key=lambda item: str(item.asset_id)):
            data = asset.source_path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest != asset.stored_sha256:
                raise ValueError("Static asset hash does not match the approved manifest")
            filename = f"{digest}.webp"
            target = assets_dir / filename
            if not target.exists():
                assets_dir.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            urls[str(asset.asset_id)] = f"assets/{filename}"
            metadata.append(
                {
                    "asset_id": str(asset.asset_id),
                    "stored_sha256": digest,
                    "path": f"assets/{filename}",
                    "content_type": asset.content_type,
                }
            )
        return urls, metadata

    def build(
        self,
        site: SiteManifest,
        context: dict | None = None,
        *,
        index_states: dict[str, str] | None = None,
        assets: list[BuildAsset] | None = None,
        compress: bool = True,
        activate: bool = True,
    ) -> dict[str, Any]:
        root = self.output_root / str(site.site_id)
        releases = root / "releases"
        releases.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".building-", dir=releases))
        hashes: list[str] = []
        indexed_urls: list[str] = []
        page_meta: list[dict[str, Any]] = []
        ctx = {**(context or {}), "site_id": str(site.site_id)}

        try:
            asset_urls, assets_meta = self._copy_assets(staging, assets or [])
            referenced_asset_ids = {
                str(attachment.asset_id) for page in site.pages for attachment in page.media
            }
            if referenced_asset_ids != set(asset_urls):
                raise ValueError("Static media assets do not match the manifest")
            for page in site.pages:
                slug = page.slug.strip("/")
                output = staging / "index.html" if not slug else staging / slug / "index.html"
                relative_media_urls = {
                    asset_id: os.path.relpath(staging / path, output.parent).replace(os.sep, "/")
                    for asset_id, path in asset_urls.items()
                }
                html = render_page(site, page, ctx, relative_media_urls)
                has_lead_form = any(block.type == "lead_form" for block in page.blocks)
                if has_lead_form:
                    html = html.replace(
                        "</body>", '  <script src="site-panel-leads.js" defer></script>\n</body>'
                    )
                html = html.replace(
                    "</body>", '  <script src="cookie-banner.js" defer></script>\n</body>'
                )
                schema = json.dumps(schema_org_jsonld(site, page, ctx), ensure_ascii=False).replace(
                    "</", "<\\/"
                )
                html = html.replace(
                    "</head>", f'  <script type="application/ld+json">{schema}</script>\n</head>'
                )

                thin = is_thin(html)
                slug = page.slug.strip("/")
                state = (index_states or {}).get(
                    page.slug,
                    page.index_state.value
                    if hasattr(page.index_state, "value")
                    else str(page.index_state),
                )
                if thin:
                    state = IndexState.NOINDEX.value
                if state != IndexState.INDEXED.value:
                    html = html.replace(
                        "</head>", '  <meta name="robots" content="noindex, follow">\n</head>'
                    )

                hashes.append(content_hash(html))
                output = staging / "index.html" if not slug else staging / slug / "index.html"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(html, encoding="utf-8")
                (output.parent / "cookie-banner.js").write_text(COOKIE_BANNER_JS, encoding="utf-8")
                if has_lead_form:
                    (output.parent / "site-panel-leads.js").write_text(
                        LEAD_FORM_SCRIPT, encoding="utf-8"
                    )
                if compress:
                    write_precompressed(output)

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

            (staging / "robots.txt").write_text(render_robots_txt(), encoding="utf-8")
            (staging / "sitemap.xml").write_text(
                render_sitemap(site.domain, indexed_urls), encoding="utf-8"
            )
            _validate_release_artifacts(staging, site, page_meta)
            write_legal_pack(staging, site.legal or {})
            assets_hash = hashlib.sha256(
                json.dumps(
                    assets_meta, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            hashes.append(assets_hash)
            build_hash = hashlib.sha256("".join(hashes).encode()).hexdigest()
            (staging / "BUILD_HASH").write_text(build_hash, encoding="utf-8")
            (staging / "assets_meta.json").write_text(
                json.dumps(assets_meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (staging / "pages_meta.json").write_text(
                json.dumps(page_meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            release = releases / build_hash
            if _exists(release):
                shutil.rmtree(staging)
            else:
                staging.rename(release)
            if activate:
                self._activate_release(root, release)
            return {
                "build_hash": build_hash,
                "pages": page_meta,
                "indexed_count": len(indexed_urls),
                "release_path": str(release),
                "activated": activate,
            }
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise

    def rollback(self, site_id: str, previous_hash: str) -> bool:
        root = self.output_root / site_id
        current = root / "current"
        previous = root / "previous"
        if not _exists(previous):
            return False

        marker = previous / "BUILD_HASH"
        if not marker.exists() or marker.read_text(encoding="utf-8").strip() != previous_hash:
            return False

        if current.is_symlink() and previous.is_symlink():
            current_target = current.resolve()
            previous_target = previous.resolve()
            if self._replace_link(current, previous_target):
                self._replace_link(previous, current_target)
                return True

        backup = root / "rollback_tmp"
        if _exists(backup):
            if backup.is_dir() and not backup.is_symlink():
                shutil.rmtree(backup)
            else:
                backup.unlink()
        if _exists(current):
            current.rename(backup)
        previous.rename(current)
        if _exists(backup):
            backup.rename(previous)
        marker = current / "BUILD_HASH"
        return marker.exists() and marker.read_text(encoding="utf-8").strip() == previous_hash
