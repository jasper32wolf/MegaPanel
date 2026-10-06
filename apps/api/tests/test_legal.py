from pathlib import Path

import pytest
from site_panel_ssg.legal import write_legal_pack


def test_legal_pack(tmp_path: Path):
    files = write_legal_pack(tmp_path, {"org": "ИП Тест", "inn": "123", "email": "a@b.c"})
    assert len(files) == 4
    assert (tmp_path / "privacy" / "index.html").exists()
    assert "152" in (tmp_path / "privacy" / "index.html").read_text(encoding="utf-8") or "ПДн" in (
        tmp_path / "privacy" / "index.html"
    ).read_text(encoding="utf-8")
    assert (tmp_path / "cookie-banner.js").exists()


def test_legal_pack_escapes_operator_fields(tmp_path: Path):
    write_legal_pack(
        tmp_path,
        {
            "org": '<img src=x onerror="alert(1)">',
            "inn": "<script>alert(1)</script>",
            "email": "privacy@example.com<script>",
            "address": "<b>address</b>",
        },
    )

    privacy = (tmp_path / "privacy" / "index.html").read_text(encoding="utf-8")
    terms = (tmp_path / "terms" / "index.html").read_text(encoding="utf-8")

    assert "<script>" not in privacy
    assert "<img " not in privacy
    assert "<img " not in terms
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in privacy
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in privacy
    assert "&lt;b&gt;address&lt;/b&gt;" in privacy


def test_legal_pack_does_not_invent_a_third_party_privacy_address(tmp_path: Path):
    write_legal_pack(tmp_path, {"org": "ИП Тест", "jurisdiction": "RU"})

    privacy = (tmp_path / "privacy" / "index.html").read_text(encoding="utf-8")
    assert "privacy@example.com" not in privacy
    assert "Юрисдикция: RU" in privacy


def test_legal_pack_exposes_withdrawal_and_actual_retention(tmp_path: Path):
    write_legal_pack(tmp_path, {"org": "ИП Тест"})

    privacy = (tmp_path / "privacy" / "index.html").read_text(encoding="utf-8")
    cookies = (tmp_path / "cookie-policy" / "index.html").read_text(encoding="utf-8")
    banner = (tmp_path / "cookie-banner.js").read_text(encoding="utf-8")

    assert "сырые события" in privacy.lower()
    assert "90 дней" in privacy and "730 дней" in privacy
    assert "Настройки приватности" in privacy and "Настройки приватности" in cookies
    assert "Consent Management модулем панели" not in cookies
    assert "sessionStorage.removeItem(sessionKey)" in banner
    assert 'new CustomEvent("sp:consent"' in banner
    assert "navigator.globalPrivacyControl" in banner


def test_legal_pack_uses_frozen_retention_periods(tmp_path: Path):
    write_legal_pack(tmp_path, {}, raw_days=45, aggregate_days=180)

    privacy = (tmp_path / "privacy" / "index.html").read_text(encoding="utf-8")
    assert "45 завершённых" in privacy
    assert "180 дней" in privacy
    with pytest.raises(ValueError, match="outside the supported range"):
        write_legal_pack(tmp_path, {}, raw_days=91)


def test_legal_pages_can_withdraw_consent_without_returning_to_a_content_page(tmp_path: Path):
    write_legal_pack(tmp_path, {}, telemetry_token='signed-token"<unsafe>')

    for page in ("privacy", "cookie-policy", "terms"):
        html = (tmp_path / page / "index.html").read_text(encoding="utf-8")
        assert 'src="/cookie-banner.js"' in html
        assert 'src="/site-panel-telemetry.js"' in html
        assert 'data-telemetry-token="signed-token&quot;&lt;unsafe&gt;"' in html
        assert 'signed-token"<unsafe>' not in html


def test_legal_pack_uses_explicit_public_privacy_contact_only(tmp_path: Path):
    write_legal_pack(
        tmp_path,
        {
            "org": "ИП Тест",
            "privacy_email": "privacy@example.com",
            "email": "private@example.com",
        },
    )

    privacy = (tmp_path / "privacy" / "index.html").read_text(encoding="utf-8")
    assert "privacy@example.com" in privacy
    assert "private@example.com" not in privacy
