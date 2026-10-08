"""Persist a baseline hash for published immutable releases."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0063_site_release_integrity"
down_revision: str | None = "0062_operator_alert_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sites", sa.Column("integrity_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("sites", "integrity_hash")
