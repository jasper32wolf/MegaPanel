from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = ROOT / "infra" / "docker" / "docker-compose.production.yml"
CADDYFILE_PATH = ROOT / "infra" / "caddy" / "Caddyfile.production"
DEV_CADDYFILE_PATH = ROOT / "infra" / "caddy" / "Caddyfile"
WORKER_DOCKERFILE_PATH = ROOT / "infra" / "docker" / "Dockerfile.worker"
PROVISIONER_PATH = ROOT / "scripts" / "install-production-vps.sh"
DEV_INSTALLER_PATH = ROOT / "scripts" / "install.sh"
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"
PANEL_ROOT = ROOT / "apps" / "panel"


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


def test_panel_uses_bundled_logo_and_favicon():
    index = (PANEL_ROOT / "index.html").read_text(encoding="utf-8")
    shell = (PANEL_ROOT / "src" / "components" / "Shell.tsx").read_text(encoding="utf-8")
    login = (PANEL_ROOT / "src" / "pages" / "LoginPage.tsx").read_text(encoding="utf-8")

    assert (PANEL_ROOT / "public" / "site-panel-mark.svg").is_file()
    assert 'rel="icon" type="image/svg+xml" href="/site-panel-mark.svg"' in index
    assert 'src="/site-panel-mark.svg"' in shell
    assert 'src="/site-panel-mark.svg"' in login


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


def test_caddy_state_volumes_are_prepared_before_non_root_caddy_starts():
    services = production_compose()["services"]
    state_init = services["caddy-state-init"]
    caddy = services["caddy"]

    assert state_init["user"] == "0:0"
    assert state_init["read_only"] is True
    assert state_init["cap_drop"] == ["ALL"]
    assert state_init["cap_add"] == ["CHOWN", "FOWNER", "DAC_OVERRIDE"]
    assert state_init["entrypoint"] == ["/bin/sh", "-ec"]
    assert state_init["command"] == ["chown 10001:10001 /data /config"]
    assert {volume["target"] for volume in state_init["volumes"]} == {"/data", "/config"}
    assert all(volume["volume"] == {"nocopy": True} for volume in state_init["volumes"])
    assert caddy["user"] == "10001:10001"
    assert caddy["depends_on"]["caddy-state-init"] == {
        "condition": "service_completed_successfully"
    }


def test_worker_healthcheck_uses_arq_liveness_key():
    worker = production_compose()["services"]["worker"]

    assert worker["healthcheck"] == {
        "test": ["CMD", "arq", "--check", "app.worker.WorkerSettings"],
        "interval": "30s",
        "timeout": "5s",
        "retries": 3,
        "start_period": "40s",
    }
    source = (ROOT / "apps" / "api" / "app" / "worker.py").read_text(encoding="utf-8")
    assert "health_check_interval = 30" in source


def test_caddy_proxies_only_through_the_public_origins():
    config = CADDYFILE_PATH.read_text(encoding="utf-8")

    assert "admin 0.0.0.0:2019" in config
    assert "port 2019 is deliberately not published on the host" in config
    assert "reverse_proxy api:8000" in config
    assert "reverse_proxy panel:80" in config
    assert "/.well-known/security.txt" not in config
    assert "Strict-Transport-Security" in config
    assert config.count('Permissions-Policy "geolocation=(), microphone=(), camera=()"') == 2
    assert config.count("X-Frame-Options DENY") == 2


def test_caddy_fallback_never_serves_candidate_release_volume():
    for path in (CADDYFILE_PATH, DEV_CADDYFILE_PATH):
        config = path.read_text(encoding="utf-8")

        assert "root * /srv/sites" not in config
        assert "file_server" not in config
        assert 'respond "Not found" 404' in config


def test_ci_validates_production_caddyfile_with_caddy_binary():
    workflow = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["release-automation"]["steps"]
    command = next(
        step["run"] for step in steps if step.get("name") == "Validate production Caddyfile"
    )

    assert "caddy:2.8-alpine" in command
    assert "Caddyfile.production:/etc/caddy/Caddyfile:ro" in command
    assert "caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile" in command
    for name in ("CADDY_EMAIL", "PANEL_DOMAIN", "API_DOMAIN"):
        assert f"--env {name}=" in command


def test_worker_image_uses_the_api_owned_delivery_registry():
    dockerfile = WORKER_DOCKERFILE_PATH.read_text(encoding="utf-8")

    assert "COPY apps/api /app/apps/api" in dockerfile
    assert "WORKDIR /app/apps/api" in dockerfile
    assert 'CMD ["arq", "app.worker.WorkerSettings"]' in dockerfile


def test_ci_runs_worker_production_image_liveness_smoke():
    workflow = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["integration-services"]["steps"]
    command = next(
        step["run"]
        for step in steps
        if step.get("name") == "Worker production image liveness smoke"
    )

    assert "docker build" in command
    assert "infra/docker/Dockerfile.worker" in command
    assert "docker run --detach --rm" in command
    assert "--network host" in command
    assert "arq --check app.worker.WorkerSettings" in command
    assert "docker rm --force" in command


def test_ci_proves_upgrade_path_from_0031_to_current_head():
    workflow = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
    job = workflow["jobs"]["integration-services"]
    step = next(
        step
        for step in job["steps"]
        if step.get("name") == "Upgrade-path migration proof from 0031"
    )
    command = step["run"]

    assert "alembic downgrade 0031_site_build_page_metadata_snapshot" in command
    assert "alembic upgrade head" in command
    assert "SELECT version_num FROM alembic_version" in command
    assert "get_current_head()" in command


def test_ci_publishes_controlled_release_restore_harness_evidence():
    workflow = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["release-automation"]["steps"]
    harness = next(
        step for step in steps if step.get("name") == "Controlled release and restore harness"
    )
    artifact = next(step for step in steps if step.get("name") == "Upload release harness evidence")

    assert "bash scripts/tests/test_release_manager.sh" in harness["run"]
    assert "site-panel-release-harness.log" in harness["run"]
    assert artifact["uses"] == "actions/upload-artifact@v4"
    assert artifact["with"]["name"] == "release-harness-evidence"
    assert artifact["with"]["retention-days"] == 14


def test_ci_runs_authenticated_production_compose_smoke_through_caddy():
    workflow = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
    job = workflow["jobs"]["production-compose-smoke"]
    command = next(
        step["run"]
        for step in job["steps"]
        if step.get("name") == "Start isolated production Compose stack"
    )

    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == 20
    assert any(step.get("uses") == "actions/setup-node@v4" for step in job["steps"])
    browser_setup = next(
        step["run"]
        for step in job["steps"]
        if step.get("name") == "Install Chromium for Caddy browser smoke"
    )
    assert "npm ci" in browser_setup
    assert "npx playwright install --with-deps chromium" in browser_setup
    assert "APP_ENV=production" in command
    assert "PANEL_DOMAIN=localhost" in command
    assert "API_DOMAIN=api.localhost" in command
    assert "docker compose -f infra/docker/docker-compose.production.yml" in command
    assert "up --build --detach" in command
    assert "https://localhost/" in command
    assert '<div id="root"></div>' in command
    assert "CaddyClient().upsert_site_vhost(" in command
    assert '"site.localhost", "/srv/sites/compose-smoke/current"' in command
    assert "https://site.localhost${path}" in command
    assert "https://site.localhost/api/v1/leads/public" in command
    assert "compose-smoke-lead-token-00000001" in command
    assert "--tenant-slug compose-smoke" in command
    assert "Compose encrypted lead" in command
    assert 'lead.phone_enc != "+79990000000"' in command
    assert 'lead.message_enc != "Compose encrypted lead"' in command
    assert 'target_key="compose-worker-smoke"' in command
    assert "target_secret_enc=None" in command
    assert "assert await enqueue_delivery(delivery_id)" in command
    assert 'delivery.status == "dead_letter"' in command
    assert "https://localhost/api/v1/auth/login" in command
    assert 'result["ok"] is True and result["mfa_required"] is False' in command
    assert "https://localhost/api/v1/security/me" in command
    assert "chromium.launch" in command
    assert 'baseURL: "https://localhost"' in command
    assert 'getByRole("heading", { name: "Обзор" })' in command
    assert 'getByText("API: ok")' in command
    assert "exec -T worker arq --check app.worker.WorkerSettings" in command
    assert "exec -T caddy wget" in command
    assert "down --volumes --remove-orphans" in command


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


def test_production_compose_builds_one_api_image_for_migrate_api_and_worker():
    services = production_compose()["services"]

    assert services["api"]["image"] == "site-panel-api:local"
    assert services["migrate"]["image"] == services["api"]["image"]
    assert services["worker"]["image"] == services["api"]["image"]
    assert "build" in services["api"]
    assert "build" not in services["migrate"]
    assert "build" not in services["worker"]
    assert services["worker"]["command"] == ["arq", "app.worker.WorkerSettings"]
