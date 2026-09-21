"""Phase 8 panel product surface: search, notifications, white-label, plugins, reports."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_panel_ux"
down_revision: str | None = "0008_serp_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("priority", sa.String(16), server_default="normal"),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), server_default=""),
        sa.Column("group_key", sa.String(128), nullable=True),
        sa.Column("read", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_notifications_tenant_id", "notifications", ["tenant_id"])

    op.create_table(
        "saved_views",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("entity", sa.String(64), nullable=False),
        sa.Column("filters", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb")),
        sa.Column("revoked", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_api_keys_tenant_id", "api_keys", ["tenant_id"])

    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("events", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb")),
        sa.Column("secret", sa.String(128), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "plugins",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("hooks", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb")),
        sa.Column("config", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    for table in ("notifications", "saved_views", "api_keys", "webhook_subscriptions"):
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
    for table in ("notifications", "saved_views", "api_keys", "webhook_subscriptions"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
    op.drop_table("plugins")
    op.drop_table("webhook_subscriptions")
    op.drop_table("api_keys")
    op.drop_table("saved_views")
    op.drop_table("notifications")
