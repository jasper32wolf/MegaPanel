"""Phase 3: competitor skeletons, blocks, media assets, knowledge hub."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_blocks_media"
down_revision: Union[str, None] = "0003_geo_taxonomy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "content_blocks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("hash_class", sa.String(64), nullable=False),
        sa.Column("html", sa.Text(), nullable=False),
        sa.Column("css", sa.Text(), server_default=""),
        sa.Column("props", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_content_blocks_tenant_id", "content_blocks", ["tenant_id"])
    op.create_index("ix_content_blocks_type", "content_blocks", ["type"])

    op.create_table(
        "media_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("source", sa.String(255), nullable=True),
        sa.Column("license", sa.String(128), nullable=True),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("phash", sa.String(64), nullable=True),
        sa.Column("normalized", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("tags", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb")),
        sa.Column("meta", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_media_assets_tenant_id", "media_assets", ["tenant_id"])
    op.create_index("ix_media_assets_phash", "media_assets", ["phash"])

    op.create_table(
        "competitor_scans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seed_url", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(32), server_default="pending"),
        sa.Column("urls", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb")),
        sa.Column("extracted", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("skeleton", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_competitor_scans_tenant_id", "competitor_scans", ["tenant_id"])

    op.create_table(
        "knowledge_docs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(64), server_default="skeleton"),
        sa.Column("content", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_scan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_knowledge_docs_tenant_id", "knowledge_docs", ["tenant_id"])

    for table in ("content_blocks", "media_assets", "competitor_scans", "knowledge_docs"):
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
    for table in ("content_blocks", "media_assets", "competitor_scans", "knowledge_docs"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
    op.drop_table("knowledge_docs")
    op.drop_table("competitor_scans")
    op.drop_table("media_assets")
    op.drop_table("content_blocks")
