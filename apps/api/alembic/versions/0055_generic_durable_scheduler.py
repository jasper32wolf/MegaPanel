"""Add PostgreSQL-authoritative generic scheduler records."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0055_generic_durable_scheduler"
down_revision: str | None = "0054_page_draft_author_binding"
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
        "scheduler_jobs",
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
        ),
        sa.Column(
            "site_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sites.id", ondelete="CASCADE")
        ),
        sa.Column("work_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_hash", sa.String(length=64)),
        sa.Column("source_version", sa.Integer()),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("not_before", sa.DateTime(timezone=True)),
        sa.Column(
            "eligible_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "first_eligible_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_id", postgresql.UUID(as_uuid=True)),
        sa.Column("leased_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("paused_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.Column("failure_code", sa.String(length=64)),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True)),
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
        sa.UniqueConstraint("work_type", "source_id", name="uq_scheduler_job_source"),
        sa.CheckConstraint("work_type IN ('site_build')", name="ck_scheduler_job_type"),
        sa.CheckConstraint(
            "state IN ('queued', 'leased', 'running', 'paused', 'cancel_requested', "
            "'succeeded', 'failed', 'cancelled')",
            name="ck_scheduler_job_state",
        ),
        sa.CheckConstraint("priority BETWEEN 0 AND 100", name="ck_scheduler_job_priority"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_scheduler_job_attempts"),
        sa.CheckConstraint(
            "source_hash IS NULL OR source_hash ~ '^[0-9a-f]{64}$'", name="ck_scheduler_job_hash"
        ),
    )
    op.create_index(
        "ix_scheduler_jobs_due",
        "scheduler_jobs",
        ["state", "eligible_at", "not_before", "priority", "queued_at"],
    )
    op.create_index("ix_scheduler_jobs_lease", "scheduler_jobs", ["state", "lease_expires_at"])
    op.create_index(
        "ix_scheduler_jobs_tenant_project", "scheduler_jobs", ["tenant_id", "project_id", "state"]
    )
    op.create_index(
        "uq_scheduler_active_project",
        "scheduler_jobs",
        ["tenant_id", "project_id"],
        unique=True,
        postgresql_where=sa.text(
            "project_id IS NOT NULL AND state IN ('leased', 'running', 'cancel_requested')"
        ),
    )
    op.create_index(
        "uq_scheduler_active_site",
        "scheduler_jobs",
        ["tenant_id", "site_id"],
        unique=True,
        postgresql_where=sa.text(
            "site_id IS NOT NULL AND state IN ('leased', 'running', 'cancel_requested')"
        ),
    )
    _tenant_policy("scheduler_jobs")

    op.create_table(
        "scheduler_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "scheduler_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scheduler_jobs.id", ondelete="CASCADE"),
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
        ),
        sa.Column(
            "site_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sites.id", ondelete="CASCADE")
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("safe_code", sa.String(length=64)),
        sa.Column(
            "details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("scheduler_job_id", "sequence", name="uq_scheduler_attempt_sequence"),
        sa.CheckConstraint("sequence > 0 AND attempt >= 0", name="ck_scheduler_attempt_numbers"),
        sa.CheckConstraint(
            "event_type IN ('queued', 'claimed', 'wake_published', 'started', "
            "'lease_expired', 'paused', 'resumed', 'cancel_requested', "
            "'cancelled', 'succeeded', 'failed')",
            name="ck_scheduler_attempt_event",
        ),
        sa.CheckConstraint(
            "safe_code IS NULL OR safe_code ~ '^[a-z0-9_]{1,64}$'", name="ck_scheduler_attempt_code"
        ),
        sa.CheckConstraint(
            "octet_length(details::text) <= 4096", name="ck_scheduler_attempt_details"
        ),
    )
    op.create_index(
        "ix_scheduler_attempts_job_sequence", "scheduler_attempts", ["scheduler_job_id", "sequence"]
    )
    _tenant_policy("scheduler_attempts")
    op.execute(
        """
        CREATE FUNCTION forbid_scheduler_attempt_mutation()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'scheduler_attempts are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_forbid_scheduler_attempt_mutation
        BEFORE UPDATE OR DELETE ON scheduler_attempts
        FOR EACH ROW EXECUTE FUNCTION forbid_scheduler_attempt_mutation()
        """
    )

    op.create_table(
        "scheduler_wakeups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "scheduler_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scheduler_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lease_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("scheduler_job_id", "lease_id", name="uq_scheduler_wakeup_lease"),
        sa.CheckConstraint("state IN ('pending', 'published')", name="ck_scheduler_wakeup_state"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_scheduler_wakeup_attempts"),
    )
    op.create_index("ix_scheduler_wakeups_pending", "scheduler_wakeups", ["state", "available_at"])
    _tenant_policy("scheduler_wakeups")

    op.create_table(
        "scheduler_project_turns",
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
        sa.Column("dispatch_turn", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_dispatched_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "project_id", name="uq_scheduler_project_turn"),
        sa.CheckConstraint("dispatch_turn >= 0", name="ck_scheduler_project_turn"),
    )
    _tenant_policy("scheduler_project_turns")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_forbid_scheduler_attempt_mutation ON scheduler_attempts")
    op.execute("DROP FUNCTION IF EXISTS forbid_scheduler_attempt_mutation()")
    for table in (
        "scheduler_project_turns",
        "scheduler_wakeups",
        "scheduler_attempts",
        "scheduler_jobs",
    ):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
    op.drop_table("scheduler_project_turns")
    op.drop_table("scheduler_wakeups")
    op.drop_table("scheduler_attempts")
    op.drop_table("scheduler_jobs")
