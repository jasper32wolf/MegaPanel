"""Persist offline prompt evaluation runs."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033_prompt_evaluation_runs"
down_revision: str | None = "0032_lead_routing_policy"
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
        "prompt_evaluation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "prompt_entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("prompt_registry.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("prompt_key", sa.String(length=128), nullable=False),
        sa.Column("baseline_hash", sa.String(length=64), nullable=False),
        sa.Column("effective_prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("fixture_hash", sa.String(length=64), nullable=False),
        sa.Column("ruleset_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="passed"),
        sa.Column("case_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(length=512), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint(
            "tenant_id",
            "prompt_entry_id",
            "effective_prompt_hash",
            "fixture_hash",
            name="uq_prompt_evaluation_run_identity",
        ),
    )
    op.create_index("ix_prompt_evaluation_runs_tenant_id", "prompt_evaluation_runs", ["tenant_id"])
    op.create_index(
        "ix_prompt_evaluation_runs_prompt_entry_id", "prompt_evaluation_runs", ["prompt_entry_id"]
    )
    op.create_index(
        "ix_prompt_evaluation_runs_prompt_key", "prompt_evaluation_runs", ["prompt_key"]
    )
    op.create_index("ix_prompt_evaluation_runs_status", "prompt_evaluation_runs", ["status"])

    op.create_table(
        "prompt_evaluation_case_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "evaluation_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("prompt_evaluation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "assertion_keys",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("diagnostic", sa.String(length=512), nullable=True),
        sa.UniqueConstraint("evaluation_run_id", "name", name="uq_prompt_evaluation_case_name"),
    )
    op.create_index(
        "ix_prompt_evaluation_case_results_evaluation_run_id",
        "prompt_evaluation_case_results",
        ["evaluation_run_id"],
    )
    op.create_index(
        "ix_prompt_evaluation_case_results_tenant_id",
        "prompt_evaluation_case_results",
        ["tenant_id"],
    )

    for table in ("prompt_evaluation_runs", "prompt_evaluation_case_results"):
        _enable_rls(table)


def downgrade() -> None:
    for table in ("prompt_evaluation_case_results", "prompt_evaluation_runs"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
    op.drop_table("prompt_evaluation_case_results")
    op.drop_table("prompt_evaluation_runs")
