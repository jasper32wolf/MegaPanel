from __future__ import annotations

import secrets

import pyotp


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def verify_totp(secret: str, code: str) -> bool:
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)


def provisioning_uri(secret: str, email: str, issuer: str = "Site Panel") -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)
