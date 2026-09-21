from app.services.hardening import EgressGuard, FinOpsLedger
from app.services.ops import (
    detect_decay,
    evergreen_refresh_text,
    footprint_scan_html,
    needs_serp_correction,
    regenerate_meta_local,
)


def test_serp_correction_gate():
    assert needs_serp_correction(50, 50) is True
    assert needs_serp_correction(10, 50) is False
    assert needs_serp_correction(None, 10) is False


def test_meta_regen():
    meta = regenerate_meta_local("Title", "H1", "Москве", "ремонт")
    assert "Москве" in meta["title"]
    assert "faq_q" in meta


def test_decay():
    assert detect_decay(100, 60) is True
    assert detect_decay(100, 90) is False


def test_evergreen_year():
    html = evergreen_refresh_text("Цены 2024 и {year}, {price_from}", year=2026)
    assert "2026" in html
    assert "990" in html


def test_footprint_risk():
    result = footprint_scan_html(
        '<div class="hero" data-block="faq"></div><a href="/wp-content/x">x</a>',
        {"primary-color": "#1a5f4a"},
        seed=3,
    )
    assert result["risk_score"] > 0
    assert any(f["code"] == "cms_path" for f in result["findings"])


def test_egress_and_finops():
    g = EgressGuard()
    assert g.check("https://api.deepseek.com/v1/chat") is True
    assert g.check("https://evil.example/x") is False
    ledger = FinOpsLedger()
    ledger.add("t1", "llm", 12.5)
    ledger.add("t1", "hosting", 5)
    assert ledger.total_for("t1") == 17.5
