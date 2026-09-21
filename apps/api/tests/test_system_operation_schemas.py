from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1 import system
from app.schemas.system import RecoveryRequest, UpdateRequest
from fastapi import HTTPException
from pydantic import ValidationError


def test_update_requires_full_sha_and_explicit_deploy_confirmation():
    assert UpdateRequest(release_sha="a" * 40, confirmation="DEPLOY").release_sha == "a" * 40
    with pytest.raises(ValidationError):
        UpdateRequest(release_sha="not-a-sha", confirmation="DEPLOY")
    with pytest.raises(ValidationError):
        UpdateRequest(release_sha="a" * 40, confirmation="deploy")


def test_recovery_requires_action_specific_confirmation():
    assert RecoveryRequest(action="rollback", confirmation="ROLLBACK").action == "rollback"
    assert (
        RecoveryRequest(
            action="restore",
            snapshot_id="deadbeef",
            confirmation="RESTORE",
        ).snapshot_id
        == "deadbeef"
    )
    with pytest.raises(ValidationError):
        RecoveryRequest(action="restore", snapshot_id="deadbeef", confirmation="ROLLBACK")
    with pytest.raises(ValidationError):
        RecoveryRequest(action="restart", snapshot_id="deadbeef", confirmation="RESTART")


class FlushConflictDatabase:
    def add(self, _: object) -> None:
        pass

    async def flush(self) -> None:
        from sqlalchemy.exc import IntegrityError

        raise IntegrityError("INSERT", {}, RuntimeError("duplicate"))

    async def rollback(self) -> None:
        pass


async def conflicting_operation(*_: object) -> object:
    return SimpleNamespace(id=uuid4())


def test_concurrent_system_operation_returns_conflict_when_flush_fails(monkeypatch):
    monkeypatch.setattr(system, "_active_operation", conflicting_operation)
    auth = SimpleNamespace(tenant_id=uuid4(), user=SimpleNamespace(id=uuid4()))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            system._create_operation(
                kind="update",
                action="deploy",
                workflow="deploy-production.yml",
                release_sha="a" * 40,
                snapshot_id=None,
                auth=auth,
                db=FlushConflictDatabase(),
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "system_operation_in_progress"
