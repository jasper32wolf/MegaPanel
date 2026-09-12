"""Phase 11: block kits library sync + content_blocks provenance."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_block_kits"
down_revision: Union[str, None] = "0010_harden"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "block_kits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(128), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), server_default=""),
        sa.Column("niches", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb")),
        sa.Column("theme", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("meta", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "block_kit_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "kit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("block_kits.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("block_type", sa.String(64), nullable=False),
        sa.Column("sort", sa.Integer(), server_default="0"),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("html", sa.Text(), nullable=False),
        sa.Column("css", sa.Text(), server_default=""),
        sa.Column("props", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_block_kit_items_kit_id", "block_kit_items", ["kit_id"])

    op.add_column("content_blocks", sa.Column("kit_key", sa.String(128), nullable=True))
    op.add_column("content_blocks", sa.Column("library_version", sa.String(32), nullable=True))
    op.add_column(
        "content_blocks",
        sa.Column("source", sa.String(32), server_default="tenant", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("content_blocks", "source")
    op.drop_column("content_blocks", "library_version")
    op.drop_column("content_blocks", "kit_key")
    op.drop_table("block_kit_items")
    op.drop_table("block_kits")
