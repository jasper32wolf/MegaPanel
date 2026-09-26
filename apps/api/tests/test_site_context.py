from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1 import bulk as bulk_api
from app.api.v1 import sites as sites_api
from app.api.v1.bulk import BulkEditBody
from app.api.v1.sites import WebhookSettingsIn, render_contacts, validate_webhook_target
from app.schemas.common import SiteCreate
from fastapi import HTTPException
from pydantic import ValidationError
from site_panel_shared.manifests import SiteManifest


def _legacy_secret_migration():
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / ("0021_encrypt_legacy_webhook_secrets.py")
    )
    spec = importlib.util.spec_from_file_location("legacy_webhook_secret_migration", migration_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WebhookSettingsDatabase:
    def __init__(self, site: object):
        self.site = site
        self.committed = False

    async def execute(self, _: object) -> object:
        return SimpleNamespace(scalar_one_or_none=lambda: self.site)

    async def commit(self) -> None:
        self.committed = True


def test_direct_site_creation_requires_service_and_city():
    with pytest.raises(ValidationError):
        SiteCreate(domain="example.test")


def test_site_manifest_context_defaults_to_empty_mapping():
    manifest = SiteManifest(site_id=uuid4(), tenant_id=uuid4(), domain="example.test")

    assert manifest.context == {}


@pytest.mark.parametrize(
    "raw",
    [
        "http://hooks.example.test/lead",
        "https://user:pass@hooks.example.test/lead",
        "https://hooks.example.test:8443/lead",
        "https://127.0.0.1/lead",
        "https://localhost/lead",
    ],
)
def test_webhook_target_rejects_nonpublic_or_non_https_urls(raw: str):
    with pytest.raises(ValueError):
        validate_webhook_target(raw)


def test_webhook_target_accepts_public_https_url():
    assert (
        validate_webhook_target("https://hooks.example.test/lead")
        == "https://hooks.example.test/lead"
    )


def test_legacy_webhook_secret_migration_encrypts_only_safe_configuration():
    class Encryptor:
        def encrypt(self, value: str) -> str:
            return f"encrypted:{value}"

    manifest = _legacy_secret_migration().migrate_manifest(
        {
            "contacts": {
                "webhook_url": " https://hooks.example.test/lead ",
                "webhook_secret": "legacy-secret",
            }
        },
        Encryptor(),
    )

    assert manifest == {
        "contacts": {
            "webhook_url": "https://hooks.example.test/lead",
            "webhook_secret_enc": "encrypted:legacy-secret",
        }
    }


def test_legacy_webhook_secret_migration_keeps_existing_ciphertext():
    class Encryptor:
        def encrypt(self, _: str) -> str:
            raise AssertionError("An existing ciphertext must not be replaced")

    manifest = _legacy_secret_migration().migrate_manifest(
        {
            "contacts": {
                "webhook_url": "https://hooks.example.test/lead",
                "webhook_secret": "legacy-secret",
                "webhook_secret_enc": "existing-ciphertext",
            }
        },
        Encryptor(),
    )

    assert manifest == {
        "contacts": {
            "webhook_url": "https://hooks.example.test/lead",
            "webhook_secret_enc": "existing-ciphertext",
        }
    }


def test_legacy_webhook_secret_migration_preserves_unsafe_target():
    manifest = {
        "contacts": {
            "webhook_url": "http://127.0.0.1/lead",
            "webhook_secret": "legacy-secret",
        }
    }

    result = _legacy_secret_migration().migrate_manifest(manifest, object())

    assert result is None
    assert manifest["contacts"]["webhook_secret"] == "legacy-secret"


def test_render_contacts_excludes_webhook_configuration():
    assert render_contacts(
        {
            "phone": "+79990000000",
            "webhook_url": "https://hooks.example.test/lead",
            "webhook_secret": "legacy-secret",
            "webhook_secret_enc": "encrypted-secret",
        }
    ) == {"phone": "+79990000000"}


def test_bulk_contacts_cannot_set_webhook_credentials():
    with pytest.raises(ValidationError, match="site webhook endpoint"):
        BulkEditBody(site_ids=[uuid4()], contacts={"webhook_secret": "do-not-store-this"})


def test_webhook_settings_do_not_accept_plaintext_legacy_secret():
    tenant_id = uuid4()
    site = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        manifest={
            "contacts": {
                "webhook_url": "https://hooks.example.test/lead",
                "webhook_secret": "legacy-plaintext",
            }
        },
    )
    db = WebhookSettingsDatabase(site)
    auth = SimpleNamespace(role="superadmin", tenant_id=tenant_id, user=SimpleNamespace(id=uuid4()))

    result = asyncio.run(sites_api.get_webhook_settings(site.id, auth, db))

    assert result == {
        "target_url": "https://hooks.example.test/lead",
        "secret_configured": False,
        "configured": False,
    }


def test_webhook_settings_encrypt_secret_and_redact_response(monkeypatch: pytest.MonkeyPatch):
    tenant_id = uuid4()
    site = SimpleNamespace(id=uuid4(), tenant_id=tenant_id, manifest={"contacts": {"phone": "+7"}})
    db = WebhookSettingsDatabase(site)
    auth = SimpleNamespace(role="superadmin", tenant_id=tenant_id, user=SimpleNamespace(id=uuid4()))
    audits: list[dict] = []

    class Encryptor:
        def encrypt(self, secret: str) -> str:
            return f"encrypted:{secret}"

    async def append_audit(*_: object, **payload: object) -> None:
        audits.append(payload)

    monkeypatch.setattr(sites_api, "get_encryptor", lambda: Encryptor())
    monkeypatch.setattr(sites_api, "append_audit", append_audit)

    result = asyncio.run(
        sites_api.update_webhook_settings(
            site.id,
            WebhookSettingsIn(url="https://hooks.example.test/lead", secret="a" * 16),
            auth,
            db,
        )
    )

    contacts = site.manifest["contacts"]
    assert contacts["webhook_secret_enc"] == f"encrypted:{'a' * 16}"
    assert "webhook_secret" not in contacts
    assert "secret" not in result
    assert audits == [
        {
            "action": "site.webhook.update",
            "payload": {"site_id": str(site.id), "target_url": "https://hooks.example.test/lead"},
            "tenant_id": tenant_id,
            "actor_id": auth.user.id,
        }
    ]
    assert db.committed


def test_bulk_contacts_require_a_nonempty_selection():
    with pytest.raises(ValidationError):
        BulkEditBody(site_ids=[], contacts={"phone": "+7"})


def test_bulk_contacts_reject_duplicates_and_no_op_edits():
    site_id = uuid4()

    with pytest.raises(ValidationError, match="duplicates"):
        BulkEditBody(site_ids=[site_id, site_id], contacts={"phone": "+7"})
    with pytest.raises(ValidationError, match="Select contacts or theme"):
        BulkEditBody(site_ids=[site_id])


def test_bulk_edit_rejects_missing_selected_sites_before_mutation():
    class Database:
        async def execute(self, _: object) -> object:
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    auth = SimpleNamespace(role="superadmin", tenant_id=uuid4(), user=SimpleNamespace(id=uuid4()))
    body = BulkEditBody(site_ids=[uuid4()], contacts={"phone": "+7"})

    with pytest.raises(HTTPException) as error:
        asyncio.run(bulk_api.bulk_edit(body, auth, Database()))

    assert error.value.status_code == 404
