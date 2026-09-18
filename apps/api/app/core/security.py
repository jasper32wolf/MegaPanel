from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from app.core.config import get_settings
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

ACCESS_COOKIE_NAME = "site_panel_access"
REFRESH_COOKIE_NAME = "site_panel_refresh"
CSRF_COOKIE_NAME = "site_panel_csrf"

_ph = PasswordHasher()
settings = get_settings()


def hash_password(password: str) -> str:
    # Pepper concatenated before hash (TZ 12.1)
    return _ph.hash(password + settings.app_pepper)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password + settings.app_pepper)
    except VerifyMismatchError:
        return False


def create_token(
    subject: str,
    *,
    token_type: str,
    expires_delta: timedelta,
    extra: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        "jti": secrets.token_urlsafe(16),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.app_secret_key, algorithm="HS256")


def create_access_token(
    user_id: UUID,
    tenant_id: UUID | None,
    role: str,
    session_id: UUID | None = None,
) -> str:
    extra = {
        "tenant_id": str(tenant_id) if tenant_id else None,
        "role": role,
    }
    if session_id:
        extra["sid"] = str(session_id)
    return create_token(
        str(user_id),
        token_type="access",
        expires_delta=timedelta(minutes=settings.access_token_ttl_minutes),
        extra=extra,
    )


def create_refresh_token(user_id: UUID) -> str:
    return create_token(
        str(user_id),
        token_type="refresh",
        expires_delta=timedelta(days=settings.refresh_token_ttl_days),
    )


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.app_secret_key, algorithms=["HS256"])


def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
