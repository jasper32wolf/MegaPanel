"""Add durable operator alert delivery and scoped incidents."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0062_operator_alert_delivery"
down_revision: str | None = "0061_auth_session_context"
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
    op.add_column(
        "alert_incidents",
        sa.Column("subject_kind", sa.String(length=32), nullable=False, server_default="system"),
    )
    op.add_column(
        "alert_incidents",
        sa.Column("subject_key", sa.String(length=64), nullable=False, server_default=""),
    )
    op.drop_index("uq_alert_incidents_active_signal", table_name="alert_incidents")
    op.create_index(
        "uq_alert_incidents_active_subject",
        "alert_incidents",
        ["tenant_id", "signal_code", "subject_kind", "subject_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('open', 'acknowledged')"),
    )

    for column, type_ in (
        ("category", sa.String(length=16)),
        ("signal_code", sa.String(length=64)),
        ("subject_kind", sa.String(length=32)),
        ("subject_key", sa.String(length=64)),
    ):
        op.add_column("notifications", sa.Column(column, type_, nullable=True))
    op.execute("UPDATE notifications SET category = 'system' WHERE category IS NULL")
    op.alter_column("notifications", "category", nullable=False, server_default="system")
    op.create_index("ix_notifications_category", "notifications", ["category"])
    op.create_index("ix_notifications_signal_code", "notifications", ["signal_code"])

    op.create_table(
        "alert_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "notification_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("notifications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "alert_delivery_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "delivery_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("alert_deliveries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("delivery_id", "sequence", name="uq_alert_delivery_attempt_sequence"),
    )
    for table in ("alert_deliveries", "alert_delivery_attempts"):
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        _enable_rls(table)
    op.create_index("ix_alert_deliveries_notification_id", "alert_deliveries", ["notification_id"])
    op.create_index("ix_alert_deliveries_status", "alert_deliveries", ["status"])
    op.create_index(
        "ix_alert_delivery_attempts_delivery_id",
        "alert_delivery_attempts",
        ["delivery_id"],
    )


def downgrade() -> None:
    for table in ("alert_delivery_attempts", "alert_deliveries"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.drop_table(table)
    op.drop_index("ix_notifications_signal_code", table_name="notifications")
    op.drop_index("ix_notifications_category", table_name="notifications")
    for column in ("subject_key", "subject_kind", "signal_code", "category"):
        op.drop_column("notifications", column)
    op.drop_index("uq_alert_incidents_active_subject", table_name="alert_incidents")
    op.create_index(
        "uq_alert_incidents_active_signal",
        "alert_incidents",
        ["tenant_id", "signal_code"],
        unique=True,
        postgresql_where=sa.text("status IN ('open', 'acknowledged')"),
    )
    op.drop_column("alert_incidents", "subject_key")
    op.drop_column("alert_incidents", "subject_kind")
