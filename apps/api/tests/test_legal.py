from pathlib import Path

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
