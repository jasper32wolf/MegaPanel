"""Link approved architecture AI runs to one draft site structure revision."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045_site_structure_ai_run_imports"
down_revision: str | None = "0044_project_city_family"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "site_structure_ai_run_imports",
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
            "ai_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "site_structure_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("site_structure_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("input_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("ai_run_id", name="uq_site_structure_ai_import_run"),
    )
    op.create_index(
        "ix_site_structure_ai_run_imports_tenant",
        "site_structure_ai_run_imports",
        ["tenant_id"],
    )
    op.create_index(
        "ix_site_structure_ai_run_imports_project",
        "site_structure_ai_run_imports",
        ["project_id"],
    )
    op.create_index(
        "ix_site_structure_ai_run_imports_revision",
        "site_structure_ai_run_imports",
        ["site_structure_revision_id"],
    )
    op.execute("ALTER TABLE site_structure_ai_run_imports ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE site_structure_ai_run_imports FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_site_structure_ai_run_imports
        ON site_structure_ai_run_imports
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
        "DROP POLICY IF EXISTS tenant_isolation_site_structure_ai_run_imports "
        "ON site_structure_ai_run_imports"
    )
    op.drop_index(
        "ix_site_structure_ai_run_imports_revision",
        table_name="site_structure_ai_run_imports",
    )
    op.drop_index(
        "ix_site_structure_ai_run_imports_project",
        table_name="site_structure_ai_run_imports",
    )
    op.drop_index(
        "ix_site_structure_ai_run_imports_tenant",
        table_name="site_structure_ai_run_imports",
    )
    op.drop_table("site_structure_ai_run_imports")
