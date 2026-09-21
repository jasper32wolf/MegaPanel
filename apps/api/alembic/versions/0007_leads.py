"""Phase 6: leads, consents, analytics events."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_leads"
down_revision: str | None = "0006_publish_seo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "leads",
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
        sa.Column("page_slug", sa.String(512), nullable=True),
        sa.Column("status", sa.String(32), server_default="new"),
        sa.Column("phone_enc", sa.Text(), nullable=True),
        sa.Column("phone_blind", sa.String(64), nullable=True),
        sa.Column("email_enc", sa.Text(), nullable=True),
        sa.Column("name_enc", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("utm", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("meta", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("qualification", sa.String(32), nullable=True),
        sa.Column("crm_status", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_leads_tenant_idem"),
    )
    op.create_index("ix_leads_tenant_id", "leads", ["tenant_id"])
    op.create_index("ix_leads_site_id", "leads", ["site_id"])
    op.create_index("ix_leads_phone_blind", "leads", ["phone_blind"])

    op.create_table(
        "consents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("visitor_id", sa.String(64), nullable=False),
        sa.Column("purposes", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("gpc", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_consents_tenant_id", "consents", ["tenant_id"])

    op.create_table(
        "analytics_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("path", sa.String(1024), nullable=True),
        sa.Column("payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_analytics_events_tenant_id", "analytics_events", ["tenant_id"])

    for table in ("leads", "consents"):
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
    for table in ("leads", "consents"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
    op.drop_table("analytics_events")
    op.drop_table("consents")
    op.drop_table("leads")
