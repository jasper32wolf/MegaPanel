"""Fixed-endpoint CAPTCHA verification; disabled unless explicitly configured."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

import httpx
from app.core.config import Settings, get_settings

CaptchaProvider = Literal["disabled", "cloudflare_turnstile", "hcaptcha", "google_recaptcha"]
_MAX_RESPONSE_BYTES = 32 * 1024
_VERIFY_URLS: dict[CaptchaProvider, str] = {
    "disabled": "",
    "cloudflare_turnstile": "https://challenges.cloudflare.com/turnstile/v0/siteverify",
    "hcaptcha": "https://hcaptcha.com/siteverify",
    "google_recaptcha": "https://www.google.com/recaptcha/api/siteverify",
}


@dataclass(frozen=True)
class CaptchaResult:
    allowed: bool
    code: str | None = None


def captcha_public_config(settings: Settings | None = None) -> dict[str, str] | None:
    config = settings or get_settings()
    if not config.captcha_enabled:
        return None
    return {"provider": config.captcha_provider, "site_key": config.captcha_site_key}


async def _bounded_json(response: httpx.Response) -> dict | None:
    length = response.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > _MAX_RESPONSE_BYTES):
        return None
    chunks = bytearray()
    async for chunk in response.aiter_bytes():
        chunks.extend(chunk)
        if len(chunks) > _MAX_RESPONSE_BYTES:
            return None
    try:
        value = json.loads(chunks)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


async def verify_captcha(
    token: str | None,
    *,
    remote_ip: str | None = None,
    settings: Settings | None = None,
) -> CaptchaResult:
    config = settings or get_settings()
    if not config.captcha_enabled:
        return CaptchaResult(allowed=True)
    if not token or len(token) > 4096:
        return CaptchaResult(allowed=False, code="captcha_missing")
    endpoint = _VERIFY_URLS[config.captcha_provider]
    form = {"secret": config.captcha_secret, "response": token}
    if remote_ip:
        form["remoteip"] = remote_ip
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            async with client.stream("POST", endpoint, data=form) as response:
                if response.status_code != 200:
                    return CaptchaResult(allowed=False, code="captcha_unavailable")
                payload = await _bounded_json(response)
    except httpx.HTTPError:
        return CaptchaResult(allowed=False, code="captcha_unavailable")
    if payload is None:
        return CaptchaResult(allowed=False, code="captcha_invalid_response")
    return CaptchaResult(
        allowed=payload.get("success") is True,
        code=None if payload.get("success") is True else "captcha_rejected",
    )


__all__ = ["CaptchaResult", "captcha_public_config", "verify_captcha"]
