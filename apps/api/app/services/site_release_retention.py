from __future__ import annotations

import asyncio
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.config import get_settings
from app.models import Site, SiteBuild
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

_HASH = re.compile(r"^[a-f0-9]{64}$")
_RETENTION_DAYS = 90
_KEEP_NEWEST = 3
_PROTECTED_BUILD_STATUSES = ("queued", "running", "ready")


def _release_hash(releases_root: Path, release: Path) -> str | None:
    if not _HASH.fullmatch(release.name) or release.is_symlink() or not release.is_dir():
        return None
    try:
        resolved_root = releases_root.resolve(strict=True)
        resolved_release = release.resolve(strict=True)
        resolved_release.relative_to(resolved_root)
        marker = resolved_release / "BUILD_HASH"
        if marker.is_symlink() or marker.read_text(encoding="utf-8").strip() != release.name:
            return None
    except (OSError, ValueError):
        return None
    return release.name


def _linked_release_hash(site_root: Path, releases_root: Path, name: str) -> str | None:
    link = site_root / name
    try:
        if not link.is_symlink():
            return None
        target = link.resolve(strict=True)
        target.relative_to(releases_root.resolve(strict=True))
    except (OSError, ValueError):
        return None
    return _release_hash(releases_root, target)


def prune_site_releases(
    site_root: Path,
    *,
    protected_hashes: set[str],
    now: datetime | None = None,
) -> int:
    """Delete only verified, unreferenced stale immutable release directories."""
    try:
        resolved_site_root = site_root.resolve(strict=True)
        releases_root = resolved_site_root / "releases"
        if releases_root.is_symlink() or not releases_root.is_dir():
            return 0
        releases_root = releases_root.resolve(strict=True)
    except OSError:
        return 0

    protected = {item for item in protected_hashes if _HASH.fullmatch(item)}
    for link_name in ("current", "previous"):
        linked = _linked_release_hash(resolved_site_root, releases_root, link_name)
        if linked:
            protected.add(linked)

    releases: list[tuple[Path, float, str]] = []
    try:
        for release in releases_root.iterdir():
            release_hash = _release_hash(releases_root, release)
            if release_hash:
                releases.append((release, release.stat().st_mtime, release_hash))
    except OSError:
        return 0

    releases.sort(key=lambda item: (item[1], item[2]), reverse=True)
    protected.update(item[2] for item in releases[:_KEEP_NEWEST])
    expiration = (now or datetime.now(UTC)) - timedelta(days=_RETENTION_DAYS)
    cutoff = expiration.timestamp()
    deleted = 0
    for release, modified_at, release_hash in releases:
        if release_hash in protected or modified_at >= cutoff:
            continue
        try:
            if _release_hash(releases_root, release) != release_hash:
                continue
            if any(item.is_symlink() for item in release.rglob("*")):
                continue
            shutil.rmtree(release)
            deleted += 1
        except OSError:
            continue
    return deleted


async def prune_stale_site_releases(db: AsyncSession) -> int:
    sites = list((await db.execute(select(Site))).scalars())
    deleted = 0
    for site in sites:
        hashes = {
            item
            for item in (site.build_hash, site.previous_build_hash)
            if item and _HASH.fullmatch(item)
        }
        build_hashes = list(
            (
                await db.execute(
                    select(SiteBuild.build_hash, SiteBuild.previous_build_hash).where(
                        SiteBuild.site_id == site.id,
                        or_(
                            SiteBuild.status.in_(_PROTECTED_BUILD_STATUSES),
                            SiteBuild.first_published_at.is_not(None),
                        ),
                    )
                )
            ).all()
        )
        hashes.update(
            item
            for build_hash, previous_build_hash in build_hashes
            for item in (build_hash, previous_build_hash)
            if item and _HASH.fullmatch(item)
        )
        deleted += await asyncio.to_thread(
            prune_site_releases,
            Path(get_settings().sites_root) / str(site.id),
            protected_hashes=hashes,
        )
    return deleted
