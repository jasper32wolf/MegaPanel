"""Add bounded HTTPS public-free Bukvarix keyword runs."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0048_bukvarix_https_public_runs"
down_revision: str | None = "0047_durable_site_build_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tenant_policy(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation_{table}
        ON {table}
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
    op.drop_constraint(
        "ck_semantic_source_run_acquisition",
        "project_semantic_source_runs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_semantic_source_run_acquisition",
        "project_semantic_source_runs",
        "acquisition IN ('manual_export', 'https_public_free')",
    )

    op.create_table(
        "project_bukvarix_keyword_runs",
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
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column(
            "provider_mode",
            sa.String(length=32),
            nullable=False,
            server_default="https_public_free",
        ),
        sa.Column("seed_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("seed_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("query_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "committed_source_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project_semantic_source_runs.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
        ),
        sa.Column("queued_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_bukvarix_keyword_run_status",
        ),
        sa.CheckConstraint(
            "provider_mode = 'https_public_free'",
            name="ck_bukvarix_keyword_run_provider_mode",
        ),
        sa.CheckConstraint("query_count >= 0 AND query_count <= 10", name="ck_bukvarix_query_count"),
        sa.CheckConstraint("result_count >= 0 AND result_count <= 1000", name="ck_bukvarix_result_count"),
    )
    op.create_index(
        "ix_bukvarix_keyword_runs_tenant_project_created",
        "project_bukvarix_keyword_runs",
        ["tenant_id", "project_id", "created_at"],
    )
    op.create_index(
        "ix_bukvarix_keyword_runs_status",
        "project_bukvarix_keyword_runs",
        ["status", "queued_at"],
    )
    _tenant_policy("project_bukvarix_keyword_runs")

    op.create_table(
        "project_bukvarix_keyword_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project_bukvarix_keyword_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
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
            "source_project_keyword_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project_keywords.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("phrase", sa.String(length=512), nullable=False),
        sa.Column("normalized", sa.String(length=512), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("run_id", "normalized", name="uq_bukvarix_keyword_run_normalized"),
        sa.CheckConstraint("char_length(phrase) BETWEEN 1 AND 512", name="ck_bukvarix_keyword_phrase"),
        sa.CheckConstraint("octet_length(metrics::text) <= 1024", name="ck_bukvarix_keyword_metrics_size"),
    )
    op.create_index(
        "ix_bukvarix_keyword_results_run",
        "project_bukvarix_keyword_results",
        ["run_id"],
    )
    op.create_index(
        "ix_bukvarix_keyword_results_tenant_project",
        "project_bukvarix_keyword_results",
        ["tenant_id", "project_id"],
    )
    _tenant_policy("project_bukvarix_keyword_results")


def downgrade() -> None:
    for table in ("project_bukvarix_keyword_results", "project_bukvarix_keyword_runs"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
    op.drop_index(
        "ix_bukvarix_keyword_results_tenant_project",
        table_name="project_bukvarix_keyword_results",
    )
    op.drop_index("ix_bukvarix_keyword_results_run", table_name="project_bukvarix_keyword_results")
    op.drop_table("project_bukvarix_keyword_results")
    op.drop_index("ix_bukvarix_keyword_runs_status", table_name="project_bukvarix_keyword_runs")
    op.drop_index(
        "ix_bukvarix_keyword_runs_tenant_project_created",
        table_name="project_bukvarix_keyword_runs",
    )
    op.drop_table("project_bukvarix_keyword_runs")
    op.drop_constraint(
        "ck_semantic_source_run_acquisition",
        "project_semantic_source_runs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_semantic_source_run_acquisition",
        "project_semantic_source_runs",
        "acquisition = 'manual_export'",
    )
