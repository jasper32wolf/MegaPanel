from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.services.leads import get_encryptor


@dataclass(frozen=True)
class EncryptedSecret:
    ciphertext: str
    last4: str


def encrypt_provider_key(api_key: str) -> EncryptedSecret:
    value = api_key.strip()
    if not value:
        raise ValueError("Provider API key must not be empty")
    return EncryptedSecret(ciphertext=get_encryptor().encrypt(value), last4=value[-4:])


def decrypt_provider_key(ciphertext: str) -> str:
    return get_encryptor().decrypt(ciphertext)


def redact_secret(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"[redacted:{digest}]"
