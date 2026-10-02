"""Add immutable approved project site structure revisions."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043_site_structure_revisions"
down_revision: str | None = "0042_competitor_crawl_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "site_structure_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "supersedes_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("site_structure_revisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column(
            "semantic_collection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project_semantic_collections.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "evidence_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "structure",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("structure_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "source_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("source_snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("materialized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "materialized_page_plan_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("project_id", "version", name="uq_site_structure_project_version"),
        sa.CheckConstraint(
            "state IN ('draft', 'review', 'approved', 'rejected')",
            name="ck_site_structure_revision_state",
        ),
    )
    op.create_index(
        "ix_site_structure_revisions_project", "site_structure_revisions", ["project_id"]
    )
    op.create_index("ix_site_structure_revisions_tenant", "site_structure_revisions", ["tenant_id"])
    op.create_index("ix_site_structure_revisions_state", "site_structure_revisions", ["state"])
    op.execute("ALTER TABLE site_structure_revisions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE site_structure_revisions FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_site_structure_revisions
        ON site_structure_revisions
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
    op.execute(
        """
        CREATE FUNCTION prevent_approved_site_structure_mutation() RETURNS trigger AS $$
        BEGIN
          IF OLD.state = 'approved' AND (
            NEW.project_id IS DISTINCT FROM OLD.project_id
            OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
            OR NEW.supersedes_id IS DISTINCT FROM OLD.supersedes_id
            OR NEW.version IS DISTINCT FROM OLD.version
            OR NEW.state IS DISTINCT FROM OLD.state
            OR NEW.semantic_collection_id IS DISTINCT FROM OLD.semantic_collection_id
            OR NEW.evidence_ids IS DISTINCT FROM OLD.evidence_ids
            OR NEW.structure IS DISTINCT FROM OLD.structure
            OR NEW.structure_hash IS DISTINCT FROM OLD.structure_hash
            OR NEW.source_snapshot IS DISTINCT FROM OLD.source_snapshot
            OR NEW.source_snapshot_hash IS DISTINCT FROM OLD.source_snapshot_hash
            OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at
            OR NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at
            OR NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by
            OR NEW.decision_reason IS DISTINCT FROM OLD.decision_reason
          ) THEN
            RAISE EXCEPTION 'approved site structure revision is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER site_structure_revisions_approved_immutable
        BEFORE UPDATE OR DELETE ON site_structure_revisions
        FOR EACH ROW EXECUTE FUNCTION prevent_approved_site_structure_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS site_structure_revisions_approved_immutable "
        "ON site_structure_revisions"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_approved_site_structure_mutation()")
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_site_structure_revisions "
        "ON site_structure_revisions"
    )
    op.drop_index("ix_site_structure_revisions_state", table_name="site_structure_revisions")
    op.drop_index("ix_site_structure_revisions_tenant", table_name="site_structure_revisions")
    op.drop_index("ix_site_structure_revisions_project", table_name="site_structure_revisions")
    op.drop_table("site_structure_revisions")
