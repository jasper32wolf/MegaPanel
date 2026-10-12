"""Add central lead-delivery routing metadata.

Existing project policies retain their current behavior by replacing the future
server-owned global destination. Existing deliveries are labelled according to
the routing data they already snapshot.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0068_central_lead_delivery_defaults"
down_revision: str | None = "0067_project_branding_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lead_routing_policies",
        sa.Column("delivery_mode", sa.String(length=16), nullable=True),
    )
    # Historical policies only delivered to their configured destinations.
    op.execute(
        "UPDATE lead_routing_policies "
        "SET delivery_mode = 'replace_global' "
        "WHERE delivery_mode IS NULL"
    )
    op.alter_column("lead_routing_policies", "delivery_mode", nullable=False)
    op.create_check_constraint(
        "ck_lead_routing_policies_delivery_mode",
        "lead_routing_policies",
        "delivery_mode IN ('append_global', 'replace_global')",
    )

    op.add_column(
        "webhook_deliveries",
        sa.Column("routing_source", sa.String(length=16), nullable=True),
    )
    op.execute(
        "UPDATE webhook_deliveries "
        "SET routing_source = CASE "
        "WHEN routing_policy_id IS NULL THEN 'legacy' "
        "ELSE 'project_policy' END "
        "WHERE routing_source IS NULL"
    )
    op.alter_column("webhook_deliveries", "routing_source", nullable=False)
    op.create_check_constraint(
        "ck_webhook_deliveries_routing_source",
        "webhook_deliveries",
        "routing_source IN ('global_default', 'project_policy', 'legacy')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_webhook_deliveries_routing_source", "webhook_deliveries", type_="check"
    )
    op.drop_column("webhook_deliveries", "routing_source")
    op.drop_constraint(
        "ck_lead_routing_policies_delivery_mode", "lead_routing_policies", type_="check"
    )
    op.drop_column("lead_routing_policies", "delivery_mode")
