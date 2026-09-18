"""Enforce one redirect source per site."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "0014_redirect_path_unique"
down_revision: str | None = "0013_site_lead_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_redirects_site_path", "redirects", ["site_id", "from_path"])


def downgrade() -> None:
    op.drop_constraint("uq_redirects_site_path", "redirects", type_="unique")
