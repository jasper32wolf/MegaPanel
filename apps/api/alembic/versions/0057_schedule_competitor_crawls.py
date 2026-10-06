"""Allow bounded competitor research through the durable scheduler."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0057_schedule_competitor_crawls"
down_revision: str | None = "0056_schedule_bukvarix_previews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_scheduler_job_type", "scheduler_jobs", type_="check")
    op.create_check_constraint(
        "ck_scheduler_job_type",
        "scheduler_jobs",
        "work_type IN ('site_build', 'bukvarix_keyword', 'competitor_crawl')",
    )


def downgrade() -> None:
    op.execute("SELECT set_config('app.bypass_rls', 'on', true)")
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM scheduler_jobs WHERE work_type = 'competitor_crawl') THEN
            RAISE EXCEPTION 'Remove competitor crawl jobs explicitly before downgrade';
          END IF;
        END $$;
        """
    )
    op.drop_constraint("ck_scheduler_job_type", "scheduler_jobs", type_="check")
    op.create_check_constraint(
        "ck_scheduler_job_type",
        "scheduler_jobs",
        "work_type IN ('site_build', 'bukvarix_keyword')",
    )
    op.execute("SELECT set_config('app.bypass_rls', 'off', true)")
