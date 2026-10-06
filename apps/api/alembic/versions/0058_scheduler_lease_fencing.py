"""Fence scheduler leases, preserve cancelled previews, and backfill queued work."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0058_scheduler_lease_fencing"
down_revision: str | None = "0057_schedule_competitor_crawls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_scheduler_wakeup_state", "scheduler_wakeups", type_="check")
    op.create_check_constraint(
        "ck_scheduler_wakeup_state",
        "scheduler_wakeups",
        "state IN ('pending', 'published', 'expired')",
    )
    op.drop_constraint(
        "ck_bukvarix_keyword_run_status", "project_bukvarix_keyword_runs", type_="check"
    )
    op.create_check_constraint(
        "ck_bukvarix_keyword_run_status",
        "project_bukvarix_keyword_runs",
        "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
    )
    op.execute(
        """
        CREATE FUNCTION forbid_scheduler_source_mutation() RETURNS trigger AS $$
        BEGIN
          IF (OLD.tenant_id, OLD.project_id, OLD.site_id, OLD.work_type,
              OLD.source_id, OLD.source_hash, OLD.source_version)
             IS DISTINCT FROM
             (NEW.tenant_id, NEW.project_id, NEW.site_id, NEW.work_type,
              NEW.source_id, NEW.source_hash, NEW.source_version) THEN
            RAISE EXCEPTION 'scheduler source identity is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_forbid_scheduler_source_mutation
        BEFORE UPDATE ON scheduler_jobs
        FOR EACH ROW EXECUTE FUNCTION forbid_scheduler_source_mutation()
        """
    )

    # Maintenance backfill must see tenant-owned rows despite FORCE RLS.
    op.execute("SELECT set_config('app.bypass_rls', 'on', true)")
    # Existing frozen, queued work must remain runnable after switching the sweeper.
    op.execute(
        """
        INSERT INTO scheduler_jobs
          (id, tenant_id, project_id, site_id, work_type, source_id,
           source_hash, source_version, state, priority, not_before,
           requested_by, queued_at, first_eligible_at)
        SELECT gen_random_uuid(), b.tenant_id, b.project_id, b.site_id,
               'site_build', b.id, b.input_snapshot_hash, 1, 'queued',
               b.queue_priority, b.not_before, b.requested_by,
               b.created_at, b.created_at
        FROM site_builds AS b
        WHERE b.status = 'queued' AND b.project_id IS NOT NULL
          AND b.snapshot_version = 1 AND b.input_snapshot IS NOT NULL
          AND b.input_snapshot_hash ~ '^[0-9a-f]{64}$'
        ON CONFLICT (work_type, source_id) DO NOTHING
        """
    )
    # Legacy incomplete candidates cannot be dispatched by the new UUID-only worker.
    op.execute(
        """
        UPDATE site_builds AS b
        SET status = 'failed', failure_code = 'snapshot_invalid', completed_at = now()
        WHERE b.status = 'queued' AND NOT EXISTS (
          SELECT 1 FROM scheduler_jobs AS j
          WHERE j.work_type = 'site_build' AND j.source_id = b.id
        )
        """
    )
    op.execute(
        """
        INSERT INTO scheduler_jobs
          (id, tenant_id, project_id, work_type, source_id,
           source_hash, source_version, state, priority, queued_at,
           first_eligible_at, requested_by)
        SELECT gen_random_uuid(), r.tenant_id, r.project_id,
               'bukvarix_keyword', r.id, r.seed_snapshot_hash, 1,
               'queued', 50, COALESCE(r.queued_at, r.created_at),
               COALESCE(r.queued_at, r.created_at), r.requested_by
        FROM project_bukvarix_keyword_runs AS r
        WHERE r.status = 'queued' AND jsonb_typeof(r.seed_snapshot) = 'array'
          AND r.seed_snapshot_hash ~ '^[0-9a-f]{64}$'
        ON CONFLICT (work_type, source_id) DO NOTHING
        """
    )
    jobs = sa.table(
        "scheduler_jobs",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("tenant_id", postgresql.UUID(as_uuid=True)),
        sa.column("project_id", postgresql.UUID(as_uuid=True)),
        sa.column("work_type", sa.String()),
        sa.column("source_id", postgresql.UUID(as_uuid=True)),
        sa.column("source_hash", sa.String()),
        sa.column("source_version", sa.Integer()),
        sa.column("state", sa.String()),
        sa.column("priority", sa.Integer()),
    )
    connection = op.get_bind()
    crawls = connection.execute(
        sa.text(
            "SELECT id, tenant_id, project_id, root_url, origin, configuration "
            "FROM competitor_crawl_runs WHERE status = 'queued'"
        )
    )
    for crawl in crawls:
        source = json.dumps(
            {
                "root_url": crawl.root_url,
                "origin": crawl.origin,
                "configuration": crawl.configuration or {},
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        connection.execute(
            postgresql.insert(jobs)
            .values(
                id=uuid4(),
                tenant_id=crawl.tenant_id,
                project_id=crawl.project_id,
                work_type="competitor_crawl",
                source_id=crawl.id,
                source_hash=hashlib.sha256(source.encode("utf-8")).hexdigest(),
                source_version=1,
                state="queued",
                priority=50,
            )
            .on_conflict_do_nothing(constraint="uq_scheduler_job_source")
        )
    op.execute(
        """
        INSERT INTO scheduler_attempts
          (id, scheduler_job_id, tenant_id, project_id, site_id,
           sequence, attempt, event_type, safe_code)
        SELECT gen_random_uuid(), j.id, j.tenant_id, j.project_id,
               j.site_id, 1, 0, 'queued', 'migration_backfill'
        FROM scheduler_jobs AS j
        WHERE NOT EXISTS (
          SELECT 1 FROM scheduler_attempts AS a WHERE a.scheduler_job_id = j.id
        )
        """
    )
    op.execute("SELECT set_config('app.bypass_rls', 'off', true)")


def downgrade() -> None:
    op.execute("SELECT set_config('app.bypass_rls', 'on', true)")
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM scheduler_wakeups WHERE state = 'expired')
            OR EXISTS (
              SELECT 1 FROM project_bukvarix_keyword_runs WHERE status = 'cancelled'
            ) THEN
            RAISE EXCEPTION 'Review expired wakeups and cancelled previews before downgrade';
          END IF;
        END $$;
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_forbid_scheduler_source_mutation ON scheduler_jobs")
    op.execute("DROP FUNCTION IF EXISTS forbid_scheduler_source_mutation()")
    op.drop_constraint("ck_scheduler_wakeup_state", "scheduler_wakeups", type_="check")
    op.create_check_constraint(
        "ck_scheduler_wakeup_state", "scheduler_wakeups", "state IN ('pending', 'published')"
    )
    op.drop_constraint(
        "ck_bukvarix_keyword_run_status", "project_bukvarix_keyword_runs", type_="check"
    )
    op.create_check_constraint(
        "ck_bukvarix_keyword_run_status",
        "project_bukvarix_keyword_runs",
        "status IN ('queued', 'running', 'completed', 'failed')",
    )
    op.execute("SELECT set_config('app.bypass_rls', 'off', true)")
