from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.api.v1.projects import project_activity


class ActivityResult:
    def __init__(self, entries: list[object]):
        self.entries = entries

    def scalars(self):
        return self

    def all(self):
        return self.entries


class ActivityDatabase:
    def __init__(self, entries):
        self.entries = entries

    async def execute(self, _statement):
        return ActivityResult(self.entries)

    async def scalar(self, _statement):
        return len(self.entries)


def test_project_activity_returns_metadata_without_audit_payload(monkeypatch):
    project_id, tenant_id = uuid4(), uuid4()
    project = SimpleNamespace(id=project_id, tenant_id=tenant_id)
    entry = SimpleNamespace(
        id=7,
        action="project.build.materialize",
        actor_id=uuid4(),
        created_at=None,
        record_hash="a" * 64,
        payload={"project_id": str(project_id), "private": "must not leak"},
    )
    db = ActivityDatabase([entry])

    async def project_or_404(*_args):
        return project

    monkeypatch.setattr("app.api.v1.projects._project_or_404", project_or_404)
    auth = SimpleNamespace(role="superadmin", tenant_id=tenant_id)

    result = asyncio.run(
        project_activity(project_id, action=None, offset=0, limit=25, auth=auth, db=db)
    )

    assert result["total"] == 1
    assert result["items"] == [
        {
            "id": 7,
            "action": "project.build.materialize",
            "actor_id": str(entry.actor_id),
            "created_at": None,
            "record_hash": "a" * 64,
        }
    ]
    assert "payload" not in result["items"][0]


def test_project_activity_route_is_registered():
    from app.main import app

    assert "get" in app.openapi()["paths"]["/api/v1/projects/{project_id}/activity"]
