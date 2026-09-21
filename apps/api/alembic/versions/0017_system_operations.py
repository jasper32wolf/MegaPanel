"""Add audited GitHub-controlled system operation records."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_system_operations"
down_revision: str | None = "0016_project_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("release_sha", sa.String(64), nullable=True),
        sa.Column("snapshot_id", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(36), nullable=False, unique=True),
        sa.Column("workflow", sa.String(128), nullable=False),
        sa.Column("workflow_run_id", sa.BigInteger(), nullable=True),
        sa.Column("workflow_url", sa.String(2048), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="requested"),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column(
            "details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_system_operations_tenant_created", "system_operations", ["tenant_id", "created_at"]
    )
    op.create_index("ix_system_operations_status", "system_operations", ["status"])
    op.create_index(
        "uq_system_operations_one_active_per_tenant",
        "system_operations",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('requested', 'queued', 'in_progress')"),
    )
    op.execute("ALTER TABLE system_operations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE system_operations FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_system_operations ON system_operations
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
    op.execute("DROP POLICY IF EXISTS tenant_isolation_system_operations ON system_operations")
    op.drop_table("system_operations")
