"""Add a public token to authenticate generated lead forms."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_site_lead_token"
down_revision: str | None = "0012_auth_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sites", sa.Column("lead_token", sa.String(64), nullable=True))
    op.create_unique_constraint("uq_sites_lead_token", "sites", ["lead_token"])


def downgrade() -> None:
    op.drop_constraint("uq_sites_lead_token", "sites", type_="unique")
    op.drop_column("sites", "lead_token")
