"""Persist redacted operational events and alert incidents."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036_operational_events_incidents"
down_revision: str | None = "0035_page_index_promotions"
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
        "operational_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "alert_incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("signal_code", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    for table in ("operational_events", "alert_incidents"):
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        _enable_rls(table)
    op.create_index("ix_operational_events_event_type", "operational_events", ["event_type"])
    op.create_index("ix_operational_events_severity", "operational_events", ["severity"])
    op.create_index("ix_operational_events_outcome", "operational_events", ["outcome"])
    op.create_index("ix_alert_incidents_signal_code", "alert_incidents", ["signal_code"])
    op.create_index("ix_alert_incidents_severity", "alert_incidents", ["severity"])
    op.create_index("ix_alert_incidents_status", "alert_incidents", ["status"])
    op.create_index(
        "uq_alert_incidents_active_signal",
        "alert_incidents",
        ["tenant_id", "signal_code"],
        unique=True,
        postgresql_where=sa.text("status IN ('open', 'acknowledged')"),
    )


def downgrade() -> None:
    for table in ("alert_incidents", "operational_events"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.drop_table(table)
