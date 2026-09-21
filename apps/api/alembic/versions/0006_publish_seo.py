"""Phase 5: pages index state, domains, redirects, bulk ops, builds."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_publish_seo"
down_revision: str | None = "0005_ai_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "site_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(512), nullable=False),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("publish_state", sa.String(32), server_default="draft"),
        sa.Column("index_state", sa.String(32), server_default="noindex"),
        sa.Column("content_chars", sa.Integer(), server_default="0"),
        sa.Column("thin", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("manifest", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("site_id", "slug", name="uq_site_pages_site_slug"),
    )
    op.create_index("ix_site_pages_tenant_id", "site_pages", ["tenant_id"])
    op.create_index("ix_site_pages_index_state", "site_pages", ["index_state"])
    op.create_index("ix_site_pages_site_id", "site_pages", ["site_id"])

    op.create_table(
        "domains",
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
            sa.ForeignKey("sites.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("hostname", sa.String(255), nullable=False, unique=True),
        sa.Column("registrar", sa.String(128), nullable=True),
        sa.Column("ssl_status", sa.String(32), server_default="pending"),
        sa.Column("dns_status", sa.String(32), server_default="unknown"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meta", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_domains_tenant_id", "domains", ["tenant_id"])

    op.create_table(
        "redirects",
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
        sa.Column("from_path", sa.String(1024), nullable=False),
        sa.Column("to_url", sa.String(2048), nullable=False),
        sa.Column("code", sa.Integer(), server_default="301"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_redirects_tenant_id", "redirects", ["tenant_id"])

    op.create_table(
        "bulk_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("op_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), server_default="pending"),
        sa.Column("payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("undo_payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_bulk_operations_tenant_id", "bulk_operations", ["tenant_id"])

    op.create_table(
        "site_builds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), server_default="pending"),
        sa.Column("build_hash", sa.String(64), nullable=True),
        sa.Column("previous_build_hash", sa.String(64), nullable=True),
        sa.Column("pages_built", sa.Integer(), server_default="0"),
        sa.Column("duration_ms", sa.Integer(), server_default="0"),
        sa.Column("log", sa.Text(), server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_site_builds_site_id", "site_builds", ["site_id"])

    op.add_column("sites", sa.Column("indexnow_key", sa.String(64), nullable=True))
    op.add_column(
        "sites", sa.Column("caddy_configured", sa.Boolean(), server_default=sa.text("false"))
    )

    for table in ("site_pages", "domains", "redirects", "bulk_operations", "site_builds"):
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
    for table in ("site_pages", "domains", "redirects", "bulk_operations", "site_builds"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
    op.drop_column("sites", "caddy_configured")
    op.drop_column("sites", "indexnow_key")
    op.drop_table("site_builds")
    op.drop_table("bulk_operations")
    op.drop_table("redirects")
    op.drop_table("domains")
    op.drop_table("site_pages")
