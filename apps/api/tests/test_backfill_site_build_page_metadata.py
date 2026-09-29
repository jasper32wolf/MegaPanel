from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sys
from argparse import Namespace
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.core import config
from app.db import session as db_session
from app.services import audit
from site_panel_shared.manifests import SiteManifest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "backfill_site_build_page_metadata.py"
)
spec = importlib.util.spec_from_file_location("backfill_site_build_page_metadata", SCRIPT_PATH)
assert spec and spec.loader
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)


class Result:
    def __init__(self, values: list[object]):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values

    def scalar_one_or_none(self):
        return self.values[0] if self.values else None


class Database:
    def __init__(self, results: list[Result]):
        self.results = results
        self.statements: list[object] = []
        self.committed = False

    async def execute(self, statement):
        self.statements.append(statement)
        return self.results.pop(0)

    async def commit(self):
        self.committed = True


def arguments(*, apply: bool = False, tenant_id=None) -> Namespace:
    return Namespace(apply=apply, tenant_id=tenant_id)


def build_fixture(root: Path, *, metadata=None, html: str | None = None):
    site_id, tenant_id, build_id = uuid4(), uuid4(), uuid4()
    snapshot = SiteManifest.model_validate(
        {
            "site_id": site_id,
            "tenant_id": tenant_id,
            "domain": "example.test",
            "pages": [
                {
                    "slug": "/",
                    "title_template": "Ремонт",
                    "h1_template": "Ремонт",
                    "service": "Ремонт",
                }
            ],
        }
    ).model_dump(mode="json")
    page_html = html or (
        '<!DOCTYPE html><link rel="canonical" href="https://example.test/">'
        "<main>Проверенный выпуск</main>"
    )
    digest = hashlib.sha256(page_html.encode()).hexdigest()
    pages = metadata or [
        {
            "slug": "/",
            "path": "/",
            "index_state": "indexed",
            "thin": False,
            "content_chars": len(page_html),
            "hash": digest,
        }
    ]
    build_hash = "a" * 64
    release = root / str(site_id) / "releases" / build_hash
    release.mkdir(parents=True)
    (release / "BUILD_HASH").write_text(build_hash, encoding="utf-8")
    (release / "index.html").write_text(page_html, encoding="utf-8")
    (release / "pages_meta.json").write_text(json.dumps(pages), encoding="utf-8")
    (release / "sitemap.xml").write_text(
        '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://example.test/</loc></url></urlset>",
        encoding="utf-8",
    )
    return SimpleNamespace(
        id=build_id,
        site_id=site_id,
        tenant_id=tenant_id,
        build_hash=build_hash,
        status="ready",
        pages_built=1,
        manifest_snapshot=snapshot,
        page_metadata_snapshot=None,
    ), pages


def patch_dependencies(monkeypatch, db: Database, root: Path, audits: list[dict]):
    @asynccontextmanager
    async def fake_session():
        yield db

    monkeypatch.setattr(db_session, "open_db_session", fake_session)
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(sites_root=str(root)))

    async def append_audit(*_args, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(audit, "append_audit", append_audit)


def test_parse_args_defaults_to_all_tenant_dry_run(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["backfill_site_build_page_metadata.py"])

    parsed = backfill.parse_args()

    assert parsed.apply is False
    assert parsed.tenant_id is None


def test_metadata_contract_rejects_noncanonical_or_tampered_records():
    from app.services.site_build_metadata import validate_page_metadata_snapshot

    invalid = {
        "slug": "/nested",
        "path": "/nested",
        "index_state": "indexed",
        "thin": False,
        "content_chars": 1,
        "hash": "A" * 64,
    }

    with pytest.raises(ValueError, match="snapshot is invalid"):
        validate_page_metadata_snapshot([invalid])
    invalid.update({"path": "/nested/", "hash": "a" * 64, "thin": True})
    with pytest.raises(ValueError, match="snapshot is invalid"):
        validate_page_metadata_snapshot([invalid])


def test_dry_run_verifies_release_without_writing(monkeypatch, tmp_path: Path):
    build, pages = build_fixture(tmp_path)
    db = Database([Result([build])])
    audits: list[dict] = []
    patch_dependencies(monkeypatch, db, tmp_path, audits)

    result = asyncio.run(backfill.backfill(arguments()))

    assert result == {"would_backfill": 1}
    assert build.page_metadata_snapshot is None
    assert db.committed is False
    assert audits == []
    assert pages[0]["slug"] == "/"


def test_apply_writes_only_verified_snapshot_and_is_idempotent(monkeypatch, tmp_path: Path):
    build, pages = build_fixture(tmp_path)
    db = Database([Result([build]), Result([build])])
    audits: list[dict] = []
    patch_dependencies(monkeypatch, db, tmp_path, audits)

    result = asyncio.run(backfill.backfill(arguments(apply=True)))

    assert result == {"backfilled": 1}
    assert build.page_metadata_snapshot == pages
    assert db.committed is True
    assert audits[0]["action"] == "site_build_page_metadata_backfill.apply"
    assert audits[0]["payload"]["build_id"] == str(build.id)
    assert "FOR UPDATE" in str(db.statements[1])

    repeat_db = Database([Result([build])])
    patch_dependencies(monkeypatch, repeat_db, tmp_path, [])
    assert asyncio.run(backfill.backfill(arguments(apply=True))) == {"already_populated": 1}
    assert repeat_db.committed is False


def test_tampered_artifacts_are_skipped_without_writes(monkeypatch, tmp_path: Path):
    build, _ = build_fixture(tmp_path)
    release = tmp_path / str(build.site_id) / "releases" / build.build_hash
    (release / "index.html").write_text("tampered", encoding="utf-8")
    db = Database([Result([build])])
    audits: list[dict] = []
    patch_dependencies(monkeypatch, db, tmp_path, audits)

    result = asyncio.run(backfill.backfill(arguments(apply=True)))

    assert result == {"content_chars_mismatch": 1}
    assert build.page_metadata_snapshot is None
    assert db.committed is False
    assert audits == []


def test_backfill_scopes_build_scan_to_requested_tenant(monkeypatch, tmp_path: Path):
    build, _ = build_fixture(tmp_path)
    db = Database([Result([])])
    patch_dependencies(monkeypatch, db, tmp_path, [])

    asyncio.run(backfill.backfill(arguments(tenant_id=build.tenant_id)))

    compiled = db.statements[0].compile()
    assert compiled.params["tenant_id_1"] == build.tenant_id
