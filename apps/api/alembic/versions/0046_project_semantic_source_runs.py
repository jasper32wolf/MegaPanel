"""Record manually acquired semantic-source provenance without provider egress."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046_project_semantic_source_runs"
down_revision: str | None = "0045_site_structure_ai_run_imports"
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
    op.create_table(
        "project_semantic_source_runs",
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
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("acquisition", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("source_label", sa.String(length=255), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("provider = 'bukvarix'", name="ck_semantic_source_run_provider"),
        sa.CheckConstraint(
            "acquisition = 'manual_export'",
            name="ck_semantic_source_run_acquisition",
        ),
        sa.CheckConstraint(
            "mode IN ('domain', 'compare', 'multi_domain')",
            name="ck_semantic_source_run_mode",
        ),
    )
    op.create_index(
        "ix_project_semantic_source_runs_tenant",
        "project_semantic_source_runs",
        ["tenant_id"],
    )
    op.create_index(
        "ix_project_semantic_source_runs_project",
        "project_semantic_source_runs",
        ["project_id"],
    )
    _tenant_policy("project_semantic_source_runs")

    op.create_table(
        "project_semantic_source_run_keywords",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project_semantic_source_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_keyword_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project_keywords.id", ondelete="RESTRICT"),
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
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "source_run_id",
            "project_keyword_id",
            name="uq_semantic_source_run_project_keyword",
        ),
    )
    op.create_index(
        "ix_project_semantic_source_run_keywords_source",
        "project_semantic_source_run_keywords",
        ["source_run_id"],
    )
    op.create_index(
        "ix_project_semantic_source_run_keywords_tenant",
        "project_semantic_source_run_keywords",
        ["tenant_id"],
    )
    op.create_index(
        "ix_project_semantic_source_run_keywords_project",
        "project_semantic_source_run_keywords",
        ["project_id"],
    )
    _tenant_policy("project_semantic_source_run_keywords")


def downgrade() -> None:
    for table in ("project_semantic_source_run_keywords", "project_semantic_source_runs"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
    op.drop_index(
        "ix_project_semantic_source_run_keywords_project",
        table_name="project_semantic_source_run_keywords",
    )
    op.drop_index(
        "ix_project_semantic_source_run_keywords_tenant",
        table_name="project_semantic_source_run_keywords",
    )
    op.drop_index(
        "ix_project_semantic_source_run_keywords_source",
        table_name="project_semantic_source_run_keywords",
    )
    op.drop_table("project_semantic_source_run_keywords")
    op.drop_index(
        "ix_project_semantic_source_runs_project",
        table_name="project_semantic_source_runs",
    )
    op.drop_index(
        "ix_project_semantic_source_runs_tenant",
        table_name="project_semantic_source_runs",
    )
    op.drop_table("project_semantic_source_runs")
