"""Support encrypted SMTP lead delivery targets."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_email_delivery_targets"
down_revision: str | None = "0023_prompt_registry_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "webhook_deliveries",
        sa.Column("channel", sa.String(length=16), nullable=False, server_default="webhook"),
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column("target_recipient_enc", sa.Text(), nullable=True),
    )
    op.alter_column("webhook_deliveries", "target_url", existing_type=sa.Text(), nullable=True)
    op.create_index("ix_webhook_deliveries_channel", "webhook_deliveries", ["channel"])


def downgrade() -> None:
    op.drop_index("ix_webhook_deliveries_channel", table_name="webhook_deliveries")
    op.alter_column("webhook_deliveries", "target_url", existing_type=sa.Text(), nullable=False)
    op.drop_column("webhook_deliveries", "target_recipient_enc")
    op.drop_column("webhook_deliveries", "channel")
