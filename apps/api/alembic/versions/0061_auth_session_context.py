"""Store encrypted session environment snapshots."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0061_auth_session_context"
down_revision: str | None = "0060_alert_incident_snooze"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("auth_sessions", sa.Column("browser_name", sa.String(length=64)))
    op.add_column("auth_sessions", sa.Column("language", sa.String(length=35)))
    op.add_column("auth_sessions", sa.Column("ip_address_enc", sa.Text()))
    op.add_column("auth_sessions", sa.Column("country_enc", sa.Text()))
    op.add_column("auth_sessions", sa.Column("city_enc", sa.Text()))


def downgrade() -> None:
    op.drop_column("auth_sessions", "city_enc")
    op.drop_column("auth_sessions", "country_enc")
    op.drop_column("auth_sessions", "ip_address_enc")
    op.drop_column("auth_sessions", "language")
    op.drop_column("auth_sessions", "browser_name")
