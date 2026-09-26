"""Require site-scoped idempotency for public lead intake."""

from __future__ import annotations

import secrets
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_lead_idempotency"
down_revision: str | None = "0018_ai_provider_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    site_ids = bind.execute(
        sa.text("SELECT id FROM sites WHERE lead_token IS NULL OR lead_token = ''")
    ).scalars()
    for site_id in site_ids:
        bind.execute(
            sa.text("UPDATE sites SET lead_token = :token WHERE id = :site_id"),
            {"site_id": site_id, "token": secrets.token_urlsafe(32)},
        )
    op.alter_column("sites", "lead_token", existing_type=sa.String(length=64), nullable=False)

    op.execute(
        "UPDATE leads SET idempotency_key = id::text "
        "WHERE idempotency_key IS NULL OR idempotency_key = ''"
    )
    op.drop_constraint("uq_leads_tenant_idem", "leads", type_="unique")
    op.alter_column("leads", "idempotency_key", existing_type=sa.String(length=128), nullable=False)
    op.create_unique_constraint(
        "uq_leads_tenant_site_idem",
        "leads",
        ["tenant_id", "site_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_leads_tenant_site_idem", "leads", type_="unique")
    op.alter_column("leads", "idempotency_key", existing_type=sa.String(length=128), nullable=True)
    op.create_unique_constraint("uq_leads_tenant_idem", "leads", ["tenant_id", "idempotency_key"])
    op.alter_column("sites", "lead_token", existing_type=sa.String(length=64), nullable=True)
