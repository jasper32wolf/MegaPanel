"""First-party, consent-gated telemetry primitives without client tenant identity."""

from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import urlsplit
from uuid import UUID

from app.core.config import get_settings

_ALLOWED_EVENTS = frozenset(
    {
        "page_view",
        "form_open",
        "form_submit_result",
        "cta_click",
        "assistant_open",
        "assistant_submit",
        "exit_offer_shown",
        "exit_offer_accepted",
    }
)


def telemetry_token(*, site_id: UUID, domain: str) -> str:
    """Create a public ingestion capability scoped to one canonical site host."""
    payload = f"{site_id}:{domain.strip().lower()}".encode()
    digest = hmac.new(
        get_settings().app_secret_key.encode("utf-8"), payload, hashlib.sha256
    ).digest()
    signature = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"{site_id}.{signature}"


def verify_telemetry_token(*, token: str, site_id: UUID, domain: str) -> bool:
    return hmac.compare_digest(token, telemetry_token(site_id=site_id, domain=domain))


def normalize_telemetry_path(value: str | None) -> str | None:
    if not value or len(value) > 512:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        return None
    return parsed.path


def session_digest(value: str | None) -> str | None:
    if not value or len(value) < 16 or len(value) > 128:
        return None
    return hmac.new(
        get_settings().blind_index_pepper.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()


__all__ = [
    "_ALLOWED_EVENTS",
    "normalize_telemetry_path",
    "session_digest",
    "telemetry_token",
    "verify_telemetry_token",
]
