"""Persist explicit, hash-bound page index promotions."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035_page_index_promotions"
down_revision: str | None = "0034_build_release_gates"
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
    op.add_column("site_pages", sa.Column("index_source_hash", sa.String(length=64), nullable=True))
    op.create_table(
        "page_index_promotions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "page_draft_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("page_drafts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(length=512), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("qa_source_hash", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_page_index_promotions_tenant_id", "page_index_promotions", ["tenant_id"])
    op.create_index("ix_page_index_promotions_site_id", "page_index_promotions", ["site_id"])
    op.create_index("ix_page_index_promotions_project_id", "page_index_promotions", ["project_id"])
    op.create_index(
        "ix_page_index_promotions_page_draft_id", "page_index_promotions", ["page_draft_id"]
    )
    _enable_rls("page_index_promotions")


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_page_index_promotions ON page_index_promotions"
    )
    op.drop_table("page_index_promotions")
    op.drop_column("site_pages", "index_source_hash")
