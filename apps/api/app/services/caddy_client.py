"""Caddy Admin API client for vhosts, redirects, SSL (TZ 13 / 7.1)."""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

import httpx
from app.core.config import get_settings


class CaddyClient:
    def __init__(self, base_url: str | None = None) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.caddy_admin_url).rstrip("/")

    @staticmethod
    def _ok(result: dict | list | None) -> bool:
        return not isinstance(result, dict) or result.get("ok") is not False

    async def _request(self, method: str, path: str, json_body: Any = None) -> dict | list | None:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.request(method, url, json=json_body)
                if resp.status_code >= 400:
                    return {"ok": False, "status": resp.status_code, "body": resp.text[:500]}
                if not resp.content:
                    return {"ok": True}
                return resp.json()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def _noindex_subroutes(self, paths: list[str]) -> list[dict]:
        """Per-path X-Robots-Tag:noindex for drip queue (not site-wide)."""
        routes = []
        for raw in paths:
            path = raw if raw.startswith("/") else f"/{raw}"
            if not path.endswith("/") and path != "/":
                match_paths = [path, f"{path}/"]
            else:
                match_paths = [path]
            routes.append(
                {
                    "match": [{"path": match_paths}],
                    "handle": [
                        {
                            "handler": "headers",
                            "response": {"set": {"X-Robots-Tag": ["noindex, follow"]}},
                        }
                    ],
                }
            )
        return routes

    def _lead_form_proxy_route(self) -> dict:
        return {
            "match": [{"path": ["/api/v1/leads/public"], "method": ["POST"]}],
            "handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "api:8000"}]}],
            "terminal": True,
        }

    async def upsert_site_vhost(
        self,
        hostname: str,
        root_path: str,
        *,
        noindex_paths: list[str] | None = None,
    ) -> dict:
        """
        Add/update a site block. Uses Caddy Admin API load/patch style.
        When Caddy is down (local dev), returns soft-failure without raising.
        Path-scoped noindex via subroute matchers (TZ 7.1).
        """
        handlers: list[dict[str, Any]] = [
            {"handler": "subroute", "routes": [self._lead_form_proxy_route()]},
            {
                "handler": "headers",
                "response": {
                    "set": {
                        "X-Content-Type-Options": ["nosniff"],
                        "Referrer-Policy": ["strict-origin-when-cross-origin"],
                        "Strict-Transport-Security": ["max-age=31536000; includeSubDomains"],
                        "X-Frame-Options": ["DENY"],
                        "Permissions-Policy": ["geolocation=(), microphone=(), camera=()"],
                    }
                },
            },
        ]
        if noindex_paths:
            handlers.append(
                {
                    "handler": "subroute",
                    "routes": self._noindex_subroutes(noindex_paths),
                }
            )
        handlers.append({"handler": "file_server", "root": root_path})

        route = {
            "@id": f"site-{hostname}",
            "match": [{"host": [hostname]}],
            "handle": handlers,
        }
        result = await self._request("PUT", f"/id/site-{hostname}", route)
        if not self._ok(result):
            created = await self._request("POST", "/config/apps/http/servers/srv0/routes/0", route)
            return {
                "ok": self._ok(created),
                "upsert": result,
                "create": created,
                "hostname": hostname,
                "noindex_paths": noindex_paths or [],
            }
        return {
            "ok": True,
            "hostname": hostname,
            "result": result,
            "noindex_paths": noindex_paths or [],
        }

    async def delete_site_vhost(self, hostname: str) -> dict:
        result = await self._request("DELETE", f"/id/site-{hostname}")
        return {
            "ok": self._ok(result) or (isinstance(result, dict) and result.get("status") == 404),
            "result": result,
        }

    async def add_redirect(
        self,
        hostname: str,
        from_path: str,
        to_url: str,
        code: int = 301,
        *,
        redirect_id: UUID | str | None = None,
    ) -> dict:
        route_id = (
            f"redir-{redirect_id}"
            if redirect_id
            else f"redir-{hashlib.sha256(f'{hostname}:{from_path}'.encode()).hexdigest()[:16]}"
        )
        route = {
            "@id": route_id,
            "match": [{"host": [hostname], "path": [from_path]}],
            "handle": [
                {
                    "handler": "static_response",
                    "headers": {"Location": [to_url]},
                    "status_code": code,
                }
            ],
            "terminal": True,
        }
        result = await self._request("PUT", f"/id/{route_id}", route)
        if not self._ok(result):
            result = await self._request("POST", "/config/apps/http/servers/srv0/routes/0", route)
        return {"ok": self._ok(result), "route_id": route_id, "route": route, "result": result}

    async def delete_redirect(self, redirect_id: UUID | str) -> dict:
        result = await self._request("DELETE", f"/id/redir-{redirect_id}")
        return {"ok": self._ok(result), "result": result}

    async def health(self) -> bool:
        return self._ok(await self._request("GET", "/config/"))
