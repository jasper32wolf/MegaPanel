"""Add reviewed design-profile revisions and effective assignments."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0049_design_profile_revisions"
down_revision: str | None = "0048_bukvarix_https_public_runs"
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
        "design_profile_revisions",
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
            "supersedes_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("design_profile_revisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("scope", sa.String(length=16), nullable=False, server_default="project"),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("profile", postgresql.JSONB(), nullable=False),
        sa.Column("profile_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "project_id",
            "scope",
            "version",
            name="uq_design_profile_revision",
        ),
        sa.CheckConstraint(
            "scope IN ('family', 'project')",
            name="ck_design_profile_revision_scope",
        ),
        sa.CheckConstraint(
            "state IN ('draft', 'review', 'approved', 'rejected')",
            name="ck_design_profile_revision_state",
        ),
        sa.CheckConstraint(
            "profile_hash ~ '^[0-9a-f]{64}$'",
            name="ck_design_profile_revision_hash",
        ),
        sa.CheckConstraint(
            "octet_length(profile::text) <= 32768",
            name="ck_design_profile_revision_profile_size",
        ),
    )
    op.create_index(
        "ix_design_profile_revisions_tenant_project_scope",
        "design_profile_revisions",
        ["tenant_id", "project_id", "scope", "created_at"],
    )
    op.create_index(
        "ix_design_profile_revisions_state",
        "design_profile_revisions",
        ["state", "tenant_id"],
    )
    _tenant_policy("design_profile_revisions")

    op.create_table(
        "design_profile_assignments",
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
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column(
            "profile_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("design_profile_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "project_id",
            "scope",
            name="uq_design_profile_assignment_scope",
        ),
        sa.UniqueConstraint(
            "profile_revision_id",
            name="uq_design_profile_assignment_revision",
        ),
        sa.CheckConstraint(
            "scope IN ('family', 'project')",
            name="ck_design_profile_assignment_scope",
        ),
    )
    op.create_index(
        "ix_design_profile_assignments_tenant_project",
        "design_profile_assignments",
        ["tenant_id", "project_id"],
    )
    _tenant_policy("design_profile_assignments")


def downgrade() -> None:
    for table in ("design_profile_assignments", "design_profile_revisions"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
    op.drop_index(
        "ix_design_profile_assignments_tenant_project",
        table_name="design_profile_assignments",
    )
    op.drop_table("design_profile_assignments")
    op.drop_index("ix_design_profile_revisions_state", table_name="design_profile_revisions")
    op.drop_index(
        "ix_design_profile_revisions_tenant_project_scope",
        table_name="design_profile_revisions",
    )
    op.drop_table("design_profile_revisions")
