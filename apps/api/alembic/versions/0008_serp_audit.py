"""Phase 7: SERP checks, content decay, footprint audits, staging."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_serp_audit"
down_revision: str | None = "0007_leads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "serp_checks",
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
        sa.Column("page_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("query", sa.String(512), nullable=False),
        sa.Column("engine", sa.String(32), server_default="yandex"),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("url_found", sa.String(2048), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("meta", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_serp_checks_tenant_id", "serp_checks", ["tenant_id"])
    op.create_index("ix_serp_checks_site_id", "serp_checks", ["site_id"])

    op.create_table(
        "content_decay_events",
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
        sa.Column("page_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("baseline", sa.Float(), nullable=True),
        sa.Column("current", sa.Float(), nullable=True),
        sa.Column("action", sa.String(64), server_default="evergreen"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_content_decay_events_tenant_id", "content_decay_events", ["tenant_id"])

    op.create_table(
        "footprint_audits",
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
        sa.Column("risk_score", sa.Integer(), server_default="0"),
        sa.Column("findings", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_footprint_audits_tenant_id", "footprint_audits", ["tenant_id"])

    op.create_table(
        "staging_approvals",
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
        sa.Column("build_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), server_default="pending"),
        sa.Column("reviewer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_staging_approvals_tenant_id", "staging_approvals", ["tenant_id"])

    for table in ("serp_checks", "content_decay_events", "footprint_audits", "staging_approvals"):
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
    for table in ("serp_checks", "content_decay_events", "footprint_audits", "staging_approvals"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
    op.drop_table("staging_approvals")
    op.drop_table("footprint_audits")
    op.drop_table("content_decay_events")
    op.drop_table("serp_checks")
