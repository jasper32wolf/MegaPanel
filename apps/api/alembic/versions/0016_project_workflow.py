"""Add project workflow entities and provenance fields."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_project_workflow"
down_revision: str | None = "0015_webhook_delivery"
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
        "projects",
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
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(160), nullable=False),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("locale", sa.String(10), nullable=False, server_default="ru"),
        sa.Column("niche", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("current_fact_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "domain_check_meta",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_projects_tenant_slug"),
        sa.UniqueConstraint("site_id", name="uq_projects_site_id"),
    )
    op.create_index("ix_projects_tenant_id", "projects", ["tenant_id"])
    op.create_index("ix_projects_status", "projects", ["status"])

    op.create_table(
        "project_fact_revisions",
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
        sa.Column(
            "supersedes_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project_fact_revisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="draft"),
        sa.Column(
            "facts", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("source_notes", sa.Text(), nullable=True),
        sa.Column("facts_hash", sa.String(64), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confirmed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("project_id", "version", name="uq_project_fact_revision"),
    )
    op.create_index("ix_project_fact_revisions_tenant_id", "project_fact_revisions", ["tenant_id"])
    op.create_index(
        "ix_project_fact_revisions_project_id", "project_fact_revisions", ["project_id"]
    )
    op.create_index("ix_project_fact_revisions_state", "project_fact_revisions", ["state"])
    op.create_foreign_key(
        "fk_projects_current_fact_revision",
        "projects",
        "project_fact_revisions",
        ["current_fact_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "project_keywords",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "keyword_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("keywords.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cluster", sa.String(255), nullable=True),
        sa.Column("intent", sa.String(128), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("project_id", "keyword_id", name="uq_project_keyword"),
    )
    for column in ("tenant_id", "project_id", "keyword_id", "cluster", "intent"):
        op.create_index(f"ix_project_keywords_{column}", "project_keywords", [column])

    op.create_table(
        "project_geo_places",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "geo_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("geo_places.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(32), nullable=False, server_default="service_area"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "morph_overrides",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("project_id", "geo_id", name="uq_project_geo_place"),
    )
    for column in ("tenant_id", "project_id", "geo_id"):
        op.create_index(f"ix_project_geo_places_{column}", "project_geo_places", [column])

    op.create_table(
        "page_plans",
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
        sa.Column(
            "supersedes_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("page_plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("slug", sa.String(512), nullable=False),
        sa.Column("objective", sa.String(512), nullable=False),
        sa.Column("intent", sa.String(128), nullable=True),
        sa.Column("risk_notes", sa.Text(), nullable=True),
        sa.Column("kit_key", sa.String(128), nullable=False),
        sa.Column(
            "block_selection",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "fact_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project_fact_revisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "keyword_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "geo_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "source_refs", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("state", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint(
            "project_id", "slug", "version", name="uq_page_plan_project_slug_version"
        ),
    )
    for column in ("tenant_id", "project_id", "state"):
        op.create_index(f"ix_page_plans_{column}", "page_plans", [column])

    op.create_table(
        "page_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "page_plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("page_plans.id", ondelete="CASCADE"),
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
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="queued"),
        sa.Column(
            "input_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "page_manifest",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "generator_meta",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column(
            "qa_runs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("last_qa_verdict", sa.String(16), nullable=True),
        sa.Column(
            "qa_override", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("applied_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("failure_message", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("page_plan_id", "revision", name="uq_page_draft_plan_revision"),
    )
    for column in ("tenant_id", "project_id", "page_plan_id", "state", "last_qa_verdict"):
        op.create_index(f"ix_page_drafts_{column}", "page_drafts", [column])

    op.add_column("sites", sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_sites_project_id", "sites", "projects", ["project_id"], ["id"], ondelete="SET NULL"
    )
    op.create_unique_constraint("uq_sites_project_id", "sites", ["project_id"])
    op.create_index("ix_sites_project_id", "sites", ["project_id"])

    op.add_column(
        "site_pages", sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "site_pages", sa.Column("page_plan_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "site_pages", sa.Column("page_draft_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_site_pages_project_id",
        "site_pages",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_site_pages_page_plan_id",
        "site_pages",
        "page_plans",
        ["page_plan_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_site_pages_page_draft_id",
        "site_pages",
        "page_drafts",
        ["page_draft_id"],
        ["id"],
        ondelete="SET NULL",
    )
    for column in ("project_id", "page_plan_id", "page_draft_id"):
        op.create_index(f"ix_site_pages_{column}", "site_pages", [column])

    op.add_column(
        "site_builds", sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("site_builds", sa.Column("manifest_snapshot", postgresql.JSONB(), nullable=True))
    op.add_column("site_builds", sa.Column("page_plan_ids", postgresql.JSONB(), nullable=True))
    op.add_column(
        "site_builds", sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "site_builds", sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_site_builds_project_id",
        "site_builds",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_site_builds_project_id", "site_builds", ["project_id"])

    op.create_table(
        "lead_outcomes",
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
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "page_plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("page_plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    for column in ("tenant_id", "project_id", "site_id", "lead_id", "outcome"):
        op.create_index(f"ix_lead_outcomes_{column}", "lead_outcomes", [column])

    for table in (
        "projects",
        "project_fact_revisions",
        "project_keywords",
        "project_geo_places",
        "page_plans",
        "page_drafts",
        "lead_outcomes",
    ):
        _enable_rls(table)


def downgrade() -> None:
    for table in (
        "lead_outcomes",
        "page_drafts",
        "page_plans",
        "project_geo_places",
        "project_keywords",
        "project_fact_revisions",
        "projects",
    ):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")

    op.drop_table("lead_outcomes")
    op.drop_constraint("fk_site_builds_project_id", "site_builds", type_="foreignkey")
    op.drop_index("ix_site_builds_project_id", table_name="site_builds")
    for column in (
        "activated_at",
        "requested_by",
        "page_plan_ids",
        "manifest_snapshot",
        "project_id",
    ):
        op.drop_column("site_builds", column)
    for name, column in (
        ("fk_site_pages_page_draft_id", "page_draft_id"),
        ("fk_site_pages_page_plan_id", "page_plan_id"),
        ("fk_site_pages_project_id", "project_id"),
    ):
        op.drop_constraint(name, "site_pages", type_="foreignkey")
        op.drop_index(f"ix_site_pages_{column}", table_name="site_pages")
        op.drop_column("site_pages", column)
    op.drop_constraint("uq_sites_project_id", "sites", type_="unique")
    op.drop_constraint("fk_sites_project_id", "sites", type_="foreignkey")
    op.drop_index("ix_sites_project_id", table_name="sites")
    op.drop_column("sites", "project_id")
    op.drop_table("page_drafts")
    op.drop_table("page_plans")
    op.drop_table("project_geo_places")
    op.drop_table("project_keywords")
    op.drop_constraint("fk_projects_current_fact_revision", "projects", type_="foreignkey")
    op.drop_table("project_fact_revisions")
    op.drop_table("projects")
