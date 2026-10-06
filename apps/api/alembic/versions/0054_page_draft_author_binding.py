"""Bind page drafts to immutable approved author revisions."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0054_page_draft_author_binding"
down_revision: str | None = "0053_author_profile_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "page_drafts",
        sa.Column(
            "author_profile_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("author_profile_revisions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_page_drafts_author_profile_revision_id",
        "page_drafts",
        ["author_profile_revision_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_page_drafts_author_profile_revision_id", table_name="page_drafts")
    op.drop_column("page_drafts", "author_profile_revision_id")
