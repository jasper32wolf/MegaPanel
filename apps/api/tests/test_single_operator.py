import asyncio
from types import SimpleNamespace

import pytest
from app import main as main_module
from app.api.deps import require_single_operator
from app.api.v1.security_ops import me
from app.core import security
from app.core.config import Settings
from app.main import app
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError


def production_settings(**overrides: str) -> Settings:
    values = {
        "app_env": "production",
        "app_secret_key": "a" * 48,
        "app_pepper": "b" * 48,
        "blind_index_pepper": "c" * 48,
        "field_encryption_key": "d" * 48,
        "panel_public_url": "https://panel.example.test",
        "api_public_url": "https://api.example.test",
        "cors_origins": "https://panel.example.test",
    }
    values.update(overrides)
    return Settings(**values)


class OperatorDatabase:
    def __init__(self, operator_ids: list[object]):
        self.operator_ids = operator_ids

    async def execute(self, _: object) -> object:
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self.operator_ids))


def test_authenticated_api_allows_the_single_active_operator():
    operator_id = object()

    asyncio.run(
        require_single_operator(OperatorDatabase([operator_id]), SimpleNamespace(id=operator_id))
    )


@pytest.mark.parametrize("operator_ids", [[], [object()], [object(), object()]])
def test_authenticated_api_rejects_non_single_operator_states(operator_ids: list[object]):
    with pytest.raises(HTTPException, match="Single operator access required"):
        asyncio.run(
            require_single_operator(OperatorDatabase(operator_ids), SimpleNamespace(id=object()))
        )


def test_current_operator_response_hides_internal_role_and_owner_scope():
    user = SimpleNamespace(
        id=object(),
        email="owner@example.test",
        mfa_enabled=True,
        totp_secret="secret",
        totp_pending=None,
    )

    response = asyncio.run(me(SimpleNamespace(user=user)))

    assert response == {
        "id": str(user.id),
        "email": "owner@example.test",
        "mfa_enabled": True,
        "mfa_pending": False,
    }


def test_production_disables_interactive_api_docs(monkeypatch):
    monkeypatch.setattr(main_module.settings, "app_env", "production")

    assert main_module._docs_url() is None
    assert main_module._openapi_url() is None


def test_nonproduction_keeps_api_docs_available(monkeypatch):
    monkeypatch.setattr(main_module.settings, "app_env", "development")

    assert main_module._docs_url() == "/docs"
    assert main_module._openapi_url() == "/openapi.json"


def test_production_settings_require_safe_secrets():
    with pytest.raises(ValidationError, match="APP_SECRET_KEY"):
        production_settings(app_secret_key="change-me")


def test_production_settings_require_https_origins():
    with pytest.raises(ValidationError, match="PANEL_PUBLIC_URL"):
        production_settings(panel_public_url="http://panel.example.test")


def test_production_settings_require_panel_cors_origin():
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        production_settings(cors_origins="https://other.example.test")


def test_access_token_contains_its_refresh_session_id(monkeypatch):
    from uuid import uuid4

    monkeypatch.setattr(security.settings, "app_secret_key", "a" * 32)
    user_id = uuid4()
    session_id = uuid4()
    payload = security.decode_token(
        security.create_access_token(user_id, None, "superadmin", session_id)
    )

    assert payload["sid"] == str(session_id)


def test_legacy_direct_publish_routes_are_not_available_in_the_release_api():
    paths = app.openapi()["paths"]

    assert "/api/v1/sites/{site_id}/build" not in paths
    assert "/api/v1/sites/{site_id}/rollback" not in paths
    assert "/api/v1/publish/sites/{site_id}/publish" not in paths
    assert "/api/v1/publish/sites/{site_id}/pages" not in paths
    assert "get" in paths["/api/v1/sites/{site_id}/pages"]

    with TestClient(app) as client:
        site_id = "00000000-0000-0000-0000-000000000000"
        assert client.post(f"/api/v1/sites/{site_id}/build").status_code == 404
        assert client.post(f"/api/v1/sites/{site_id}/rollback").status_code == 404
        assert client.post(f"/api/v1/publish/sites/{site_id}/publish", json={}).status_code == 404
        assert client.get(f"/api/v1/publish/sites/{site_id}/pages").status_code == 404


def test_legacy_onboarding_routes_are_not_available_in_the_release_api():
    paths = app.openapi()["paths"]

    assert "/api/v1/onboarding/start" not in paths
    assert "/api/v1/onboarding/{session_id}/step" not in paths
    assert "/api/v1/onboarding/health-check" not in paths

    with TestClient(app) as client:
        assert client.post("/api/v1/onboarding/start", json={"niche": "repair"}).status_code == 404
        assert client.post("/api/v1/onboarding/example/step", json={}).status_code == 404
        assert client.get("/api/v1/onboarding/health-check").status_code == 404


def test_system_control_routes_are_registered_in_the_release_api():
    paths = app.openapi()["paths"]

    assert "/api/v1/system/control" in paths
    assert "post" in paths["/api/v1/system/updates"]
    assert "post" in paths["/api/v1/system/recovery"]


def test_security_session_routes_are_registered():
    paths = app.openapi()["paths"]

    assert "/api/v1/security/me" in paths
    assert "/api/v1/security/dsar" not in paths
    assert "get" in paths["/api/v1/security/sessions"]
    assert "post" in paths["/api/v1/security/sessions/{session_id}/revoke"]
    with TestClient(app) as client:
        assert client.post("/api/v1/security/dsar", json={}).status_code == 404


def test_public_registration_is_not_exposed():
    assert "/api/v1/auth/register" not in app.openapi()["paths"]
    with TestClient(app) as client:
        assert client.post("/api/v1/auth/register", json={}).status_code == 404


def test_unmanaged_wayback_redirect_api_is_not_exposed():
    assert "/api/v1/seo/wayback-301" not in app.openapi()["paths"]


def test_unmanaged_ops_apis_are_not_exposed():
    paths = app.openapi()["paths"]

    assert "/api/v1/ops/serp/check" not in paths
    assert "/api/v1/ops/decay" not in paths
    assert "/api/v1/ops/staging" not in paths

    with TestClient(app) as client:
        assert client.post("/api/v1/ops/serp/check", json={}).status_code == 404
        assert client.post("/api/v1/ops/decay", json={}).status_code == 404
        assert client.post("/api/v1/ops/staging", json={}).status_code == 404


def test_unmanaged_analytics_apis_are_not_exposed():
    paths = app.openapi()["paths"]

    assert "/api/v1/analytics/collect" not in paths
    assert "/api/v1/analytics/consent" not in paths
    assert "/api/v1/analytics/pixel.js" not in paths

    with TestClient(app) as client:
        assert client.post("/api/v1/analytics/collect", json={}).status_code == 404
        assert client.post("/api/v1/analytics/consent", json={}).status_code == 404
        assert client.get("/api/v1/analytics/pixel.js").status_code == 404


def test_legacy_taxonomy_and_upload_apis_are_not_exposed():
    paths = app.openapi()["paths"]

    assert "/api/v1/taxonomy" not in paths
    assert "/api/v1/uploads/image" not in paths

    with TestClient(app) as client:
        assert client.get("/api/v1/taxonomy").status_code == 404
        assert client.post("/api/v1/uploads/image", files={}).status_code == 404


def test_unready_generation_apis_are_not_exposed():
    paths = app.openapi()["paths"]

    assert "/api/v1/ai/micro-infill" not in paths
    assert "/api/v1/ai/prompts/seed" not in paths
    assert "/api/v1/competitors/scan" not in paths
    assert "/api/v1/competitors/scans" not in paths

    with TestClient(app) as client:
        assert client.post("/api/v1/ai/micro-infill", json={}).status_code == 404
        assert client.post("/api/v1/ai/prompts/seed", json={}).status_code == 404
        assert client.post("/api/v1/competitors/scan", json={}).status_code == 404
        assert client.get("/api/v1/competitors/scans").status_code == 404


def test_unready_custom_block_apis_are_not_exposed():
    paths = app.openapi()["paths"]

    assert "/api/v1/blocks" not in paths
    assert "/api/v1/blocks/kits/register" not in paths
    assert "/api/v1/blocks/seed-defaults" not in paths

    with TestClient(app) as client:
        assert client.get("/api/v1/blocks").status_code == 404
        assert client.post("/api/v1/blocks", json={}).status_code == 404
        assert client.post("/api/v1/blocks/kits/register", json={}).status_code == 404
        assert client.post("/api/v1/blocks/seed-defaults", json={}).status_code == 404


def test_unverified_bulk_apis_are_not_exposed():
    paths = app.openapi()["paths"]

    assert "/api/v1/bulk/publish" not in paths
    assert "/api/v1/bulk/redirects" not in paths
    assert "/api/v1/bulk/indexnow" not in paths

    with TestClient(app) as client:
        assert client.post("/api/v1/bulk/publish", json={}).status_code == 404
        assert client.post("/api/v1/bulk/redirects", json={}).status_code == 404
        assert client.post("/api/v1/bulk/indexnow", json={}).status_code == 404


def test_unverified_indexing_apis_are_not_exposed():
    paths = app.openapi()["paths"]

    assert "/api/v1/publish/drip/run" not in paths
    assert "/api/v1/publish/force-index" not in paths
    assert "/api/v1/publish/indexnow/{site_id}" not in paths
    assert "/api/v1/publish/queue-index" not in paths

    with TestClient(app) as client:
        assert client.post("/api/v1/publish/drip/run").status_code == 404
        assert client.post("/api/v1/publish/force-index", json={}).status_code == 404
        assert (
            client.post("/api/v1/publish/indexnow/00000000-0000-0000-0000-000000000000").status_code
            == 404
        )
        assert client.post("/api/v1/publish/queue-index", json={}).status_code == 404


def test_unverified_compliance_apis_are_not_exposed():
    paths = app.openapi()["paths"]

    assert "/api/v1/compliance/finops" not in paths
    assert "/api/v1/compliance/dr/status" not in paths
    assert "/api/v1/compliance/legal/subprocessors" not in paths

    with TestClient(app) as client:
        assert client.get("/api/v1/compliance/finops").status_code == 404
        assert client.get("/api/v1/compliance/dr/status").status_code == 404
        assert client.get("/api/v1/compliance/legal/subprocessors").status_code == 404


def test_hidden_panel_utilities_are_not_exposed():
    paths = app.openapi()["paths"]

    assert "/api/v1/panel/search" not in paths
    assert "/api/v1/panel/notifications" not in paths
    assert "/api/v1/panel/audit" not in paths
    assert "/api/v1/panel/views" not in paths
    assert "/api/v1/panel/branding" not in paths
    assert "/api/v1/panel/reports/export.csv" not in paths

    with TestClient(app) as client:
        assert client.get("/api/v1/panel/search", params={"q": "x"}).status_code == 404
        assert client.get("/api/v1/panel/notifications").status_code == 404
        assert client.get("/api/v1/panel/audit").status_code == 404
        assert client.post("/api/v1/panel/views", json={}).status_code == 404
        assert client.patch("/api/v1/panel/branding", json={}).status_code == 404
        assert client.get("/api/v1/panel/reports/export.csv").status_code == 404


def test_legacy_saas_surfaces_are_not_exposed():
    paths = app.openapi()["paths"]

    assert "/api/v1/tenants" not in paths
    assert "/api/v1/panel/api-keys" not in paths
    assert "/api/v1/panel/webhooks" not in paths
    assert "/api/v1/panel/plugins" not in paths

    with TestClient(app) as client:
        assert client.get("/api/v1/tenants").status_code == 404
        assert client.post("/api/v1/panel/api-keys", json={}).status_code == 404
        assert client.post("/api/v1/panel/webhooks", json={}).status_code == 404
        assert client.get("/api/v1/panel/plugins").status_code == 404
