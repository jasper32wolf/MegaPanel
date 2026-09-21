from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
SNAPSHOT_RE = re.compile(r"^[0-9a-f]{8,64}$")
DEPLOY_WORKFLOW = "deploy-production.yml"
RECOVERY_WORKFLOW = "recover-production.yml"
CI_WORKFLOW = "ci.yml"


class GitHubControlError(Exception):
    def __init__(self, code: str, status_code: int = 503) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class VerifiedRelease:
    sha: str
    updated_at: str | None
    url: str | None


@dataclass(frozen=True)
class WorkflowRun:
    run_id: int
    url: str | None
    status: str
    conclusion: str | None


def _api_base_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise GitHubControlError("github_api_url_invalid", 503)
    return value.rstrip("/")


def _repository(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise GitHubControlError("github_repository_invalid", 503)
    return value


def _safe_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    return value


class GitHubControl:
    def __init__(
        self,
        *,
        repository: str,
        token: str,
        api_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not token:
            raise GitHubControlError("github_control_not_configured", 503)
        self.repository = _repository(repository)
        self.token = token
        self.api_url = _api_base_url(api_url)
        self.transport = transport

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                base_url=self.api_url,
                headers=self._headers,
                timeout=httpx.Timeout(15.0, connect=5.0),
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.request(method, path, json=payload)
        except httpx.TimeoutException as exc:
            raise GitHubControlError("github_timeout", 503) from exc
        except httpx.HTTPError as exc:
            raise GitHubControlError("github_network_error", 503) from exc
        if response.status_code in {401, 403}:
            raise GitHubControlError("github_authorization_failed", 503)
        if response.status_code == 404:
            raise GitHubControlError("github_resource_not_found", 503)
        if response.status_code == 409:
            raise GitHubControlError("github_conflict", 409)
        if response.status_code == 429:
            raise GitHubControlError("github_rate_limited", 503)
        if response.status_code >= 500:
            raise GitHubControlError("github_unavailable", 503)
        if response.status_code >= 400:
            raise GitHubControlError("github_request_failed", 503)
        return response

    @staticmethod
    def _payload(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise GitHubControlError("github_response_invalid", 503) from exc
        if not isinstance(payload, dict):
            raise GitHubControlError("github_response_invalid", 503)
        return payload

    async def dispatch_deploy(self, *, release_sha: str, request_id: str) -> None:
        if not SHA_RE.fullmatch(release_sha):
            raise GitHubControlError("release_sha_invalid", 422)
        await self._dispatch(
            DEPLOY_WORKFLOW,
            {
                "release_sha": release_sha,
                "request_id": request_id,
            },
        )

    async def dispatch_recovery(
        self,
        *,
        action: str,
        snapshot: str | None,
        request_id: str,
    ) -> None:
        if action not in {"status", "restart", "rollback", "recover", "restore"}:
            raise GitHubControlError("recovery_action_invalid", 422)
        if action == "restore" and (not snapshot or not SNAPSHOT_RE.fullmatch(snapshot)):
            raise GitHubControlError("restore_snapshot_invalid", 422)
        await self._dispatch(
            RECOVERY_WORKFLOW,
            {
                "operation": action,
                "snapshot": snapshot or "",
                "confirmation": "RESTORE" if action == "restore" else "",
                "request_id": request_id,
            },
        )

    async def _dispatch(self, workflow: str, inputs: dict[str, str]) -> None:
        response = await self._request(
            "POST",
            f"/repos/{self.repository}/actions/workflows/{workflow}/dispatches",
            payload={"ref": "main", "inputs": inputs},
        )
        if response.status_code != 204:
            raise GitHubControlError("github_dispatch_failed", 503)

    async def successful_releases(self) -> list[VerifiedRelease]:
        response = await self._request(
            "GET",
            f"/repos/{self.repository}/actions/workflows/{CI_WORKFLOW}/runs?branch=main&status=completed&per_page=30",
        )
        rows = self._payload(response).get("workflow_runs", [])
        if not isinstance(rows, list):
            raise GitHubControlError("github_response_invalid", 503)
        releases: list[VerifiedRelease] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            sha = row.get("head_sha", "")
            if (
                row.get("conclusion") != "success"
                or not isinstance(sha, str)
                or not SHA_RE.fullmatch(sha)
                or sha in seen
            ):
                continue
            seen.add(sha)
            releases.append(
                VerifiedRelease(
                    sha=sha,
                    updated_at=row.get("updated_at")
                    if isinstance(row.get("updated_at"), str)
                    else None,
                    url=_safe_url(row.get("html_url")),
                )
            )
        return releases

    async def workflow_run(self, *, workflow: str, request_id: str) -> WorkflowRun | None:
        if workflow not in {DEPLOY_WORKFLOW, RECOVERY_WORKFLOW}:
            raise GitHubControlError("workflow_not_allowed", 503)
        response = await self._request(
            "GET",
            f"/repos/{self.repository}/actions/workflows/{workflow}/runs?event=workflow_dispatch&per_page=30",
        )
        rows = self._payload(response).get("workflow_runs", [])
        if not isinstance(rows, list):
            raise GitHubControlError("github_response_invalid", 503)
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = row.get("display_title")
            run_id = row.get("id")
            if title != request_id or not isinstance(run_id, int) or isinstance(run_id, bool):
                continue
            return WorkflowRun(
                run_id=run_id,
                url=_safe_url(row.get("html_url")),
                status=str(row.get("status") or "unknown"),
                conclusion=row.get("conclusion")
                if isinstance(row.get("conclusion"), str)
                else None,
            )
        return None


def operation_status(run: WorkflowRun | None) -> str:
    if run is None:
        return "queued"
    if run.status in {"queued", "requested", "waiting", "pending"}:
        return "queued"
    if run.status == "in_progress":
        return "in_progress"
    if run.status != "completed":
        return "unknown"
    if run.conclusion == "success":
        return "success"
    if run.conclusion == "cancelled":
        return "cancelled"
    return "failure"
