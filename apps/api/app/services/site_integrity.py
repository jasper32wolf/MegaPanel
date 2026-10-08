from __future__ import annotations

import hashlib
from pathlib import Path

from app.core.config import get_settings
from app.models import AlertIncident, Site
from app.services.operations import observe_alert
from app.services.operator_alerts import create_operator_alert
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_MAX_FILES = 10_000
_MAX_BYTES = 512 * 1024 * 1024


def _release_hash(release: Path) -> str | None:
    try:
        root = release.resolve(strict=True)
    except OSError:
        return None
    if not root.is_dir():
        return None
    digest = hashlib.sha256()
    files = 0
    total_bytes = 0
    try:
        for artifact in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
            if artifact.is_symlink():
                return None
            if not artifact.is_file():
                continue
            files += 1
            total_bytes += artifact.stat().st_size
            if files > _MAX_FILES or total_bytes > _MAX_BYTES:
                return None
            digest.update(artifact.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            with artifact.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _active_release(site: Site) -> Path | None:
    if not site.build_hash:
        return None
    root = Path(get_settings().sites_root) / str(site.id)
    current = root / "current"
    expected = root / "releases" / site.build_hash
    try:
        if current.resolve(strict=True) != expected.resolve(strict=True):
            return None
    except OSError:
        return None
    marker = expected / "BUILD_HASH"
    try:
        return expected if marker.read_text(encoding="utf-8").strip() == site.build_hash else None
    except OSError:
        return None


async def _observe_integrity(db: AsyncSession, *, site: Site, active: bool) -> None:
    subject_key = str(site.id)
    existing = (
        await db.execute(
            select(AlertIncident.id).where(
                AlertIncident.tenant_id == site.tenant_id,
                AlertIncident.signal_code == "site-integrity-failure",
                AlertIncident.subject_kind == "site",
                AlertIncident.subject_key == subject_key,
                AlertIncident.status.in_(("open", "acknowledged")),
            )
        )
    ).scalar_one_or_none()
    await observe_alert(
        db,
        tenant_id=site.tenant_id,
        signal_code="site-integrity-failure",
        active=active,
        subject_kind="site",
        subject_key=subject_key,
    )
    if active and existing is None:
        create_operator_alert(
            db,
            tenant_id=site.tenant_id,
            category="security",
            signal_code="site-integrity-failure",
            title="Нарушена целостность опубликованного сайта",
            body=(
                "Активный immutable release не соответствует ожидаемому состоянию. "
                "Проверьте инцидент до любых действий с публикацией."
            ),
            subject_kind="site",
            subject_key=subject_key,
        )
    elif not active and existing is not None:
        create_operator_alert(
            db,
            tenant_id=site.tenant_id,
            category="security",
            signal_code="site-integrity-recovered",
            title="Целостность опубликованного сайта восстановлена",
            body="Проверка active immutable release снова проходит успешно.",
            subject_kind="site",
            subject_key=subject_key,
        )


async def verify_published_release_integrity(db: AsyncSession) -> int:
    sites = list(
        (
            await db.execute(
                select(Site)
                .where(Site.publish_state == "published", Site.build_hash.is_not(None))
                .order_by(Site.created_at, Site.id)
            )
        ).scalars()
    )
    for site in sites:
        release = _active_release(site)
        observed = _release_hash(release) if release else None
        if observed is None:
            await _observe_integrity(db, site=site, active=True)
            continue
        if site.integrity_hash is None:
            site.integrity_hash = observed
            await _observe_integrity(db, site=site, active=False)
            continue
        if site.integrity_hash != observed:
            await _observe_integrity(db, site=site, active=True)
        else:
            await _observe_integrity(db, site=site, active=False)
    if sites:
        await db.commit()
    return len(sites)
