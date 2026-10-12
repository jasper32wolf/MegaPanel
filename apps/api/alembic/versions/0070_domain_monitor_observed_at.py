"""Record bounded persisted monitoring observations for published domains."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0070_domain_monitor_observed_at"
down_revision: str | None = "0069_panel_lead_smtp_transport"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("domains", sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "site_monitor_observations",
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
            "domain_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("domains.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dns_status", sa.String(length=32), nullable=False),
        sa.Column("ssl_status", sa.String(length=32), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("signal_code", sa.String(length=64), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "domain_id", "observed_at", name="uq_site_monitor_observation_domain_time"
        ),
    )
    op.create_index(
        "ix_site_monitor_observations_tenant_id",
        "site_monitor_observations",
        ["tenant_id"],
    )
    op.create_index(
        "ix_site_monitor_observations_site_id",
        "site_monitor_observations",
        ["site_id"],
    )
    op.create_index(
        "ix_site_monitor_observations_domain_id",
        "site_monitor_observations",
        ["domain_id"],
    )
    op.create_index(
        "ix_site_monitor_observations_observed_at",
        "site_monitor_observations",
        ["observed_at"],
    )
    op.execute("ALTER TABLE site_monitor_observations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE site_monitor_observations FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_site_monitor_observations
        ON site_monitor_observations
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


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_site_monitor_observations "
        "ON site_monitor_observations"
    )
    op.drop_table("site_monitor_observations")
    op.drop_column("domains", "checked_at")
