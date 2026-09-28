"""Store private project lead recipient emails encrypted."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_encrypt_private_lead_email"
down_revision: str | None = "0021_encrypt_legacy_webhook_secrets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "project_fact_revisions",
        sa.Column("private_lead_email_enc", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project_fact_revisions", "private_lead_email_enc")
