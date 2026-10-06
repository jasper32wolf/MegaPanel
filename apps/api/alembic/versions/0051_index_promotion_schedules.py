"""Add reviewed, hash-bound schedules for gradual index promotion."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0051_index_promotion_schedules"
down_revision: str | None = "0050_candidate_build_scheduling"
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
        "index_promotion_schedules",
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
        sa.Column("state", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interval_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("batch_size", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "state IN ('draft', 'review', 'active', 'paused', 'completed', 'cancelled')",
            name="ck_index_promotion_schedule_state",
        ),
        sa.CheckConstraint(
            "interval_hours >= 1 AND interval_hours <= 720",
            name="ck_index_promotion_schedule_interval",
        ),
        sa.CheckConstraint(
            "batch_size >= 1 AND batch_size <= 20",
            name="ck_index_promotion_schedule_batch_size",
        ),
    )
    op.create_index(
        "ix_index_promotion_schedules_due",
        "index_promotion_schedules",
        ["tenant_id", "site_id", "state", "starts_at"],
    )
    _tenant_policy("index_promotion_schedules")

    op.create_table(
        "index_promotion_schedule_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "schedule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("index_promotion_schedules.id", ondelete="CASCADE"),
            nullable=False,
        ),
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
        sa.Column("batch_number", sa.Integer(), nullable=False),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="planned"),
        sa.Column(
            "promotion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("page_index_promotions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "candidate_build_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("site_builds.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stale_reason", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("schedule_id", "slug", name="uq_index_schedule_item_slug"),
        sa.CheckConstraint("batch_number >= 1", name="ck_index_schedule_item_batch"),
        sa.CheckConstraint(
            "state IN ('planned', 'prepared', 'stale', 'cancelled')",
            name="ck_index_schedule_item_state",
        ),
        sa.CheckConstraint(
            "source_hash ~ '^[0-9a-f]{64}$' AND qa_source_hash ~ '^[0-9a-f]{64}$'",
            name="ck_index_schedule_item_hashes",
        ),
    )
    op.create_index(
        "ix_index_schedule_items_due",
        "index_promotion_schedule_items",
        ["tenant_id", "site_id", "state", "planned_at"],
    )
    op.create_index(
        "ix_index_schedule_items_schedule_batch",
        "index_promotion_schedule_items",
        ["schedule_id", "batch_number"],
    )
    _tenant_policy("index_promotion_schedule_items")


def downgrade() -> None:
    for table in ("index_promotion_schedule_items", "index_promotion_schedules"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
    op.drop_index(
        "ix_index_schedule_items_schedule_batch",
        table_name="index_promotion_schedule_items",
    )
    op.drop_index(
        "ix_index_schedule_items_due",
        table_name="index_promotion_schedule_items",
    )
    op.drop_table("index_promotion_schedule_items")
    op.drop_index("ix_index_promotion_schedules_due", table_name="index_promotion_schedules")
    op.drop_table("index_promotion_schedules")
