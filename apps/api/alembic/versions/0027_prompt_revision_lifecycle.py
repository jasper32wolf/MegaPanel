"""Add review metadata to managed prompt revisions."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_prompt_revision_lifecycle"
down_revision: str | None = "0026_project_semantic_collections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "prompt_registry",
        sa.Column("state", sa.String(length=16), nullable=False, server_default="active"),
    )
    op.add_column("prompt_registry", sa.Column("created_by", sa.UUID(), nullable=True))
    op.add_column("prompt_registry", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("prompt_registry", sa.Column("reviewed_by", sa.UUID(), nullable=True))
    op.add_column("prompt_registry", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("prompt_registry", sa.Column("decision_reason", sa.Text(), nullable=True))
    op.create_index("ix_prompt_registry_state", "prompt_registry", ["state"])


def downgrade() -> None:
    op.drop_index("ix_prompt_registry_state", table_name="prompt_registry")
    op.drop_column("prompt_registry", "decision_reason")
    op.drop_column("prompt_registry", "reviewed_at")
    op.drop_column("prompt_registry", "reviewed_by")
    op.drop_column("prompt_registry", "submitted_at")
    op.drop_column("prompt_registry", "created_by")
    op.drop_column("prompt_registry", "state")
