from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.services.telemetry import (
    normalize_telemetry_path,
    session_digest,
    telemetry_token,
    verify_telemetry_token,
)


def test_telemetry_token_is_scoped_to_the_exact_site_and_domain():
    site_id = uuid4()
    token = telemetry_token(site_id=site_id, domain="example.test")

    assert verify_telemetry_token(token=token, site_id=site_id, domain="example.test")
    assert not verify_telemetry_token(token=token, site_id=site_id, domain="other.test")
    assert not verify_telemetry_token(token=token, site_id=uuid4(), domain="example.test")


def test_telemetry_rejects_query_urls_and_never_keeps_raw_session_identity():
    assert normalize_telemetry_path("/repair/") == "/repair/"
    assert normalize_telemetry_path("https://example.test/repair/") is None
    assert normalize_telemetry_path("/repair/?phone=123") is None
    assert normalize_telemetry_path("/repair/#contact") is None
    assert session_digest("session-identifier-123") != "session-identifier-123"
    assert session_digest("short") is None


def test_secure_telemetry_route_and_ssg_script_are_consent_gated():
    from app.main import app

    paths = app.openapi()["paths"]
    assert "post" in paths["/api/v1/telemetry/collect"]
    assert "get" in paths["/api/v1/telemetry/sites/{site_id}/summary"]

    root = Path(__file__).parents[1]
    route = (root / "app" / "api" / "v1" / "telemetry.py").read_text(encoding="utf-8")
    builder = (
        root.parents[1] / "packages" / "ssg" / "src" / "site_panel_ssg" / "builder.py"
    ).read_text(encoding="utf-8")

    assert "tenant_id" not in route.split("class TelemetryEventIn", 1)[1].split(
        "async def _site_from_public_token", 1
    )[0]
    assert "origin does not match the site" in route
    assert "client_ip" not in route.split("AnalyticsEvent(", 1)[1]
    assert "window.__spConsent?.analytics" in builder
    assert "navigator.globalPrivacyControl" in builder
