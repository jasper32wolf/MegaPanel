from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = ROOT / "infra" / "docker" / "docker-compose.production.yml"
CADDYFILE_PATH = ROOT / "infra" / "caddy" / "Caddyfile.production"
WORKER_DOCKERFILE_PATH = ROOT / "infra" / "docker" / "Dockerfile.worker"
PROVISIONER_PATH = ROOT / "scripts" / "install-production-vps.sh"
DEV_INSTALLER_PATH = ROOT / "scripts" / "install.sh"
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"


def production_compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_ci_publishes_a_cyclonedx_sbom_artifact():
    workflow = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["sbom"]["steps"]

    assert any(
        step.get("uses") == "aquasecurity/trivy-action@v0.36.0"
        and step.get("with", {}).get("format") == "cyclonedx"
        and step.get("with", {}).get("output") == "site-panel.cdx.json"
        for step in steps
    )
    assert any(
        step.get("uses") == "actions/upload-artifact@v4"
        and step.get("with", {}).get("path") == "site-panel.cdx.json"
        for step in steps
    )


def test_panel_e2e_always_uploads_playwright_diagnostics():
    workflow = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["panel-e2e"]["steps"]

    assert any(
        step.get("uses") == "actions/upload-artifact@v4"
        and step.get("if") == "always()"
        and step.get("with", {}).get("name") == "panel-e2e-diagnostics"
        and "apps/panel/playwright-report" in step.get("with", {}).get("path", "")
        and "apps/panel/test-results" in step.get("with", {}).get("path", "")
        for step in steps
    )


def test_production_compose_exposes_only_caddy_http_ports():
    services = production_compose()["services"]

    assert services["caddy"]["ports"] == ["80:80", "443:443"]
    for name in ("postgres", "redis", "migrate", "api", "worker", "panel"):
        assert "ports" not in services[name]


def test_production_services_stay_on_the_internal_network():
    services = production_compose()["services"]

    for service in services.values():
        assert service["networks"] == ["internal"]


def test_production_api_healthcheck_uses_liveness():
    healthcheck = production_compose()["services"]["api"]["healthcheck"]["test"]

    assert "/api/v1/health/live" in " ".join(healthcheck)
    assert "/api/v1/health/ready" not in " ".join(healthcheck)


def test_production_api_and_worker_are_hardened():
    services = production_compose()["services"]

    for name in ("api", "worker"):
        service = services[name]
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["tmpfs"] == ["/tmp"]


def test_caddy_proxies_only_through_the_public_origins():
    config = CADDYFILE_PATH.read_text(encoding="utf-8")

    assert "admin 0.0.0.0:2019" in config
    assert "port 2019 is deliberately not published on the host" in config
    assert "reverse_proxy api:8000" in config
    assert "reverse_proxy panel:80" in config
    assert "/.well-known/security.txt" not in config
    assert "Strict-Transport-Security" in config
    assert config.count('Permissions-Policy "geolocation=(), microphone=(), camera=()"') == 3
    assert config.count("X-Frame-Options DENY") == 3


def test_worker_image_uses_the_api_owned_delivery_registry():
    dockerfile = WORKER_DOCKERFILE_PATH.read_text(encoding="utf-8")

    assert "COPY apps/api /app/apps/api" in dockerfile
    assert "WORKDIR /app/apps/api" in dockerfile
    assert 'CMD ["arq", "app.worker.WorkerSettings"]' in dockerfile


def test_vps_provisioner_uses_immutable_production_release_path():
    provisioner = PROVISIONER_PATH.read_text(encoding="utf-8")
    development_installer = DEV_INSTALLER_PATH.read_text(encoding="utf-8")

    assert 'release-manager.sh" deploy' in provisioner
    assert 'git -C "$SOURCE_DIR" archive "$SOURCE_SHA"' in provisioner
    assert "docker-compose.yml" not in provisioner
    assert "install-production-vps.sh" in development_installer
    assert 'die "VPS production uses:' in development_installer


def test_release_worker_runs_only_lead_delivery_tasks():
    worker = (ROOT / "apps" / "api" / "app" / "worker.py").read_text(encoding="utf-8")

    assert "drip_promote_task" not in worker
    assert "dsar_process_task" not in worker
    assert "dsar_cleanup_task" not in worker
    assert "webhook_delivery_task" in worker
    assert "webhook_delivery_sweep_task" in worker
