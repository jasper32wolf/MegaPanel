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
