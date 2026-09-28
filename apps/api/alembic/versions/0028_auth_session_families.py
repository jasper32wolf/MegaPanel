"""Group rotated refresh tokens into device-safe session families."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028_auth_session_families"
down_revision: str | None = "0027_prompt_revision_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "auth_sessions", sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("auth_sessions", sa.Column("device_label", sa.String(length=128), nullable=True))
    op.execute("UPDATE auth_sessions SET family_id = id WHERE family_id IS NULL")
    op.alter_column("auth_sessions", "family_id", nullable=False)
    op.create_index("ix_auth_sessions_user_family", "auth_sessions", ["user_id", "family_id"])


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_user_family", table_name="auth_sessions")
    op.drop_column("auth_sessions", "device_label")
    op.drop_column("auth_sessions", "family_id")
