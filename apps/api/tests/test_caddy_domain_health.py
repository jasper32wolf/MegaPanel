from app.services.caddy_client import CaddyClient
from app.services.domain_health import resolve_dns


def test_caddy_noindex_is_path_scoped():
    client = CaddyClient(base_url="http://127.0.0.1:9")
    routes = client._noindex_subroutes(["/a", "/b/"])
    assert len(routes) == 2
    assert routes[0]["match"][0]["path"] == ["/a", "/a/"]
    assert routes[0]["handle"][0]["response"]["set"]["X-Robots-Tag"] == ["noindex, follow"]


def test_resolve_dns_localhost():
    result = resolve_dns("localhost")
    assert result["status"] in {"ok", "nxdomain", "error"}
    # On Windows localhost usually resolves
    if result["ok"]:
        assert result["a"] or result["aaaa"]
