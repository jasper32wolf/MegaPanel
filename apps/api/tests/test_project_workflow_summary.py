from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.deps import AuthContext
from app.api.v1.projects import project_workflow_summary
from fastapi import HTTPException


class SummaryDatabase:
    def __init__(self, project, site, counts):
        self.project = project
        self.site = site
        self.counts = iter(counts)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(str(statement))
        return SimpleNamespace(scalar_one_or_none=lambda: self.project)

    async def get(self, model, key):
        assert model.__name__ == "Site"
        assert key == self.project.site_id
        return self.site

    async def scalar(self, statement):
        self.statements.append(str(statement.compile(compile_kwargs={"literal_binds": True})))
        return next(self.counts)


def test_workflow_summary_reports_only_tenant_scoped_artifact_counts():
    tenant_id, project_id, site_id = uuid4(), uuid4(), uuid4()
    project = SimpleNamespace(
        id=project_id, tenant_id=tenant_id, site_id=site_id, current_fact_revision_id=uuid4()
    )
    site = SimpleNamespace(
        tenant_id=tenant_id, project_id=project_id, publish_state="published", build_hash="a" * 64
    )
    db = SummaryDatabase(project, site, [2, 1, 1, 1, 3, 2, 1, 0])
    auth = AuthContext(user=SimpleNamespace(id=uuid4()), tenant_id=tenant_id, role="manager")

    result = asyncio.run(project_workflow_summary(project_id, auth, db))

    assert result == {
        "facts_confirmed": True,
        "keyword_count": 2,
        "geo_count": 1,
        "approved_collection_count": 1,
        "approved_structure_count": 1,
        "approved_plan_count": 3,
        "applied_draft_count": 2,
        "ready_candidate_count": 1,
        "running_candidate_count": 0,
        "published": True,
    }
    assert len(db.statements) == 9
    assert all("tenant_id" in query and "project_id" in query for query in db.statements[1:])
    assert "approved" in db.statements[3]
    assert "applied" in db.statements[6]
    assert "build_hash" not in result
    assert "facts" not in result


def test_workflow_summary_rejects_other_tenant_before_reading_artifacts():
    project = SimpleNamespace(
        id=uuid4(), tenant_id=uuid4(), site_id=None, current_fact_revision_id=None
    )
    db = SummaryDatabase(project, None, [])
    auth = AuthContext(user=SimpleNamespace(id=uuid4()), tenant_id=uuid4(), role="manager")

    with pytest.raises(HTTPException) as error:
        asyncio.run(project_workflow_summary(project.id, auth, db))

    assert error.value.status_code == 403
    assert len(db.statements) == 1


@pytest.mark.parametrize("foreign_tenant", [True, False])
def test_workflow_summary_does_not_count_foreign_site_as_published(foreign_tenant):
    project = SimpleNamespace(
        id=uuid4(), tenant_id=uuid4(), site_id=uuid4(), current_fact_revision_id=None
    )
    foreign_site = SimpleNamespace(
        tenant_id=uuid4() if foreign_tenant else project.tenant_id,
        project_id=uuid4(),
        publish_state="published",
        build_hash="a" * 64,
    )
    db = SummaryDatabase(project, foreign_site, [0] * 8)
    auth = AuthContext(user=SimpleNamespace(id=uuid4()), tenant_id=None, role="superadmin")

    result = asyncio.run(project_workflow_summary(project.id, auth, db))

    assert result["published"] is False
    assert result["facts_confirmed"] is False
    assert result["ready_candidate_count"] == 0
