"""Allow preview-only Bukvarix runs in the durable scheduler."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0056_schedule_bukvarix_previews"
down_revision: str | Sequence[str] | None = "0055_generic_durable_scheduler"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_scheduler_job_type", "scheduler_jobs", type_="check")
    op.create_check_constraint(
        "ck_scheduler_job_type",
        "scheduler_jobs",
        "work_type IN ('site_build', 'bukvarix_keyword')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_scheduler_job_type", "scheduler_jobs", type_="check")
    op.create_check_constraint(
        "ck_scheduler_job_type",
        "scheduler_jobs",
        "work_type IN ('site_build')",
    )
