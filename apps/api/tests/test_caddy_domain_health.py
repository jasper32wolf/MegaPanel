import asyncio
from uuid import uuid4

from app.services.caddy_client import CaddyClient
from app.services.domain_health import resolve_dns


def test_caddy_noindex_is_path_scoped():
    client = CaddyClient(base_url="http://127.0.0.1:9")
    routes = client._noindex_subroutes(["/a", "/b/"])
    assert len(routes) == 2
    assert routes[0]["match"][0]["path"] == ["/a", "/a/"]
    assert routes[0]["handle"][0]["response"]["set"]["X-Robots-Tag"] == ["noindex, follow"]


def test_caddy_site_vhost_proxies_lead_form_same_origin():
    client = CaddyClient(base_url="http://127.0.0.1:9")
    route = client._lead_form_proxy_route()

    assert route["match"] == [{"path": ["/api/v1/leads/public"], "method": ["POST"]}]
    assert route["handle"][0]["upstreams"] == [{"dial": "api:8000"}]
    assert route["terminal"] is True


def test_caddy_site_vhost_is_inserted_before_the_static_fallback():
    client = CaddyClient(base_url="http://127.0.0.1:9")
    calls = []

    async def request(method, path, json_body=None):
        calls.append((method, path, json_body))
        return {"ok": False, "status": 404} if len(calls) == 1 else {"ok": True}

    client._request = request
    result = asyncio.run(client.upsert_site_vhost("example.test", "/srv/sites/example.test"))

    assert result["ok"] is True
    assert calls[0][:2] == ("PUT", "/id/site-example.test")
    assert calls[1][:2] == ("POST", "/config/apps/http/servers/srv0/routes/0")
    assert calls[1][2] == calls[0][2]


def test_caddy_site_vhost_has_the_minimum_client_security_headers():
    client = CaddyClient(base_url="http://127.0.0.1:9")
    calls = []

    async def request(method, path, json_body=None):
        calls.append((method, path, json_body))
        return {"ok": True}

    client._request = request
    asyncio.run(client.upsert_site_vhost("example.test", "/srv/sites/example.test"))

    headers = calls[0][2]["handle"][1]["response"]["set"]
    assert headers == {
        "X-Content-Type-Options": ["nosniff"],
        "Referrer-Policy": ["strict-origin-when-cross-origin"],
        "Strict-Transport-Security": ["max-age=31536000; includeSubDomains"],
        "X-Frame-Options": ["DENY"],
        "Permissions-Policy": ["geolocation=(), microphone=(), camera=()"],
    }


def test_caddy_redirect_uses_stable_id_and_high_priority_route():
    client = CaddyClient(base_url="http://127.0.0.1:9")
    calls = []

    async def request(method, path, json_body=None):
        calls.append((method, path, json_body))
        return {"ok": False, "status": 404} if len(calls) == 1 else {"ok": True}

    client._request = request
    redirect_id = uuid4()
    result = asyncio.run(
        client.add_redirect("example.ru", "/old", "https://example.ru/new", redirect_id=redirect_id)
    )

    assert result["ok"] is True
    assert result["route_id"] == f"redir-{redirect_id}"
    assert calls == [
        ("PUT", f"/id/redir-{redirect_id}", result["route"]),
        ("POST", "/config/apps/http/servers/srv0/routes/0", result["route"]),
    ]
    assert result["route"]["terminal"] is True


def test_caddy_health_propagates_an_admin_failure():
    client = CaddyClient(base_url="http://127.0.0.1:9")

    async def request(method, path, json_body=None):
        return {"ok": False, "status": 503}

    client._request = request
    assert asyncio.run(client.health()) is False


def test_resolve_dns_localhost():
    result = resolve_dns("localhost")
    assert result["status"] in {"ok", "nxdomain", "error"}
    if result["ok"]:
        assert result["a"] or result["aaaa"]
