"""Add reviewed public author revisions for EEAT service-page provenance."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0053_author_profile_revisions"
down_revision: str | None = "0052_secure_first_party_telemetry"
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
        "author_profile_revisions",
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
            sa.ForeignKey("author_profile_revisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "portrait_asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("media_assets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=255), nullable=False),
        sa.Column("biography", sa.Text(), nullable=False),
        sa.Column("expertise", postgresql.JSONB(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("profile_hash", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="draft"),
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
        sa.UniqueConstraint("project_id", "slug", "version", name="uq_author_profile_revision"),
        sa.CheckConstraint(
            "state IN ('draft', 'review', 'approved', 'rejected')",
            name="ck_author_profile_revision_state",
        ),
        sa.CheckConstraint(
            "profile_hash ~ '^[0-9a-f]{64}$'",
            name="ck_author_profile_revision_hash",
        ),
        sa.CheckConstraint(
            "octet_length(biography) <= 16384",
            name="ck_author_profile_revision_biography_size",
        ),
    )
    op.create_index(
        "ix_author_profile_revisions_tenant_project",
        "author_profile_revisions",
        ["tenant_id", "project_id", "created_at"],
    )
    op.create_index(
        "ix_author_profile_revisions_state",
        "author_profile_revisions",
        ["tenant_id", "state"],
    )
    _tenant_policy("author_profile_revisions")


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_author_profile_revisions "
        "ON author_profile_revisions"
    )
    op.drop_index("ix_author_profile_revisions_state", table_name="author_profile_revisions")
    op.drop_index(
        "ix_author_profile_revisions_tenant_project",
        table_name="author_profile_revisions",
    )
    op.drop_table("author_profile_revisions")
