"""Add versioned lead routing policies and delivery aggregates."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032_lead_routing_policy"
down_revision: str | None = "0031_site_build_page_metadata_snapshot"
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
        "lead_routing_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "supersedes_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("lead_routing_policies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("project_id", "version", name="uq_lead_routing_policy_version"),
    )
    op.create_index("ix_lead_routing_policies_tenant_id", "lead_routing_policies", ["tenant_id"])
    op.create_index("ix_lead_routing_policies_project_id", "lead_routing_policies", ["project_id"])
    op.create_index("ix_lead_routing_policies_site_id", "lead_routing_policies", ["site_id"])
    op.create_index("ix_lead_routing_policies_state", "lead_routing_policies", ["state"])
    op.create_index(
        "uq_lead_routing_policy_active_site",
        "lead_routing_policies",
        ["site_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )

    op.create_table(
        "lead_routing_destinations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "policy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("lead_routing_policies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_key", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("target_url", sa.Text(), nullable=True),
        sa.Column("target_recipient_enc", sa.Text(), nullable=True),
        sa.Column("target_secret_enc", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("policy_id", "target_key", name="uq_lead_routing_destination_key"),
    )
    op.create_index(
        "ix_lead_routing_destinations_tenant_id", "lead_routing_destinations", ["tenant_id"]
    )
    op.create_index(
        "ix_lead_routing_destinations_policy_id", "lead_routing_destinations", ["policy_id"]
    )

    op.create_table(
        "lead_delivery_aggregates",
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="not_configured"),
        sa.Column("expected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivered_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attention_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("oldest_pending_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_lead_delivery_aggregates_tenant_id", "lead_delivery_aggregates", ["tenant_id"]
    )
    op.create_index("ix_lead_delivery_aggregates_status", "lead_delivery_aggregates", ["status"])

    op.add_column(
        "webhook_deliveries",
        sa.Column(
            "routing_policy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("lead_routing_policies.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "webhook_deliveries", sa.Column("routing_policy_version", sa.Integer(), nullable=True)
    )
    op.add_column(
        "webhook_deliveries", sa.Column("routing_policy_hash", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("webhook_deliveries", "required", server_default=None)

    for table in (
        "lead_routing_policies",
        "lead_routing_destinations",
        "lead_delivery_aggregates",
    ):
        _enable_rls(table)


def downgrade() -> None:
    for table in (
        "lead_delivery_aggregates",
        "lead_routing_destinations",
        "lead_routing_policies",
    ):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
    op.drop_column("webhook_deliveries", "required")
    op.drop_column("webhook_deliveries", "routing_policy_hash")
    op.drop_column("webhook_deliveries", "routing_policy_version")
    op.drop_column("webhook_deliveries", "routing_policy_id")
    op.drop_table("lead_delivery_aggregates")
    op.drop_table("lead_routing_destinations")
    op.drop_table("lead_routing_policies")
