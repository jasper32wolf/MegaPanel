"""Phase 10 harden: MFA pending, FinOps ledger, DSAR jobs."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_harden"
down_revision: str | None = "0009_panel_ux"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_pending", sa.String(64), nullable=True))
    op.add_column(
        "users",
        sa.Column("mfa_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    # Enable MFA for existing users who already have totp_secret
    op.execute("UPDATE users SET mfa_enabled = true WHERE totp_secret IS NOT NULL")

    op.create_table(
        "finops_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("amount_usd", sa.Float(), server_default="0"),
        sa.Column("meta", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_finops_entries_tenant_id", "finops_entries", ["tenant_id"])

    op.create_table(
        "dsar_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("subject_email", sa.String(320), nullable=True),
        sa.Column("subject_phone", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), server_default="queued"),
        sa.Column("result", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_dsar_jobs_tenant_id", "dsar_jobs", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("dsar_jobs")
    op.drop_table("finops_entries")
    op.drop_column("users", "mfa_enabled")
    op.drop_column("users", "totp_pending")
