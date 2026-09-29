#!/usr/bin/env python3
"""Backfill verified page metadata for legacy static-site builds.

Run a dry-run first:
    python scripts/backfill_site_build_page_metadata.py

Apply only after reviewing a bounded scope:
    python scripts/backfill_site_build_page_metadata.py --tenant-id <UUID> --apply
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

_BUILD_HASH = re.compile(r"[a-f0-9]{64}")
_ROBOTS = '<meta name="robots" content="noindex, follow">'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill only verified legacy SiteBuild page metadata snapshots."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write verified metadata snapshots after reviewing the dry-run output.",
    )
    parser.add_argument(
        "--tenant-id",
        type=UUID,
        help="Limit the scan to one tenant UUID. By default all tenants are scanned.",
    )
    return parser.parse_args()


def _label(build: object) -> str:
    return f"build_id={build.id} site_id={build.site_id} build_hash={build.build_hash}"


def _safe_file(root: Path, path: Path) -> Path:
    resolved = path.resolve(strict=True)
    if root not in resolved.parents or not resolved.is_file() or path.is_symlink():
        raise ValueError("unsafe_release_artifact")
    return resolved


def _canonical_urls(html: str) -> list[str]:
    return re.findall(r'<link rel="canonical" href="([^"]+)">', html)


def _verify_release(build: object, sites_root: Path) -> list[dict]:
    from app.services.site_build_metadata import (
        canonical_page_path,
        validate_page_metadata_snapshot,
    )
    from site_panel_shared.manifests import SiteManifest

    if not isinstance(build.build_hash, str) or not _BUILD_HASH.fullmatch(build.build_hash):
        raise ValueError("invalid_build_hash")
    try:
        manifest = SiteManifest.model_validate(build.manifest_snapshot)
    except ValueError as exc:
        raise ValueError("invalid_manifest_snapshot") from exc
    if manifest.site_id != build.site_id or manifest.tenant_id != build.tenant_id:
        raise ValueError("build_identity_mismatch")
    releases = (sites_root / str(build.site_id) / "releases").resolve()
    release = releases / build.build_hash
    try:
        release_root = release.resolve(strict=True)
        if (
            releases not in release_root.parents
            or release.is_symlink()
            or not release_root.is_dir()
        ):
            raise ValueError("unsafe_release_artifact")
        marker = _safe_file(release_root, release_root / "BUILD_HASH")
        metadata_file = _safe_file(release_root, release_root / "pages_meta.json")
        if marker.read_text(encoding="utf-8").strip() != build.build_hash:
            raise ValueError("invalid_release_marker")
        raw = json.loads(metadata_file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("missing_release_artifact") from exc
    except UnicodeDecodeError as exc:
        raise ValueError("invalid_release_artifact_encoding") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_pages_meta") from exc
    try:
        by_slug = validate_page_metadata_snapshot(
            raw, expected_slugs={page.slug for page in manifest.pages}
        )
    except ValueError as exc:
        raise ValueError("invalid_pages_meta") from exc
    if build.pages_built and build.pages_built != len(raw):
        raise ValueError("pages_built_mismatch")

    indexed_urls: set[str] = set()
    for slug, item in by_slug.items():
        path = canonical_page_path(slug)
        html_path = (
            release_root / "index.html"
            if path == "/"
            else release_root / path.strip("/") / "index.html"
        )
        try:
            html = _safe_file(release_root, html_path).read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ValueError("missing_page_artifact") from exc
        except UnicodeDecodeError as exc:
            raise ValueError("invalid_release_artifact_encoding") from exc
        if len(html) != item["content_chars"]:
            raise ValueError("content_chars_mismatch")
        if hashlib.sha256(html.encode("utf-8")).hexdigest() != item["hash"]:
            raise ValueError("html_hash_mismatch")
        canonicals = _canonical_urls(html)
        expected_url = f"https://{manifest.domain}{path}"
        if canonicals != [expected_url]:
            raise ValueError("canonical_mismatch")
        has_noindex = _ROBOTS in html
        if item["index_state"] == "indexed":
            if has_noindex:
                raise ValueError("robots_mismatch")
            indexed_urls.add(expected_url)
        elif not has_noindex:
            raise ValueError("robots_mismatch")

    try:
        sitemap_file = _safe_file(release_root, release_root / "sitemap.xml")
        sitemap = ET.fromstring(sitemap_file.read_text(encoding="utf-8"))
        sitemap_urls = [
            item.text or ""
            for item in sitemap.findall(
                "{http://www.sitemaps.org/schemas/sitemap/0.9}url/"
                "{http://www.sitemaps.org/schemas/sitemap/0.9}loc"
            )
        ]
    except (ET.ParseError, UnicodeDecodeError, FileNotFoundError) as exc:
        raise ValueError("sitemap_mismatch") from exc
    if set(sitemap_urls) != indexed_urls or len(sitemap_urls) != len(indexed_urls):
        raise ValueError("sitemap_mismatch")
    return raw


async def backfill(args: argparse.Namespace) -> dict[str, int]:
    from app.core.config import get_settings
    from app.db.session import open_db_session
    from app.models.publish import SiteBuild
    from app.services.audit import append_audit
    from sqlalchemy import select

    mode = "apply" if args.apply else "dry-run"
    counts: Counter[str] = Counter()
    sites_root = await asyncio.to_thread(lambda: Path(get_settings().sites_root).resolve())
    async with open_db_session() as db:
        statement = select(SiteBuild).order_by(SiteBuild.id)
        if args.tenant_id is not None:
            statement = statement.where(SiteBuild.tenant_id == args.tenant_id)
        builds = list((await db.execute(statement)).scalars().all())
        for build in builds:
            label = _label(build)
            if build.page_metadata_snapshot is not None:
                counts["already_populated"] += 1
                print(f"SKIP already_populated {label}")
                continue
            if build.status not in {"ready", "published", "rolled_back"}:
                counts["ineligible_status"] += 1
                print(f"SKIP ineligible_status {label} status={build.status}")
                continue
            try:
                metadata = _verify_release(build, sites_root)
            except ValueError as exc:
                counts[str(exc)] += 1
                print(f"SKIP {exc} {label}")
                continue
            if not args.apply:
                counts["would_backfill"] += 1
                print(f"WOULD BACKFILL {label} pages={len(metadata)}")
                continue

            locked = (
                await db.execute(
                    select(SiteBuild).where(SiteBuild.id == build.id).with_for_update()
                )
            ).scalar_one_or_none()
            if (
                not locked
                or locked.page_metadata_snapshot is not None
                or locked.site_id != build.site_id
                or locked.tenant_id != build.tenant_id
                or locked.build_hash != build.build_hash
                or locked.status not in {"ready", "published", "rolled_back"}
            ):
                counts["race_or_already_populated"] += 1
                print(f"SKIP race_or_already_populated {label}")
                continue
            try:
                metadata = _verify_release(locked, sites_root)
            except ValueError as exc:
                counts[str(exc)] += 1
                print(f"SKIP {exc} {label}")
                continue
            locked.page_metadata_snapshot = metadata
            digest = hashlib.sha256(
                json.dumps(
                    metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            await append_audit(
                db,
                action="site_build_page_metadata_backfill.apply",
                payload={
                    "build_id": str(locked.id),
                    "site_id": str(locked.site_id),
                    "build_hash": locked.build_hash,
                    "pages": len(metadata),
                    "metadata_sha256": digest,
                    "mode": mode,
                },
                tenant_id=locked.tenant_id,
                actor_id=None,
            )
            counts["backfilled"] += 1
            print(f"BACKFILLED {label} pages={len(metadata)}")
        if args.apply and counts["backfilled"]:
            await db.commit()
    summary = dict(sorted(counts.items()))
    print(f"SUMMARY mode={mode} scanned={len(builds)} {summary}")
    return summary


def main() -> None:
    asyncio.run(backfill(parse_args()))


if __name__ == "__main__":
    main()
