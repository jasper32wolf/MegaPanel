from __future__ import annotations

import time
from uuid import uuid4

import pytest
from app.api.v1.leads import LeadStatusUpdate, PublicLeadIn, _csv_cell
from app.services.ab import assign_variant, significant
from app.services.leads import (
    check_honeypot,
    check_time_lock,
    cro_score,
    qualify_lead_local,
    sign_webhook,
)
from pydantic import ValidationError


def test_honeypot_and_timelock():
    assert check_honeypot("http://spam") is True
    assert check_honeypot("") is False
    assert check_time_lock(time.time()) is True
    assert check_time_lock(time.time() - 5) is False


def test_public_lead_requires_site_token():
    with pytest.raises(ValidationError, match="lead_token"):
        PublicLeadIn(site_id=uuid4(), phone="+79991234567", consent=True)


def test_lead_status_update_tracks_explicit_note_clear():
    update = LeadStatusUpdate(status="new", notes=None)
    assert "notes" in update.model_fields_set


@pytest.mark.parametrize("raw", ["=1+1", "+value", "-value", "@value", "\tvalue"])
def test_csv_cells_neutralize_spreadsheet_formulas(raw: str):
    assert _csv_cell(raw) == f"'{raw}"


def test_csv_cells_keep_normal_values():
    assert _csv_cell("plain text") == "plain text"


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
