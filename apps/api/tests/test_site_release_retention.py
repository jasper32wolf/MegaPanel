from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.services.site_release_retention import prune_site_releases


def _release(releases_root: Path, build_hash: str, *, modified_at: float) -> Path:
    release = releases_root / build_hash
    release.mkdir(parents=True)
    (release / "BUILD_HASH").write_text(build_hash, encoding="utf-8")
    (release / "index.html").write_text("<html></html>", encoding="utf-8")
    os.utime(release, (modified_at, modified_at))
    return release


def test_release_collector_keeps_references_recent_and_newest_three(tmp_path):
    site_root = tmp_path / "site"
    releases = site_root / "releases"
    releases.mkdir(parents=True)
    now = datetime(2026, 10, 11, tzinfo=UTC)
    old = (now - timedelta(days=91)).timestamp()
    recent = (now - timedelta(days=1)).timestamp()
    hashes = [f"{number:064x}" for number in range(1, 8)]

    active = _release(releases, hashes[0], modified_at=old)
    candidate = _release(releases, hashes[1], modified_at=old)
    obsolete = _release(releases, hashes[2], modified_at=old)
    _release(releases, hashes[3], modified_at=recent)
    _release(releases, hashes[4], modified_at=recent + 1)
    _release(releases, hashes[5], modified_at=recent + 2)
    _release(releases, hashes[6], modified_at=recent + 3)
    assert (
        prune_site_releases(
            site_root,
            protected_hashes={active.name, candidate.name},
            now=now,
        )
        == 1
    )
    assert active.exists()
    assert candidate.exists()
    assert not obsolete.exists()
    assert all((releases / build_hash).exists() for build_hash in hashes[3:])


def test_release_collector_refuses_symlinked_and_invalid_artifacts(tmp_path):
    site_root = tmp_path / "site"
    releases = site_root / "releases"
    releases.mkdir(parents=True)
    now = datetime(2026, 10, 11, tzinfo=UTC)
    old = (now - timedelta(days=91)).timestamp()
    safe_hashes = [f"{number:064x}" for number in range(1, 5)]
    for build_hash in safe_hashes:
        _release(releases, build_hash, modified_at=old + int(build_hash, 16))

    protected_by_child_link = _release(releases, f"{5:064x}", modified_at=old)
    outside = tmp_path / "outside"
    outside.mkdir()
    child_linked = True
    try:
        (protected_by_child_link / "asset").symlink_to(outside, target_is_directory=True)
    except OSError:
        child_linked = False
    invalid = releases / "not-a-build"
    invalid.mkdir()
    (invalid / "BUILD_HASH").write_text("wrong", encoding="utf-8")
    escaped = releases / f"{6:064x}"
    try:
        escaped.symlink_to(outside, target_is_directory=True)
    except OSError:
        pass

    deleted = prune_site_releases(site_root, protected_hashes=set(), now=now)

    assert deleted >= 1
    if child_linked:
        assert protected_by_child_link.exists()
    assert invalid.exists()
    assert not escaped.exists() or escaped.is_symlink()
    assert outside.exists()
