from site_panel_security.crypto import BlindIndex, FieldEncryptor
from site_panel_security.ssrf import SSRFBlockedError, SSRFGuard
import pytest


def test_field_encrypt_roundtrip():
    enc = FieldEncryptor.from_base64("dev-field-encryption-key-change-me")
    token = enc.encrypt("+79991234567")
    assert enc.decrypt(token) == "+79991234567"
    assert token != "+79991234567"


def test_blind_index_stable():
    bi = BlindIndex("pepper")
    assert bi.index("  Foo ") == bi.index("foo")


def test_ssrf_blocks_localhost():
    guard = SSRFGuard()
    with pytest.raises(SSRFBlockedError):
        guard.resolve_safe("127.0.0.1")
