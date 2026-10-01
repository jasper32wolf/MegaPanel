"""Add idempotent GitHub workflow-run delivery records."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0040_github_workflow_deliveries"
down_revision: str | None = "0039_operational_verifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "github_workflow_run_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "operation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("system_operations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("github_delivery_id", sa.String(length=128), nullable=False, unique=True),
        sa.Column("workflow_run_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_github_workflow_run_deliveries_operation",
        "github_workflow_run_deliveries",
        ["operation_id"],
    )
    op.execute("ALTER TABLE github_workflow_run_deliveries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE github_workflow_run_deliveries FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_github_workflow_run_deliveries
        ON github_workflow_run_deliveries
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
        "DROP POLICY IF EXISTS tenant_isolation_github_workflow_run_deliveries "
        "ON github_workflow_run_deliveries"
    )
    op.drop_table("github_workflow_run_deliveries")
