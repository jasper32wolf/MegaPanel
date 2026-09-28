"""Add reviewed project semantic collections and frozen page targets."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_project_semantic_collections"
down_revision: str | None = "0025_project_competitor_evidence"
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
        "project_semantic_collections",
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
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "source_refs", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("project_id", "name", "version", name="uq_project_semantic_collection"),
    )
    op.create_index(
        "ix_project_semantic_collections_project_id", "project_semantic_collections", ["project_id"]
    )
    op.create_index(
        "ix_project_semantic_collections_tenant_id", "project_semantic_collections", ["tenant_id"]
    )
    op.create_index(
        "ix_project_semantic_collections_state", "project_semantic_collections", ["state"]
    )

    op.create_table(
        "project_semantic_collection_keywords",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "collection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project_semantic_collections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_keyword_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project_keywords.id", ondelete="RESTRICT"),
            nullable=False,
        ),
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
        sa.Column("cluster", sa.String(length=255), nullable=True),
        sa.Column("intent", sa.String(length=128), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint(
            "collection_id", "project_keyword_id", name="uq_collection_project_keyword"
        ),
    )
    op.create_index(
        "ix_project_semantic_collection_keywords_collection_id",
        "project_semantic_collection_keywords",
        ["collection_id"],
    )
    op.create_index(
        "ix_project_semantic_collection_keywords_project_id",
        "project_semantic_collection_keywords",
        ["project_id"],
    )
    op.create_index(
        "ix_project_semantic_collection_keywords_tenant_id",
        "project_semantic_collection_keywords",
        ["tenant_id"],
    )

    op.create_table(
        "project_semantic_keyword_geo_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "collection_keyword_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project_semantic_collection_keywords.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_geo_place_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project_geo_places.id", ondelete="RESTRICT"),
            nullable=False,
        ),
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
        sa.Column("scope", sa.String(length=32), nullable=False, server_default="service_area"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint(
            "collection_keyword_id",
            "project_geo_place_id",
            name="uq_collection_keyword_project_geo",
        ),
    )
    op.create_index(
        "ix_project_semantic_keyword_geo_bindings_collection_keyword_id",
        "project_semantic_keyword_geo_bindings",
        ["collection_keyword_id"],
    )
    op.create_index(
        "ix_project_semantic_keyword_geo_bindings_project_id",
        "project_semantic_keyword_geo_bindings",
        ["project_id"],
    )
    op.create_index(
        "ix_project_semantic_keyword_geo_bindings_tenant_id",
        "project_semantic_keyword_geo_bindings",
        ["tenant_id"],
    )

    op.add_column(
        "page_plans",
        sa.Column(
            "semantic_target_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    for table in (
        "project_semantic_collections",
        "project_semantic_collection_keywords",
        "project_semantic_keyword_geo_bindings",
    ):
        _enable_rls(table)


def downgrade() -> None:
    for table in (
        "project_semantic_keyword_geo_bindings",
        "project_semantic_collection_keywords",
        "project_semantic_collections",
    ):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_column("page_plans", "semantic_target_snapshot")
    op.drop_table("project_semantic_keyword_geo_bindings")
    op.drop_table("project_semantic_collection_keywords")
    op.drop_table("project_semantic_collections")
