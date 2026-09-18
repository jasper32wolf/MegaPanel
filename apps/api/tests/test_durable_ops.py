from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.services import dsar
from app.services.audit import ZERO_HASH, audit_record_hash, verify_audit_chain


def test_purge_expired_dsar_exports_only_removes_old_files(tmp_path: Path, monkeypatch):
    expired = tmp_path / "tenant" / "expired.json"
    fresh = tmp_path / "tenant" / "fresh.json"
    expired.parent.mkdir(parents=True)
    expired.write_text("{}", encoding="utf-8")
    fresh.write_text("{}", encoding="utf-8")
    now = datetime.now(UTC)
    old_timestamp = (now - timedelta(hours=25)).timestamp()
    os.utime(expired, (old_timestamp, old_timestamp))
    monkeypatch.setattr(
        dsar,
        "get_settings",
        lambda: SimpleNamespace(dsar_exports_root=str(tmp_path), dsar_export_ttl_hours=24),
    )

    assert dsar.purge_expired_exports(now) == 1
    assert not expired.exists()
    assert fresh.exists()


def test_dsar_matches_any_provided_subject_identifier():
    class Encryptor:
        def decrypt(self, value: str) -> str:
            return value

    email_match = SimpleNamespace(email_enc="subject@example.test", phone_blind="other-phone")
    phone_match = SimpleNamespace(email_enc="other@example.test", phone_blind="subject-phone")

    assert dsar._matches_subject(email_match, Encryptor(), "subject@example.test", "subject-phone")
    assert dsar._matches_subject(phone_match, Encryptor(), "subject@example.test", "subject-phone")


def test_audit_chain_verifier_accepts_valid_entries_and_flags_tampering():
    tenant_id = uuid4()
    first_payload = {"site": "one"}
    first_hash = audit_record_hash(
        action="site.create",
        payload=first_payload,
        tenant_id=tenant_id,
        actor_id=None,
        prev_hash=ZERO_HASH,
    )
    first = SimpleNamespace(
        id=1,
        action="site.create",
        payload=first_payload,
        tenant_id=tenant_id,
        actor_id=None,
        prev_hash=ZERO_HASH,
        record_hash=first_hash,
    )
    second_payload = {"site": "two"}
    second_hash = audit_record_hash(
        action="site.create",
        payload=second_payload,
        tenant_id=tenant_id,
        actor_id=None,
        prev_hash=first_hash,
    )
    second = SimpleNamespace(
        id=2,
        action="site.create",
        payload=second_payload,
        tenant_id=tenant_id,
        actor_id=None,
        prev_hash=first_hash,
        record_hash=second_hash,
    )

    assert verify_audit_chain([first, second]) == []
    second.payload = {"site": "tampered"}
    assert verify_audit_chain([first, second]) == [2]
