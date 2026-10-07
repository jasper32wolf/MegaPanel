from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from app.api.deps import AuthContext
from app.api.v1 import projects
from app.main import app
from app.schemas.workflow import PageDraftBatchRequest
from app.services.design_profiles import ResolvedDesignProfile
from fastapi import HTTPException
from pydantic import ValidationError


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return SimpleNamespace(all=lambda: self.value)


class DraftDatabase:
    def __init__(self, project, plans, latest):
        self.project = project
        self.plans = plans
        self.latest = iter(latest)
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.statements = []

    async def execute(self, statement):
        query = str(statement)
        self.statements.append(query)
        if "FROM projects" in query:
            return Result(self.project)
        if "FROM page_plans" in query:
            return Result(self.plans)
        if "FROM page_drafts" in query:
            return Result(next(self.latest))
        raise AssertionError(query)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        for row in self.added:
            if row.id is None:
                row.id = uuid4()

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _setup(monkeypatch, *, states=("approved", "approved"), latest=(None, None)):
    tenant_id, project_id = uuid4(), uuid4()
    project = SimpleNamespace(id=project_id, tenant_id=tenant_id)
    plan_ids = (UUID(int=1), UUID(int=2))
    plans = [
        SimpleNamespace(id=plan_id, state=state, kit_key="service-local-v1")
        for plan_id, state in zip(plan_ids, states, strict=True)
    ]
    db = DraftDatabase(project, plans, latest)
    auth = AuthContext(user=SimpleNamespace(id=uuid4()), tenant_id=tenant_id, role="editor")
    events = []

    async def confirmed_facts(_db, _project):
        return SimpleNamespace(facts={"organization": "Confirmed"})

    async def design(_db, *, tenant_id, project_id):
        assert tenant_id == project.tenant_id and project_id == project.id
        return ResolvedDesignProfile(project_id, None, None, None, None, None)

    def draft_snapshot(*, plan, **_kwargs):
        return {"slug": f"/{plan.id.int}/"}, {"generator_meta": {"kind": "deterministic"}}, ""

    async def audit(_db, **kwargs):
        events.append(kwargs)

    monkeypatch.setattr(projects, "_confirmed_facts", confirmed_facts)
    monkeypatch.setattr(projects, "resolve_design_profile", design)
    monkeypatch.setattr(projects, "create_page_draft", draft_snapshot)
    monkeypatch.setattr(projects, "_draft_manifest_hash", lambda _manifest: "a" * 64)
    monkeypatch.setattr(projects, "append_audit", audit)
    return db, auth, plan_ids, events


def _request(plan_ids, revisions=(0, 0)):
    return PageDraftBatchRequest(
        items=[
            {"plan_id": plan_id, "expected_latest_revision": revision}
            for plan_id, revision in zip(plan_ids, revisions, strict=True)
        ],
        confirm_drafts_only=True,
    )


def test_batch_creates_only_selected_drafts_with_one_commit(monkeypatch):
    db, auth, plan_ids, events = _setup(monkeypatch, latest=(None, SimpleNamespace(revision=2)))
    result = asyncio.run(
        projects.generate_page_draft_batch(db.project.id, _request(plan_ids, (0, 2)), auth, db)
    )

    assert result["drafts_only"] is True
    assert [(row["page_plan_id"], row["revision"], row["state"]) for row in result["drafts"]] == [
        (str(plan_ids[0]), 1, "draft"),
        (str(plan_ids[1]), 3, "draft"),
    ]
    assert db.commits == 1 and db.rollbacks == 0
    assert len(db.added) == len(events) == 2
    assert all(event["action"] == "page_draft.generate" for event in events)
    assert all(
        draft.input_snapshot["generator_meta"]["kind"] == "deterministic" for draft in db.added
    )
    assert all("FOR UPDATE" in query for query in db.statements if "FROM page_plans" in query)
    assert "post" in app.openapi()["paths"]["/api/v1/projects/{project_id}/page-plans/drafts/batch"]


def test_batch_rejects_stale_revision_without_committing_partial_drafts(monkeypatch):
    db, auth, plan_ids, _events = _setup(monkeypatch, latest=(None, SimpleNamespace(revision=1)))
    with pytest.raises(HTTPException) as error:
        asyncio.run(projects.generate_page_draft_batch(db.project.id, _request(plan_ids), auth, db))

    assert error.value.status_code == 409
    assert "revision changed" in str(error.value.detail)
    assert len(db.added) == 1  # The first draft was flushed but rolled back.
    assert db.commits == 0 and db.rollbacks == 1


def test_batch_blocks_unapproved_plans_before_any_draft(monkeypatch):
    db, auth, plan_ids, _events = _setup(monkeypatch, states=("approved", "review"))
    with pytest.raises(HTTPException) as error:
        asyncio.run(projects.generate_page_draft_batch(db.project.id, _request(plan_ids), auth, db))

    assert error.value.status_code == 409
    assert db.added == [] and db.commits == 0


def test_batch_rejects_invalid_second_plan_without_partial_commit(monkeypatch):
    db, auth, plan_ids, _events = _setup(monkeypatch)

    def generation(*, plan, **_kwargs):
        if plan.id == plan_ids[1]:
            raise ValueError("invalid curated block selection")
        return {"slug": "/first/"}, {"generator_meta": {}}, ""

    monkeypatch.setattr(projects, "create_page_draft", generation)
    with pytest.raises(HTTPException) as error:
        asyncio.run(projects.generate_page_draft_batch(db.project.id, _request(plan_ids), auth, db))
    assert error.value.status_code == 409
    assert "curated block selection" not in str(error.value.detail)
    assert len(db.added) == 1 and db.commits == 0 and db.rollbacks == 1


def test_batch_rejects_missing_plan_and_other_tenant_without_creating_drafts(monkeypatch):
    db, auth, plan_ids, _events = _setup(monkeypatch)
    db.plans = db.plans[:1]
    with pytest.raises(HTTPException) as missing:
        asyncio.run(projects.generate_page_draft_batch(db.project.id, _request(plan_ids), auth, db))
    assert missing.value.status_code == 404
    assert db.added == [] and db.commits == 0

    db, auth, plan_ids, _events = _setup(monkeypatch)
    auth.tenant_id = uuid4()
    with pytest.raises(HTTPException) as forbidden:
        asyncio.run(projects.generate_page_draft_batch(db.project.id, _request(plan_ids), auth, db))
    assert forbidden.value.status_code == 403
    assert db.added == [] and db.commits == 0
    assert len(db.statements) == 1


def test_batch_requires_explicit_confirmation_bounded_unique_selection():
    plan_id = uuid4()
    with pytest.raises(ValidationError):
        PageDraftBatchRequest(
            items=[{"plan_id": plan_id, "expected_latest_revision": 0}],
            confirm_drafts_only=False,
        )
    with pytest.raises(ValidationError):
        _request([plan_id] * 2)
    with pytest.raises(ValidationError):
        _request([uuid4() for _ in range(11)], [0] * 11)
    with pytest.raises(ValidationError):
        _request([plan_id], [-1])
