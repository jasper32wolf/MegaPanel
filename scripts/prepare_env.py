#!/usr/bin/env python3
"""Generate or merge .env for Site Panel installers."""

from __future__ import annotations

import argparse
import base64
import os
import re
import secrets
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / ".env.example"
ENV_PATH = ROOT / ".env"


def _gen_secrets() -> dict[str, str]:
    return {
        "APP_SECRET_KEY": secrets.token_urlsafe(48),
        "APP_PEPPER": secrets.token_urlsafe(32),
        "BLIND_INDEX_PEPPER": secrets.token_urlsafe(32),
        "FIELD_ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode("ascii"),
    }


PLACEHOLDER_MARKERS = (
    "change-me",
    "change-me-to-a-long-random-string",
    "change-me-pepper-for-argon2",
    "change-me-blind-index-pepper",
    "change-me-32-byte-base64-key",
)


def _is_placeholder(value: str) -> bool:
    v = value.strip().strip('"').strip("'")
    return (not v) or any(m in v for m in PLACEHOLDER_MARKERS)


def parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, val = s.partition("=")
        out[key.strip()] = val.strip()
    return out


def upsert_env(path: Path, updates: dict[str, str], *, only_if_missing_or_placeholder: bool = True) -> None:
    if path.exists():
        text = path.read_text(encoding="utf-8")
    elif EXAMPLE.exists():
        text = EXAMPLE.read_text(encoding="utf-8")
    else:
        text = ""

    existing = parse_env(text)
    lines = text.splitlines() if text else []
    if not lines and EXAMPLE.exists():
        lines = EXAMPLE.read_text(encoding="utf-8").splitlines()

    applied: dict[str, str] = {}
    for key, new_val in updates.items():
        old = existing.get(key)
        if only_if_missing_or_placeholder:
            if old is not None and not _is_placeholder(old):
                continue
        applied[key] = new_val

    if not applied:
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return

    seen: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line)
        if m and m.group(1) in applied:
            key = m.group(1)
            new_lines.append(f"{key}={applied[key]}")
            seen.add(key)
        else:
            new_lines.append(line)

    for key, val in applied.items():
        if key not in seen:
            new_lines.append(f"{key}={val}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare Site Panel .env")
    parser.add_argument("--mode", choices=("local", "docker", "vps"), default="local")
    parser.add_argument("--force-secrets", action="store_true", help="Overwrite existing secrets")
    parser.add_argument("--demo-db-password", default="site_panel_dev")
    args = parser.parse_args()

    secrets_map = _gen_secrets()
    upsert_env(ENV_PATH, secrets_map, only_if_missing_or_placeholder=not args.force_secrets)

    if args.mode == "local":
        local_updates = {
            "APP_ENV": "development",
            "CORS_ORIGINS": "http://localhost:5173,http://127.0.0.1:5173",
            "PANEL_PUBLIC_URL": "http://localhost:5173",
            "API_PUBLIC_URL": "http://localhost:8000",
            "POSTGRES_HOST": "localhost",
            "POSTGRES_PORT": "5432",
            "POSTGRES_DB": "site_panel",
            "POSTGRES_USER": "site_panel",
            "POSTGRES_PASSWORD": args.demo_db_password,
            "DATABASE_URL": (
                f"postgresql+asyncpg://site_panel:{args.demo_db_password}@localhost:5432/site_panel"
            ),
            "REDIS_URL": "redis://localhost:6379/0",
            "CADDY_ADMIN_URL": "http://localhost:2019",
            "SITES_ROOT": str((ROOT / "data" / "sites").resolve()),
            "LOG_LEVEL": "INFO",
        }
        upsert_env(ENV_PATH, local_updates, only_if_missing_or_placeholder=False)
    elif args.mode == "docker":
        docker_updates = {
            "APP_ENV": "development",
            "CORS_ORIGINS": "http://localhost:5173,http://127.0.0.1:5173",
            "PANEL_PUBLIC_URL": "http://localhost:5173",
            "API_PUBLIC_URL": "http://localhost:8000",
            "POSTGRES_PASSWORD": args.demo_db_password,
            "DATABASE_URL": (
                f"postgresql+asyncpg://site_panel:{args.demo_db_password}@postgres:5432/site_panel"
            ),
            "REDIS_URL": "redis://redis:6379/0",
            "CADDY_ADMIN_URL": "http://caddy:2019",
            "SITES_ROOT": "/app/dist",
        }
        upsert_env(ENV_PATH, docker_updates, only_if_missing_or_placeholder=False)
    else:  # vps
        pw = secrets.token_urlsafe(24)
        vps_updates = {
            "APP_ENV": "production",
            "POSTGRES_PASSWORD": pw,
            "DATABASE_URL": f"postgresql+asyncpg://site_panel:{pw}@postgres:5432/site_panel",
            "REDIS_URL": "redis://redis:6379/0",
            "CADDY_ADMIN_URL": "http://caddy:2019",
            "SITES_ROOT": "/app/dist",
            "LOG_LEVEL": "INFO",
            # Public URLs left for operator — keep placeholders if empty
        }
        upsert_env(ENV_PATH, secrets_map, only_if_missing_or_placeholder=not args.force_secrets)
        upsert_env(ENV_PATH, vps_updates, only_if_missing_or_placeholder=False)

    (ROOT / "data" / "sites").mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "uploads").mkdir(parents=True, exist_ok=True)
    print(f"OK: wrote {ENV_PATH} (mode={args.mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
