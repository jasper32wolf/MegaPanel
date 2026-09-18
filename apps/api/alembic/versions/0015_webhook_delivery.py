"""Persist durable delivery state for lead webhooks."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_webhook_delivery"
down_revision: str | None = "0014_redirect_path_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation_{table} ON {table}
        USING (
          current_setting('app.bypass_rls', true) = 'on'
          OR tenant_id::text = current_setting('app.tenant_id', true)
        )
        WITH CHECK (
          current_setting('app.bypass_rls', true) = 'on'
          OR tenant_id::text = current_setting('app.tenant_id', true)
        )
        """
    )


def upgrade() -> None:
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_key", sa.String(64), nullable=False),
        sa.Column("target_url", sa.Text(), nullable=False),
        sa.Column("target_secret_enc", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), server_default="queued", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(512), nullable=True),
        sa.Column("last_http_status", sa.Integer(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("lead_id", "target_key", name="uq_webhook_delivery_lead_target"),
    )
    op.create_index("ix_webhook_deliveries_tenant_id", "webhook_deliveries", ["tenant_id"])
    op.create_index("ix_webhook_deliveries_site_id", "webhook_deliveries", ["site_id"])
    op.create_index("ix_webhook_deliveries_lead_id", "webhook_deliveries", ["lead_id"])
    op.create_index("ix_webhook_deliveries_status", "webhook_deliveries", ["status"])
    op.create_index(
        "ix_webhook_deliveries_next_attempt_at", "webhook_deliveries", ["next_attempt_at"]
    )

    op.create_table(
        "webhook_delivery_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "delivery_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("webhook_deliveries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(512), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("delivery_id", "sequence", name="uq_webhook_delivery_attempt_sequence"),
    )
    op.create_index(
        "ix_webhook_delivery_attempts_delivery_id", "webhook_delivery_attempts", ["delivery_id"]
    )
    op.create_index(
        "ix_webhook_delivery_attempts_tenant_id", "webhook_delivery_attempts", ["tenant_id"]
    )

    _enable_rls("webhook_deliveries")
    _enable_rls("webhook_delivery_attempts")


def downgrade() -> None:
    for table in ("webhook_delivery_attempts", "webhook_deliveries"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
    op.drop_table("webhook_delivery_attempts")
    op.drop_table("webhook_deliveries")
