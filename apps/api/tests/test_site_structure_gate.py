from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1.projects import _approved_structure_for_new_page_plans
from fastapi import HTTPException


def test_new_page_plan_gate_returns_latest_approved_structure() -> None:
    revision = SimpleNamespace(id=uuid4(), version=3, state="approved")
    project = SimpleNamespace(id=uuid4(), tenant_id=uuid4())

    class Session:
        async def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: revision)

    result = asyncio.run(_approved_structure_for_new_page_plans(Session(), project))

    assert result is revision


def test_new_page_plan_gate_blocks_missing_structure() -> None:
    project = SimpleNamespace(id=uuid4(), tenant_id=uuid4())

    class Session:
        async def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: None)

    with pytest.raises(HTTPException, match="Approve a site structure revision") as error:
        asyncio.run(_approved_structure_for_new_page_plans(Session(), project))

    assert error.value.status_code == 409


def test_direct_page_plan_creators_use_server_owned_structure_provenance() -> None:
    source = (Path(__file__).parents[1] / "app" / "api" / "v1" / "projects.py").read_text(
        encoding="utf-8"
    )

    assert source.count("_approved_structure_for_new_page_plans(db, project)") == 2
    assert source.count('"site_structure_revision_id": str(structure.id)') == 2
    assert source.count('"site_structure_version": structure.version') == 2
