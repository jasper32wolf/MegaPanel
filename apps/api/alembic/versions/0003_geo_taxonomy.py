"""Phase 2: taxonomy, geo_places, morph_cache, onboarding, coverage_gaps."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_geo_taxonomy"
down_revision: Union[str, None] = "0002_rls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("is_demo", sa.Boolean(), server_default=sa.text("false")))
    op.add_column("sites", sa.Column("niche", sa.String(128), nullable=True))

    op.create_table(
        "taxonomy_categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("niche", sa.String(128), nullable=False),
        sa.Column("slug", sa.String(160), nullable=False),
        sa.Column("service", sa.String(255), nullable=False),
        sa.Column("modifier", sa.String(255), nullable=True),
        sa.Column("method", sa.String(255), nullable=True),
        sa.Column("attrs", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("templates", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_taxonomy_tenant_slug"),
    )
    op.create_index("ix_taxonomy_categories_tenant_id", "taxonomy_categories", ["tenant_id"])

    op.add_column(
        "keywords",
        sa.Column("category_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("taxonomy_categories.id", ondelete="SET NULL"), nullable=True),
    )

    op.create_table(
        "geo_places",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("name_forms", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("geo_places.id", ondelete="SET NULL"), nullable=True),
        sa.Column("external_id", sa.String(128), nullable=True),
        sa.Column("source", sa.String(32), server_default="manual"),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=True),
        sa.Column("population", sa.Integer(), nullable=True),
        sa.Column("attrs", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_validated", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("kind", "external_id", name="uq_geo_kind_ext"),
    )
    op.create_index("ix_geo_places_kind", "geo_places", ["kind"])
    op.create_index("ix_geo_places_name", "geo_places", ["name"])
    op.create_index("ix_geo_places_parent_id", "geo_places", ["parent_id"])

    op.create_table(
        "coverage_gaps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("geo_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("meta", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_coverage_gaps_tenant_id", "coverage_gaps", ["tenant_id"])

    op.create_table(
        "morph_cache",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("word_hash", sa.String(64), nullable=False),
        sa.Column("lemma", sa.String(255), nullable=False),
        sa.Column("forms", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("word_hash", name="uq_morph_word_hash"),
    )

    op.create_table(
        "onboarding_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("step", sa.String(64), server_default="niche"),
        sa.Column("payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("completed", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_onboarding_sessions_tenant_id", "onboarding_sessions", ["tenant_id"])

    # RLS for new tenant tables
    for table in ("taxonomy_categories", "coverage_gaps", "onboarding_sessions"):
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


def downgrade() -> None:
    for table in ("taxonomy_categories", "coverage_gaps", "onboarding_sessions"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
    op.drop_table("onboarding_sessions")
    op.drop_table("morph_cache")
    op.drop_table("coverage_gaps")
    op.drop_table("geo_places")
    op.drop_column("keywords", "category_id")
    op.drop_table("taxonomy_categories")
    op.drop_column("sites", "niche")
    op.drop_column("tenants", "is_demo")
