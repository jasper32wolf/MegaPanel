"""Make candidate builds durable queued jobs with append-only safe events."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0047_durable_site_build_queue"
down_revision: str | None = "0046_project_semantic_source_runs"
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
    op.add_column("site_builds", sa.Column("input_snapshot", postgresql.JSONB(), nullable=True))
    op.add_column(
        "site_builds", sa.Column("input_snapshot_hash", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "site_builds",
        sa.Column("snapshot_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "site_builds",
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "site_builds", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "site_builds", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "site_builds", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "site_builds", sa.Column("last_enqueued_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("site_builds", sa.Column("failure_code", sa.String(length=64), nullable=True))
    op.add_column(
        "site_builds", sa.Column("first_published_at", sa.DateTime(timezone=True), nullable=True)
    )

    # Legacy output snapshots remain usable, but cannot be retried without a frozen input.
    op.execute(
        """
        UPDATE site_builds
        SET status = CASE
            WHEN status IN ('published', 'rolled_back') THEN 'ready'
            WHEN status = 'pending' THEN 'failed'
            WHEN status IN ('ready', 'failed') THEN status
            ELSE 'failed'
        END,
        failure_code = CASE
            WHEN status = 'pending' THEN 'legacy_incomplete'
            WHEN status NOT IN ('pending', 'ready', 'published', 'rolled_back', 'failed')
                THEN 'legacy_incomplete'
            ELSE failure_code
        END,
        first_published_at = CASE
            WHEN status IN ('published', 'rolled_back')
                THEN COALESCE(activated_at, created_at)
            ELSE first_published_at
        END
        """
    )
    op.alter_column("site_builds", "status", server_default="queued")
    op.create_check_constraint(
        "ck_site_build_queue_status",
        "site_builds",
        "status IN ('queued', 'running', 'ready', 'failed')",
    )
    op.create_check_constraint(
        "ck_site_build_attempt_count",
        "site_builds",
        "attempt_count >= 0",
    )
    op.create_index(
        "ix_site_builds_project_status_created",
        "site_builds",
        ["tenant_id", "project_id", "status", "created_at"],
    )
    op.create_index(
        "ix_site_builds_status_lease",
        "site_builds",
        ["status", "lease_expires_at"],
    )

    op.create_table(
        "site_build_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "site_build_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("site_builds.id", ondelete="CASCADE"),
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
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("safe_code", sa.String(length=64), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("site_build_id", "sequence", name="uq_site_build_event_sequence"),
        sa.CheckConstraint("sequence > 0", name="ck_site_build_event_sequence"),
        sa.CheckConstraint("attempt >= 0", name="ck_site_build_event_attempt"),
        sa.CheckConstraint(
            "event_type IN ('queued', 'enqueue_deferred', 'started', 'ready', 'failed', "
            "'retry_requested', 'worker_lease_expired')",
            name="ck_site_build_event_type",
        ),
        sa.CheckConstraint(
            "safe_code IS NULL OR char_length(safe_code) <= 64",
            name="ck_site_build_event_safe_code",
        ),
        sa.CheckConstraint(
            "octet_length(details::text) <= 4096",
            name="ck_site_build_event_details_size",
        ),
    )
    op.create_index(
        "ix_site_build_events_build_sequence",
        "site_build_events",
        ["site_build_id", "sequence"],
    )
    op.create_index(
        "ix_site_build_events_tenant_project_created",
        "site_build_events",
        ["tenant_id", "project_id", "created_at"],
    )
    _tenant_policy("site_build_events")
    op.execute(
        """
        CREATE FUNCTION forbid_site_build_event_mutation()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'site_build_events are append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_forbid_site_build_event_mutation
        BEFORE UPDATE OR DELETE ON site_build_events
        FOR EACH ROW EXECUTE FUNCTION forbid_site_build_event_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_forbid_site_build_event_mutation ON site_build_events")
    op.execute("DROP FUNCTION IF EXISTS forbid_site_build_event_mutation()")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_site_build_events ON site_build_events")
    op.drop_index("ix_site_build_events_tenant_project_created", table_name="site_build_events")
    op.drop_index("ix_site_build_events_build_sequence", table_name="site_build_events")
    op.drop_table("site_build_events")

    op.drop_index("ix_site_builds_status_lease", table_name="site_builds")
    op.drop_index("ix_site_builds_project_status_created", table_name="site_builds")
    op.drop_constraint("ck_site_build_attempt_count", "site_builds", type_="check")
    op.drop_constraint("ck_site_build_queue_status", "site_builds", type_="check")
    op.alter_column("site_builds", "status", server_default="pending")
    op.drop_column("site_builds", "first_published_at")
    op.drop_column("site_builds", "failure_code")
    op.drop_column("site_builds", "last_enqueued_at")
    op.drop_column("site_builds", "lease_expires_at")
    op.drop_column("site_builds", "completed_at")
    op.drop_column("site_builds", "started_at")
    op.drop_column("site_builds", "attempt_count")
    op.drop_column("site_builds", "snapshot_version")
    op.drop_column("site_builds", "input_snapshot_hash")
    op.drop_column("site_builds", "input_snapshot")
