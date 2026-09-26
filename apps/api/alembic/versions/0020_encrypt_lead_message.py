"""Encrypt persisted lead messages."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.core.config import get_settings
from site_panel_security import FieldEncryptor

revision: str = "0020_encrypt_lead_message"
down_revision: str | None = "0019_lead_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _encryptor() -> FieldEncryptor:
    return FieldEncryptor.from_base64(get_settings().field_encryption_key)


def upgrade() -> None:
    op.add_column("leads", sa.Column("message_enc", sa.Text(), nullable=True))
    bind = op.get_bind()
    encryptor = _encryptor()
    rows = bind.execute(sa.text("SELECT id, message FROM leads WHERE message IS NOT NULL")).all()
    for lead_id, message in rows:
        bind.execute(
            sa.text("UPDATE leads SET message_enc = :message_enc WHERE id = :lead_id"),
            {"lead_id": lead_id, "message_enc": encryptor.encrypt(str(message))},
        )
    op.drop_column("leads", "message")


def downgrade() -> None:
    op.add_column("leads", sa.Column("message", sa.Text(), nullable=True))
    bind = op.get_bind()
    encryptor = _encryptor()
    rows = bind.execute(
        sa.text("SELECT id, message_enc FROM leads WHERE message_enc IS NOT NULL")
    ).all()
    for lead_id, message_enc in rows:
        bind.execute(
            sa.text("UPDATE leads SET message = :message WHERE id = :lead_id"),
            {"lead_id": lead_id, "message": encryptor.decrypt(str(message_enc))},
        )
    op.drop_column("leads", "message_enc")
