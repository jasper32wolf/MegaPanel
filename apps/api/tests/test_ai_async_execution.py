from __future__ import annotations

import inspect

from app.api.v1 import ai_workspace
from app.schemas.ai import AIRunStatus
from app.worker import WorkerSettings, architecture_proposal_task


def test_async_status_contract_includes_worker_and_approval_states() -> None:
    assert set(AIRunStatus.__args__) >= {
        "reserved",
        "running",
        "pending_approval",
        "failed",
    }


def test_architecture_worker_is_registered() -> None:
    assert architecture_proposal_task in WorkerSettings.functions


def test_architecture_route_has_no_inline_provider_call() -> None:
    source = inspect.getsource(ai_workspace.propose_architecture)
    assert "generate_structured" not in source
    assert "_adapter" not in source
    assert "enqueue_ai_run" in source


def test_queue_contract_is_run_id_only() -> None:
    source = inspect.getsource(ai_workspace.enqueue_ai_run)
    assert "enqueue_job(\"architecture_proposal_task\", str(run_id))" in source
    assert "api_key" not in source
    assert "execution_envelope" not in source


def test_worker_duplicate_handling_is_explicit_and_tenant_scoped() -> None:
    source = inspect.getsource(architecture_proposal_task)
    assert 'run.status != "reserved"' in source
    assert '"duplicate": True' in source
    assert '"tenant_scope_violation"' in source


def test_queue_failure_code_and_conservative_reservation_are_in_route() -> None:
    source = inspect.getsource(ai_workspace.propose_architecture)
    assert 'run.error_code = "queue_unavailable"' in source
    assert 'run.status = "failed"' in source
    assert "cost_usd" not in source[source.index('run.status = "failed"') :]


def test_run_id_is_the_only_worker_argument() -> None:
    assert list(inspect.signature(architecture_proposal_task).parameters) == ["ctx", "run_id"]
