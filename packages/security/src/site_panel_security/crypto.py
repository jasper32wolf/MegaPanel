from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class FieldEncryptor:
    """AES-256-GCM field-level encryption for PII."""

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("AES-256 key must be 32 bytes")
        self._aesgcm = AESGCM(key)

    @classmethod
    def from_base64(cls, key_b64: str) -> FieldEncryptor:
        raw = base64.urlsafe_b64decode(key_b64 + "==")
        # Accept 32-byte keys; pad/hash shorter demo keys for local dev
        if len(raw) != 32:
            raw = hashlib.sha256(key_b64.encode()).digest()
        return cls(raw)

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(12)
        ct = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.urlsafe_b64encode(nonce + ct).decode("ascii")

    def decrypt(self, token: str) -> str:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        nonce, ct = raw[:12], raw[12:]
        return self._aesgcm.decrypt(nonce, ct, None).decode("utf-8")


class BlindIndex:
    """HMAC-SHA256 blind index for searchable encrypted fields."""

    def __init__(self, pepper: str) -> None:
        self._pepper = pepper.encode("utf-8")

    def index(self, value: str) -> str:
        normalized = value.strip().lower()
        digest = hmac.new(self._pepper, normalized.encode("utf-8"), hashlib.sha256).hexdigest()
        return digest
