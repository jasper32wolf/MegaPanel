from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = ROOT / "infra" / "docker" / "docker-compose.production.yml"
CADDYFILE_PATH = ROOT / "infra" / "caddy" / "Caddyfile.production"
WORKER_DOCKERFILE_PATH = ROOT / "infra" / "docker" / "Dockerfile.worker"


def production_compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_production_compose_exposes_only_caddy_http_ports():
    services = production_compose()["services"]

    assert services["caddy"]["ports"] == ["80:80", "443:443"]
    for name in ("postgres", "redis", "migrate", "api", "worker", "panel"):
        assert "ports" not in services[name]


def test_production_services_stay_on_the_internal_network():
    services = production_compose()["services"]

    for service in services.values():
        assert service["networks"] == ["internal"]


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


def test_worker_image_uses_the_api_owned_delivery_registry():
    dockerfile = WORKER_DOCKERFILE_PATH.read_text(encoding="utf-8")

    assert "COPY apps/api /app/apps/api" in dockerfile
    assert "WORKDIR /app/apps/api" in dockerfile
    assert 'CMD ["arq", "app.worker.WorkerSettings"]' in dockerfile


def test_release_worker_runs_only_lead_delivery_tasks():
    worker = (ROOT / "apps" / "api" / "app" / "worker.py").read_text(encoding="utf-8")

    assert "drip_promote_task" not in worker
    assert "dsar_process_task" not in worker
    assert "dsar_cleanup_task" not in worker
    assert "webhook_delivery_task" in worker
    assert "webhook_delivery_sweep_task" in worker
