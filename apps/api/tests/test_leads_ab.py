from app.services.ab import assign_variant, significant
from app.services.leads import check_honeypot, check_time_lock, cro_score, qualify_lead_local, sign_webhook
import time


def test_honeypot_and_timelock():
    assert check_honeypot("http://spam") is True
    assert check_honeypot("") is False
    assert check_time_lock(time.time()) is True
    assert check_time_lock(time.time() - 5) is False


def test_qualify():
    assert qualify_lead_local("купить ссылки seo", "+79991234567") == "spam"
    assert qualify_lead_local("Нужен ремонт", "+79991234567") == "qualified"


def test_webhook_sign_stable():
    s1 = sign_webhook({"a": 1}, "secret")
    s2 = sign_webhook({"a": 1}, "secret")
    assert s1 == s2


def test_cro_score():
    result = cro_score("hero", ["offer", "cta_above_fold"])
    assert result["score"] < 100
    assert "utp" in result["missing"]


def test_ab_deterministic():
    a = assign_variant("visitor-1", "hero", ["A", "B"])
    b = assign_variant("visitor-1", "hero", ["A", "B"])
    assert a == b
    assert significant(30, 100, 50, 100) is False
    assert significant(20, 200, 40, 200) is True
