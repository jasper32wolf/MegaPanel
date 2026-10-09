"""Store the operator alert recipient encrypted in panel data."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0064_operator_alert_recipient"
down_revision: str | None = "0063_site_release_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operator_alert_recipients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("recipient_enc", sa.String(length=1024), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_operator_alert_recipients_tenant_id",
        "operator_alert_recipients",
        ["tenant_id"],
    )
    op.execute("ALTER TABLE operator_alert_recipients ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE operator_alert_recipients FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_operator_alert_recipients
        ON operator_alert_recipients
        USING (
          current_setting('app.bypass_rls', true) = 'on'
          OR tenant_id::text = current_setting('app.tenant_id', true)
        )
        WITH CHECK (
          current_setting('app.bypass_rls', true) = 'on'
          OR tenant_id::text = current_setting('app.tenant_id', true)
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_operator_alert_recipients "
        "ON operator_alert_recipients"
    )
    op.drop_table("operator_alert_recipients")
