from __future__ import annotations

import asyncio
import importlib.util
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.core import security
from app.db import session as db_session
from app.services import audit

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "bootstrap_operator.py"
spec = importlib.util.spec_from_file_location("bootstrap_operator", SCRIPT_PATH)
assert spec and spec.loader
bootstrap_operator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap_operator)


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
        self.committed = False
        self.statements: list[object] = []

    async def execute(self, statement: object) -> QueryResult:
        self.statements.append(statement)
        return self.results.pop(0)

    async def flush(self) -> None:
        return None

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


def args(**overrides: object) -> Namespace:
    values = {
        "email": "owner@example.test",
        "name": "Owner",
        "tenant_slug": "operator",
        "reset_password": False,
    }
    values.update(overrides)
    return Namespace(**values)


def patch_dependencies(monkeypatch: pytest.MonkeyPatch, db: FakeDatabase) -> None:
    monkeypatch.setattr(db_session, "open_db_session", lambda: FakeSession(db))
    monkeypatch.setattr(security, "hash_password", lambda password: f"hash:{password}")

    async def append_audit(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(audit, "append_audit", append_audit)


def test_bootstrap_rejects_multiple_existing_users(monkeypatch: pytest.MonkeyPatch):
    db = FakeDatabase(
        [
            QueryResult(
                [
                    SimpleNamespace(email="owner@example.test"),
                    SimpleNamespace(email="other@example.test"),
                ]
            )
        ]
    )
    patch_dependencies(monkeypatch, db)

    with pytest.raises(SystemExit, match="Multiple user accounts"):
        asyncio.run(bootstrap_operator.bootstrap(args(), "correct-horse"))

    assert len(db.statements) == 1
    assert not db.added
    assert not db.committed


def test_bootstrap_rejects_another_operator_email(monkeypatch: pytest.MonkeyPatch):
    db = FakeDatabase([QueryResult([SimpleNamespace(email="owner@example.test")])])
    patch_dependencies(monkeypatch, db)

    with pytest.raises(SystemExit, match="operator account already exists"):
        asyncio.run(bootstrap_operator.bootstrap(args(email="other@example.test"), "correct-horse"))

    assert len(db.statements) == 1
    assert not db.added
    assert not db.committed


def test_password_reset_keeps_existing_owner_scope(monkeypatch: pytest.MonkeyPatch):
    tenant_id = uuid4()
    user = SimpleNamespace(
        id=uuid4(),
        email="owner@example.test",
        tenant_id=tenant_id,
        role="tenant_admin",
        password_hash="old",
        is_active=False,
    )
    tenant = SimpleNamespace(id=tenant_id, slug="operator")
    db = FakeDatabase([QueryResult([user]), QueryResult([tenant])])
    patch_dependencies(monkeypatch, db)

    asyncio.run(
        bootstrap_operator.bootstrap(
            args(reset_password=True, tenant_slug="ignored"), "correct-horse"
        )
    )

    assert user.tenant_id == tenant_id
    assert user.role == "superadmin"
    assert user.password_hash == "hash:correct-horse"
    assert user.is_active
    assert db.committed


def test_bootstrap_rejects_multiple_owner_scopes(monkeypatch: pytest.MonkeyPatch):
    db = FakeDatabase(
        [
            QueryResult([]),
            QueryResult(
                [
                    SimpleNamespace(slug="operator"),
                    SimpleNamespace(slug="other-owner"),
                ]
            ),
        ]
    )
    patch_dependencies(monkeypatch, db)

    with pytest.raises(SystemExit, match="Multiple owner scopes"):
        asyncio.run(bootstrap_operator.bootstrap(args(), "correct-horse"))

    assert len(db.statements) == 2
    assert not db.added
    assert not db.committed


def test_bootstrap_rejects_another_owner_scope(monkeypatch: pytest.MonkeyPatch):
    db = FakeDatabase([QueryResult([]), QueryResult([SimpleNamespace(slug="other-owner")])])
    patch_dependencies(monkeypatch, db)

    with pytest.raises(SystemExit, match="owner scope already exists"):
        asyncio.run(bootstrap_operator.bootstrap(args(), "correct-horse"))

    assert len(db.statements) == 2
    assert not db.added
    assert not db.committed


def test_initial_bootstrap_creates_a_superadmin(monkeypatch: pytest.MonkeyPatch):
    tenant = SimpleNamespace(id=uuid4(), slug="operator")
    db = FakeDatabase([QueryResult([]), QueryResult([tenant])])
    patch_dependencies(monkeypatch, db)

    asyncio.run(bootstrap_operator.bootstrap(args(), "correct-horse"))

    user = db.added[0]
    assert user.email == "owner@example.test"
    assert user.role == "superadmin"
    assert user.tenant_id == tenant.id
    assert db.committed
