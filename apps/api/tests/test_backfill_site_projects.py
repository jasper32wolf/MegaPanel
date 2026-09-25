from __future__ import annotations

import asyncio
import importlib.util
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.db import session as db_session
from app.services import audit

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "backfill_site_projects.py"
spec = importlib.util.spec_from_file_location("backfill_site_projects", SCRIPT_PATH)
assert spec and spec.loader
backfill_site_projects = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill_site_projects)


class QueryResult:
    def __init__(self, values: list[object]):
        self.values = values

    def scalars(self) -> QueryResult:
        return self

    def all(self) -> list[object]:
        return self.values

    def scalar_one_or_none(self) -> object | None:
        return self.values[0] if self.values else None


class FakeDatabase:
    def __init__(self, results: list[QueryResult]):
        self.results = results
        self.added: list[object] = []
        self.statements: list[object] = []
        self.committed = False

    async def execute(self, statement: object) -> QueryResult:
        self.statements.append(statement)
        return self.results.pop(0)

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.committed = True


class FakeSession:
    def __init__(self, db: FakeDatabase):
        self.db = db

    async def __aenter__(self) -> FakeDatabase:
        return self.db

    async def __aexit__(self, *_: object) -> None:
        return None


def legacy_site(**overrides: object) -> SimpleNamespace:
    values = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "project_id": None,
        "domain": "example.test",
        "locale": "ru",
        "niche": "repair",
        "manifest": {"pages": [{"slug": "/"}]},
        "publish_state": "published",
        "build_hash": "a" * 64,
        "previous_build_hash": "b" * 64,
        "version": 7,
        "lead_token": "lead-token",
        "indexnow_key": "indexnow-key",
        "caddy_configured": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def args(**overrides: object) -> Namespace:
    values = {"apply": False, "tenant_id": None}
    values.update(overrides)
    return Namespace(**values)


def patch_dependencies(
    monkeypatch: pytest.MonkeyPatch, db: FakeDatabase, audits: list[dict]
) -> None:
    monkeypatch.setattr(db_session, "open_db_session", lambda: FakeSession(db))

    async def append_audit(*_: object, **kwargs: object) -> None:
        audits.append(kwargs)

    monkeypatch.setattr(audit, "append_audit", append_audit)


def test_parse_args_defaults_to_all_tenant_dry_run(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "argv", ["backfill_site_projects.py"])

    parsed = backfill_site_projects.parse_args()

    assert not parsed.apply
    assert parsed.tenant_id is None


def test_parse_args_validates_tenant_uuid(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "argv", ["backfill_site_projects.py", "--tenant-id", "invalid"])

    with pytest.raises(SystemExit):
        backfill_site_projects.parse_args()


def test_dry_run_reports_candidate_without_writes(monkeypatch: pytest.MonkeyPatch):
    site = legacy_site()
    db = FakeDatabase([QueryResult([site]), QueryResult([]), QueryResult([])])
    audits: list[dict] = []
    patch_dependencies(monkeypatch, db, audits)

    result = asyncio.run(backfill_site_projects.backfill(args()))

    assert result == {"would_create": 1}
    assert not db.added
    assert not db.committed
    assert site.project_id is None
    assert not audits


def test_apply_creates_draft_project_and_preserves_legacy_release_fields(
    monkeypatch: pytest.MonkeyPatch,
):
    site = legacy_site()
    original = {
        name: getattr(site, name)
        for name in (
            "manifest",
            "publish_state",
            "build_hash",
            "previous_build_hash",
            "version",
            "lead_token",
            "indexnow_key",
            "caddy_configured",
        )
    }
    db = FakeDatabase([QueryResult([site]), QueryResult([]), QueryResult([])])
    audits: list[dict] = []
    patch_dependencies(monkeypatch, db, audits)

    result = asyncio.run(backfill_site_projects.backfill(args(apply=True)))

    assert result == {"created": 1}
    project = db.added[0]
    assert project.name == site.domain
    assert project.slug == f"site-{site.id.hex}"
    assert project.tenant_id == site.tenant_id
    assert project.site_id == site.id
    assert project.domain == site.domain
    assert project.locale == site.locale
    assert project.niche == site.niche
    assert project.status == "draft"
    assert site.project_id == project.id
    assert db.committed
    assert audits == [
        {
            "action": "site_project_backfill.apply",
            "payload": {
                "site_id": str(site.id),
                "project_id": str(project.id),
                "slug": f"site-{site.id.hex}",
                "domain": site.domain,
                "mode": "apply",
            },
            "tenant_id": site.tenant_id,
            "actor_id": None,
        }
    ]
    assert {name: getattr(site, name) for name in original} == original


def test_reciprocal_existing_link_is_an_idempotent_noop(monkeypatch: pytest.MonkeyPatch):
    site = legacy_site(project_id=uuid4())
    linked_project = SimpleNamespace(id=site.project_id, site_id=site.id, tenant_id=site.tenant_id)
    db = FakeDatabase([QueryResult([site]), QueryResult([linked_project])])
    audits: list[dict] = []
    patch_dependencies(monkeypatch, db, audits)

    result = asyncio.run(backfill_site_projects.backfill(args(apply=True)))

    assert result == {"already_linked": 1}
    assert not db.added
    assert not db.committed
    assert not audits


@pytest.mark.parametrize(
    ("site", "results", "expected"),
    [
        (
            legacy_site(project_id=uuid4()),
            lambda site: [QueryResult([site]), QueryResult([])],
            "dangling_site_link",
        ),
        (
            legacy_site(project_id=uuid4()),
            lambda site: [
                QueryResult([site]),
                QueryResult(
                    [SimpleNamespace(id=site.project_id, site_id=uuid4(), tenant_id=site.tenant_id)]
                ),
            ],
            "conflicting_site_link",
        ),
        (
            legacy_site(),
            lambda site: [
                QueryResult([site]),
                QueryResult(
                    [SimpleNamespace(id=uuid4(), site_id=site.id, tenant_id=site.tenant_id)]
                ),
            ],
            "reverse_link_conflict",
        ),
        (
            legacy_site(),
            lambda site: [
                QueryResult([site]),
                QueryResult([]),
                QueryResult([SimpleNamespace(id=uuid4())]),
            ],
            "slug_conflict",
        ),
    ],
)
def test_backfill_skips_ambiguous_links_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    site: SimpleNamespace,
    results: object,
    expected: str,
):
    db = FakeDatabase(results(site))
    audits: list[dict] = []
    patch_dependencies(monkeypatch, db, audits)

    result = asyncio.run(backfill_site_projects.backfill(args(apply=True)))

    assert result == {expected: 1}
    assert not db.added
    assert not db.committed
    assert not audits


def test_backfill_scopes_site_scan_to_requested_tenant(monkeypatch: pytest.MonkeyPatch):
    tenant_id = uuid4()
    site = legacy_site(tenant_id=tenant_id)
    db = FakeDatabase([QueryResult([site]), QueryResult([]), QueryResult([])])
    audits: list[dict] = []
    patch_dependencies(monkeypatch, db, audits)

    asyncio.run(backfill_site_projects.backfill(args(tenant_id=tenant_id)))

    compiled = db.statements[0].compile()
    assert compiled.params["tenant_id_1"] == tenant_id
