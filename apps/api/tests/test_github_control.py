from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from app.services.github_control import (
    DEPLOY_WORKFLOW,
    RECOVERY_WORKFLOW,
    GitHubControl,
    GitHubControlError,
    operation_status,
)


def run(coro):
    return asyncio.run(coro)


def test_dispatches_only_fixed_deploy_workflow_with_main_ref():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer control-token"
        return httpx.Response(204)

    client = GitHubControl(
        repository="owner/repository",
        token="control-token",
        api_url="https://api.github.com",
        transport=httpx.MockTransport(handler),
    )

    run(client.dispatch_deploy(release_sha="a" * 40, request_id="request-id"))

    assert (
        captured["path"]
        == f"/repos/owner/repository/actions/workflows/{DEPLOY_WORKFLOW}/dispatches"
    )
    assert captured["body"] == {
        "ref": "main",
        "inputs": {"release_sha": "a" * 40, "request_id": "request-id"},
    }


def test_restore_dispatch_forces_literal_restore_confirmation():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(204)

    client = GitHubControl(
        repository="owner/repository",
        token="control-token",
        api_url="https://api.github.com",
        transport=httpx.MockTransport(handler),
    )

    run(client.dispatch_recovery(action="restore", snapshot="deadbeef", request_id="request-id"))

    assert (
        captured["path"]
        == f"/repos/owner/repository/actions/workflows/{RECOVERY_WORKFLOW}/dispatches"
    )
    assert captured["body"] == {
        "ref": "main",
        "inputs": {
            "operation": "restore",
            "snapshot": "deadbeef",
            "confirmation": "RESTORE",
            "request_id": "request-id",
        },
    }


def test_available_releases_excludes_unsuccessful_or_invalid_shas():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "workflow_runs": [
                    {
                        "head_sha": "a" * 40,
                        "conclusion": "success",
                        "updated_at": "2026-09-21T00:00:00Z",
                        "html_url": "https://example.test/a",
                    },
                    {"head_sha": "b" * 40, "conclusion": "failure"},
                    {"head_sha": "not-a-sha", "conclusion": "success"},
                    {"head_sha": "a" * 40, "conclusion": "success"},
                ]
            },
        )

    client = GitHubControl(
        repository="owner/repository",
        token="control-token",
        api_url="https://api.github.com",
        transport=httpx.MockTransport(handler),
    )

    releases = run(client.successful_releases())

    assert [release.sha for release in releases] == ["a" * 40]
    assert releases[0].url == "https://example.test/a"


@pytest.mark.parametrize(
    "repository", ["", "owner", "owner/repo/path", "https://github.com/owner/repo"]
)
def test_rejects_invalid_repository(repository: str):
    with pytest.raises(GitHubControlError, match="github_repository_invalid"):
        GitHubControl(repository=repository, token="token", api_url="https://api.github.com")


@pytest.mark.parametrize(
    "api_url", ["http://api.github.com", "https://user@example.test", "file:///tmp/api"]
)
def test_rejects_unsafe_github_api_url(api_url: str):
    with pytest.raises(GitHubControlError, match="github_api_url_invalid"):
        GitHubControl(repository="owner/repository", token="token", api_url=api_url)


def test_finds_panel_run_by_exact_request_id_and_sanitizes_url():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "workflow_runs": [
                    {
                        "id": 42,
                        "display_title": "other-request",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "id": 43,
                        "display_title": "request-id",
                        "status": "in_progress",
                        "conclusion": None,
                        "html_url": "javascript:alert(1)",
                    },
                ]
            },
        )

    client = GitHubControl(
        repository="owner/repository",
        token="control-token",
        api_url="https://api.github.com",
        transport=httpx.MockTransport(handler),
    )

    workflow_run = run(client.workflow_run(workflow=DEPLOY_WORKFLOW, request_id="request-id"))

    assert workflow_run is not None
    assert workflow_run.run_id == 43
    assert workflow_run.url is None
    assert workflow_run.status == "in_progress"


def test_rejects_malformed_github_response():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    client = GitHubControl(
        repository="owner/repository",
        token="control-token",
        api_url="https://api.github.com",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(GitHubControlError, match="github_response_invalid"):
        run(client.successful_releases())


def test_maps_workflow_run_states_without_exposing_payloads():
    from app.services.github_control import WorkflowRun

    assert operation_status(None) == "queued"
    assert operation_status(WorkflowRun(1, None, "in_progress", None)) == "in_progress"
    assert operation_status(WorkflowRun(1, None, "completed", "success")) == "success"
    assert operation_status(WorkflowRun(1, None, "completed", "failure")) == "failure"
