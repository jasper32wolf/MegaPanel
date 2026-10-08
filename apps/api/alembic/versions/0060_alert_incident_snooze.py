"""Add bounded operator snooze persistence to alert incidents."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0060_alert_incident_snooze"
down_revision: str | None = "0059_telemetry_retention"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("alert_incidents", sa.Column("snoozed_until", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("alert_incidents", "snoozed_until")
