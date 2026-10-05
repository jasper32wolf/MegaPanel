"""Add bounded candidate-build scheduling fields.

Candidate builds remain immutable and unpublished. These fields only let the
worker select one due build at a time instead of flooding the local VPS.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050_candidate_build_scheduling"
down_revision: str | None = "0049_design_profile_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "site_builds",
        sa.Column("queue_priority", sa.Integer(), nullable=False, server_default="50"),
    )
    op.add_column("site_builds", sa.Column("not_before", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_site_builds_queue_priority", "site_builds", ["queue_priority"])
    op.create_index("ix_site_builds_not_before", "site_builds", ["not_before"])
    op.create_check_constraint(
        "ck_site_builds_queue_priority",
        "site_builds",
        "queue_priority >= 0 AND queue_priority <= 100",
    )


def downgrade() -> None:
    op.drop_constraint("ck_site_builds_queue_priority", "site_builds", type_="check")
    op.drop_index("ix_site_builds_not_before", table_name="site_builds")
    op.drop_index("ix_site_builds_queue_priority", table_name="site_builds")
    op.drop_column("site_builds", "not_before")
    op.drop_column("site_builds", "queue_priority")
