from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PREPARE_ENV = ROOT / "scripts" / "prepare_env.py"
VALIDATOR = ROOT / "scripts" / "validate_production_env.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def test_production_env_is_written_only_to_explicit_target(tmp_path: Path):
    output = tmp_path / "shared.env"
    repository_env = ROOT / ".env"
    before = repository_env.read_bytes() if repository_env.exists() else None

    result = run(
        str(PREPARE_ENV),
        "--production",
        "--output",
        str(output),
        "--panel-domain",
        "panel.acme.test",
        "--api-domain",
        "api.acme.test",
        "--caddy-email",
        "ops@acme.test",
    )

    assert result.returncode == 0, result.stderr
    text = output.read_text(encoding="utf-8")
    assert "PANEL_PUBLIC_URL=https://panel.acme.test" in text
    assert "API_PUBLIC_URL=https://api.acme.test" in text
    assert "CORS_ORIGINS=https://panel.acme.test" in text
    assert "APP_ENV=production" in text
    after = repository_env.read_bytes() if repository_env.exists() else None
    assert after == before


def test_production_validator_rejects_inconsistent_public_origin(tmp_path: Path):
    env_file = tmp_path / "shared.env"
    env_file.write_text(
        "\n".join(
            [
                "APP_ENV=production",
                "PANEL_DOMAIN=panel.acme.test",
                "API_DOMAIN=api.acme.test",
                "CADDY_EMAIL=ops@acme.test",
                "PANEL_PUBLIC_URL=https://panel.acme.test",
                "API_PUBLIC_URL=https://api.acme.test",
                "CORS_ORIGINS=https://other.acme.test",
                "POSTGRES_DB=site_panel",
                "POSTGRES_USER=site_panel",
                f"POSTGRES_PASSWORD={'p' * 16}",
                f"APP_SECRET_KEY={'a' * 16}",
                f"APP_PEPPER={'b' * 16}",
                f"BLIND_INDEX_PEPPER={'c' * 16}",
                f"FIELD_ENCRYPTION_KEY={'d' * 16}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = run(str(VALIDATOR), "--env-file", str(env_file))

    assert result.returncode == 1
    assert "cors_origins_must_equal_panel_origin" in result.stderr
