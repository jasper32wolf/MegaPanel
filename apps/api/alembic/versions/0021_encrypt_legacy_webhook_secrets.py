"""Encrypt safe legacy webhook secrets."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.api.v1.sites import validate_webhook_target
from app.core.config import get_settings
from site_panel_security import FieldEncryptor
from sqlalchemy.dialects import postgresql

revision: str = "0021_encrypt_legacy_webhook_secrets"
down_revision: str | None = "0020_encrypt_lead_message"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _encryptor() -> FieldEncryptor:
    return FieldEncryptor.from_base64(get_settings().field_encryption_key)


def migrate_manifest(raw_manifest: object, encryptor: FieldEncryptor) -> dict | None:
    manifest = dict(raw_manifest or {})
    contacts = dict(manifest.get("contacts") or {})
    legacy_secret = str(contacts.get("webhook_secret") or "")
    if not legacy_secret:
        return None
    try:
        target_url = validate_webhook_target(str(contacts.get("webhook_url") or ""))
    except ValueError:
        return None
    contacts["webhook_url"] = target_url
    contacts["webhook_secret_enc"] = str(
        contacts.get("webhook_secret_enc") or encryptor.encrypt(legacy_secret)
    )
    contacts.pop("webhook_secret", None)
    manifest["contacts"] = contacts
    return manifest


def upgrade() -> None:
    bind = op.get_bind()
    sites = sa.table(
        "sites",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("manifest", postgresql.JSONB()),
    )
    rows = bind.execute(sa.select(sites.c.id, sites.c.manifest)).all()
    encryptor = _encryptor()
    for site_id, raw_manifest in rows:
        manifest = migrate_manifest(raw_manifest, encryptor)
        if manifest is not None:
            bind.execute(sites.update().where(sites.c.id == site_id).values(manifest=manifest))


def downgrade() -> None:
    return None
