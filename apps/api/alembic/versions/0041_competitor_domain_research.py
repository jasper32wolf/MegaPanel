"""Persist bounded competitor domain research runs and page signals."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0041_competitor_domain_research"
down_revision: str | None = "0040_github_workflow_deliveries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_RUN_STATUSES = ("queued", "running", "partial", "done", "blocked", "failed", "cancelled")
_PAGE_STATUSES = ("queued", "fetched", "skipped", "failed")


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _enable_tenant_rls(table: str) -> None:
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
        "competitor_crawl_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("root_url", sa.String(length=2048), nullable=False),
        sa.Column("origin", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column(
            "configuration",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "progress",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "coverage",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=255), nullable=True),
        sa.Column(
            "queued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            f"status IN ({_values(_RUN_STATUSES)})", name="ck_competitor_crawl_run_status"
        ),
    )
    op.create_index("ix_competitor_crawl_runs_tenant", "competitor_crawl_runs", ["tenant_id"])
    op.create_index("ix_competitor_crawl_runs_project", "competitor_crawl_runs", ["project_id"])
    op.create_index("ix_competitor_crawl_runs_status", "competitor_crawl_runs", ["status"])

    op.create_table(
        "competitor_crawl_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "crawl_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("competitor_crawl_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("canonical_url", sa.String(length=2048), nullable=True),
        sa.Column("parent_url", sa.String(length=2048), nullable=True),
        sa.Column("discovery_source", sa.String(length=16), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "signals",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("crawl_run_id", "url", name="uq_competitor_crawl_page_url"),
        sa.CheckConstraint(
            f"status IN ({_values(_PAGE_STATUSES)})", name="ck_competitor_crawl_page_status"
        ),
        sa.CheckConstraint("depth >= 0", name="ck_competitor_crawl_page_depth"),
    )
    op.create_index("ix_competitor_crawl_pages_run", "competitor_crawl_pages", ["crawl_run_id"])
    op.create_index("ix_competitor_crawl_pages_tenant", "competitor_crawl_pages", ["tenant_id"])
    op.create_index("ix_competitor_crawl_pages_project", "competitor_crawl_pages", ["project_id"])
    op.create_index("ix_competitor_crawl_pages_status", "competitor_crawl_pages", ["status"])

    _enable_tenant_rls("competitor_crawl_runs")
    _enable_tenant_rls("competitor_crawl_pages")


def downgrade() -> None:
    for table in ("competitor_crawl_pages", "competitor_crawl_runs"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
    op.drop_index("ix_competitor_crawl_pages_status", table_name="competitor_crawl_pages")
    op.drop_index("ix_competitor_crawl_pages_project", table_name="competitor_crawl_pages")
    op.drop_index("ix_competitor_crawl_pages_tenant", table_name="competitor_crawl_pages")
    op.drop_index("ix_competitor_crawl_pages_run", table_name="competitor_crawl_pages")
    op.drop_table("competitor_crawl_pages")
    op.drop_index("ix_competitor_crawl_runs_status", table_name="competitor_crawl_runs")
    op.drop_index("ix_competitor_crawl_runs_project", table_name="competitor_crawl_runs")
    op.drop_index("ix_competitor_crawl_runs_tenant", table_name="competitor_crawl_runs")
    op.drop_table("competitor_crawl_runs")
