#!/usr/bin/env python3
"""Validate Site Panel production environment files without exposing their values."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from pathlib import Path
from urllib.parse import urlparse

PLACEHOLDER_MARKERS = (
    "change-me",
    "example.com",
    "example.test",
    "example.invalid",
    "localhost",
)
REQUIRED_KEYS = (
    "APP_ENV",
    "PANEL_DOMAIN",
    "API_DOMAIN",
    "CADDY_EMAIL",
    "PANEL_PUBLIC_URL",
    "API_PUBLIC_URL",
    "CORS_ORIGINS",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "APP_SECRET_KEY",
    "APP_PEPPER",
    "BLIND_INDEX_PEPPER",
    "FIELD_ENCRYPTION_KEY",
)
HOSTNAME_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z",
    re.I,
)
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise ValueError(f"Invalid dotenv line {number}")
        key, value = stripped.split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"Invalid dotenv key on line {number}")
        values[key] = value
    return values


def is_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return not lowered or any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def is_valid_hostname(value: str) -> bool:
    return bool(HOSTNAME_RE.fullmatch(value)) and not is_placeholder(value)


def validate(values: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for key in REQUIRED_KEYS:
        value = values.get(key, "")
        if not value:
            errors.append(f"missing_{key}")
        elif key not in {"PANEL_DOMAIN", "API_DOMAIN", "CADDY_EMAIL"} and is_placeholder(value):
            errors.append(f"placeholder_{key}")

    if values.get("APP_ENV") != "production":
        errors.append("app_env_must_be_production")

    panel_domain = values.get("PANEL_DOMAIN", "")
    api_domain = values.get("API_DOMAIN", "")
    if not is_valid_hostname(panel_domain):
        errors.append("invalid_PANEL_DOMAIN")
    if not is_valid_hostname(api_domain):
        errors.append("invalid_API_DOMAIN")
    if panel_domain and panel_domain == api_domain:
        errors.append("panel_and_api_domains_must_differ")

    caddy_email = values.get("CADDY_EMAIL", "")
    if not EMAIL_RE.fullmatch(caddy_email) or is_placeholder(caddy_email):
        errors.append("invalid_CADDY_EMAIL")

    expected_urls = {
        "PANEL_PUBLIC_URL": f"https://{panel_domain}",
        "API_PUBLIC_URL": f"https://{api_domain}",
    }
    for key, expected in expected_urls.items():
        value = values.get(key, "")
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or parsed.netloc != expected.removeprefix("https://")
            or parsed.path not in ("", "/")
        ):
            errors.append(f"invalid_{key}")
        elif value.rstrip("/") != expected:
            errors.append(f"inconsistent_{key}")

    allowed_origins = [
        item.strip().rstrip("/")
        for item in values.get("CORS_ORIGINS", "").split(",")
        if item.strip()
    ]
    expected_origin = expected_urls["PANEL_PUBLIC_URL"]
    if allowed_origins != [expected_origin]:
        errors.append("cors_origins_must_equal_panel_origin")

    for key in (
        "POSTGRES_PASSWORD",
        "APP_SECRET_KEY",
        "APP_PEPPER",
        "BLIND_INDEX_PEPPER",
        "FIELD_ENCRYPTION_KEY",
    ):
        if len(values.get(key, "")) < 16:
            errors.append(f"weak_{key}")
    return errors


def validate_file(path: Path, *, require_secure_permissions: bool = False) -> list[str]:
    if not path.is_file():
        return ["env_file_missing"]
    errors = validate(parse_env(path))
    if require_secure_permissions and os.name != "nt":
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            errors.append("env_file_permissions_must_be_0600")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Site Panel production dotenv configuration"
    )
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--require-secure-permissions", action="store_true")
    args = parser.parse_args()
    try:
        errors = validate_file(
            args.env_file,
            require_secure_permissions=args.require_secure_permissions,
        )
    except (OSError, UnicodeError, ValueError) as error:
        print(f"result=error\nerror=invalid_env_file:{error}", file=sys.stderr)
        return 1
    if errors:
        print("result=error", file=sys.stderr)
        for error in errors:
            print(f"error={error}", file=sys.stderr)
        return 1
    print("result=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
