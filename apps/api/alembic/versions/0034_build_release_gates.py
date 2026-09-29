"""Persist immutable candidate release gate evidence."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034_build_release_gates"
down_revision: str | None = "0033_prompt_evaluation_runs"
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
        "build_release_gates",
        sa.Column(
            "build_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("site_builds.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="block"),
        sa.Column("ruleset_version", sa.String(length=32), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "blockers", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "warnings", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("evaluated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_build_release_gates_tenant_id", "build_release_gates", ["tenant_id"])
    op.create_index("ix_build_release_gates_status", "build_release_gates", ["status"])
    _enable_rls("build_release_gates")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_build_release_gates ON build_release_gates")
    op.drop_table("build_release_gates")
