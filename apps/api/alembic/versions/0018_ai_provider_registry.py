"""Add VPS-wide AI provider connections and auditable AI runs."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_ai_provider_registry"
down_revision: str | None = "0017_system_operations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enable_tenant_rls(table: str) -> None:
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
        "ai_provider_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("base_url", sa.String(2048), nullable=True),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("credential_last4", sa.String(4), nullable=True),
        sa.Column(
            "model_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_ai_provider_connections_provider_id", "ai_provider_connections", ["provider_id"]
    )

    op.create_table(
        "ai_runs",
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
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending_approval"),
        sa.Column("provider_id", sa.String(64), nullable=True),
        sa.Column("model_id", sa.String(256), nullable=True),
        sa.Column("prompt_id", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("prompt_hash", sa.String(64), nullable=False),
        sa.Column("input_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column(
            "input_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "output", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "usage", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("operator_decision", sa.String(32), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_ai_runs_tenant_created", "ai_runs", ["tenant_id", "created_at"])
    op.create_index("ix_ai_runs_project_id", "ai_runs", ["project_id"])
    _enable_tenant_rls("ai_runs")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_ai_runs ON ai_runs")
    op.drop_table("ai_runs")
    op.drop_index("ix_ai_provider_connections_provider_id", table_name="ai_provider_connections")
    op.drop_table("ai_provider_connections")
