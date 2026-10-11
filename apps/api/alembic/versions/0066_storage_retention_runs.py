"""Add durable previewed storage retention runs."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0066_storage_retention_runs"
down_revision: str | None = "0065_operator_alert_transport"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "storage_retention_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="preview"),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "preview", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column(
            "result", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('preview', 'queued', 'processing', 'succeeded', 'failed')",
            name="ck_storage_retention_run_status",
        ),
    )
    op.create_index("ix_storage_retention_runs_tenant_id", "storage_retention_runs", ["tenant_id"])
    op.create_index("ix_storage_retention_runs_status", "storage_retention_runs", ["status"])
    op.execute("ALTER TABLE storage_retention_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE storage_retention_runs FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_storage_retention_runs
        ON storage_retention_runs
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
    op.create_index(
        "ix_auth_sessions_retention",
        "auth_sessions",
        ["expires_at", "revoked_at"],
    )
    op.create_index(
        "ix_notifications_retention",
        "notifications",
        ["tenant_id", "read", "created_at"],
    )
    op.create_index(
        "ix_operational_events_retention",
        "operational_events",
        ["tenant_id", "occurred_at"],
    )
    op.create_index(
        "ix_webhook_delivery_attempts_retention",
        "webhook_delivery_attempts",
        ["tenant_id", "finished_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_delivery_attempts_retention", table_name="webhook_delivery_attempts")
    op.drop_index("ix_operational_events_retention", table_name="operational_events")
    op.drop_index("ix_notifications_retention", table_name="notifications")
    op.drop_index("ix_auth_sessions_retention", table_name="auth_sessions")
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_storage_retention_runs ON storage_retention_runs"
    )
    op.drop_table("storage_retention_runs")
