from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from app.core.config import Settings
from app.services.captcha import captcha_public_config, verify_captcha


def test_captcha_is_disabled_by_default_and_has_no_public_config():
    settings = Settings()

    assert settings.captcha_enabled is False
    assert captcha_public_config(settings) is None
    assert asyncio.run(verify_captcha(None, settings=settings)).allowed is True


def test_enabled_captcha_requires_both_vps_keys():
    with pytest.raises(ValueError, match="CAPTCHA_SITE_KEY"):
        Settings(captcha_provider="cloudflare_turnstile", captcha_mode="always")

    settings = Settings(
        captcha_provider="cloudflare_turnstile",
        captcha_mode="always",
        captcha_site_key="public-site-key",
        captcha_secret="server-only-secret",
    )
    assert captcha_public_config(settings) == {
        "provider": "cloudflare_turnstile",
        "site_key": "public-site-key",
    }
    result = asyncio.run(verify_captcha(None, settings=settings))
    assert result.allowed is False
    assert result.code == "captcha_missing"


def test_captcha_contract_uses_fixed_https_providers_and_no_secret_in_ssg():
    root = Path(__file__).parents[1]
    service = (root / "app" / "services" / "captcha.py").read_text(encoding="utf-8")
    builder = (
        root.parents[1] / "packages" / "ssg" / "src" / "site_panel_ssg" / "builder.py"
    ).read_text(encoding="utf-8")

    assert "https://challenges.cloudflare.com/turnstile/v0/siteverify" in service
    assert "https://hcaptcha.com/siteverify" in service
    assert "https://www.google.com/recaptcha/api/siteverify" in service
    assert "follow_redirects=False" in service
    assert "site-panel-captcha.js" in builder
    assert "data-captcha-site-key" in builder
    assert "captcha_secret" not in builder
