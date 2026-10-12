"""Store panel-managed generic SMTP transport for centralized lead delivery."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0069_panel_lead_smtp_transport"
down_revision: str | None = "0068_central_lead_delivery_defaults"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "central_lead_smtp_transports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("tls_mode", sa.String(length=16), nullable=False),
        sa.Column("sender_email", sa.String(length=320), nullable=False),
        sa.Column("recipient_enc", sa.Text(), nullable=False),
        sa.Column("smtp_username_enc", sa.Text(), nullable=True),
        sa.Column("smtp_password_enc", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(
            "tls_mode IN ('starttls', 'implicit_tls')",
            name="ck_central_lead_smtp_transport_tls_mode",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_central_lead_smtp_transport_revision",
        ),
        sa.CheckConstraint(
            "(smtp_username_enc IS NULL) = (smtp_password_enc IS NULL)",
            name="ck_central_lead_smtp_transport_credentials",
        ),
    )
    op.create_index(
        "ix_central_lead_smtp_transports_tenant_id",
        "central_lead_smtp_transports",
        ["tenant_id"],
    )
    op.execute("ALTER TABLE central_lead_smtp_transports ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE central_lead_smtp_transports FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_central_lead_smtp_transports
        ON central_lead_smtp_transports
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
    op.add_column("webhook_deliveries", sa.Column("lead_transport_revision", sa.Integer()))


def downgrade() -> None:
    op.drop_column("webhook_deliveries", "lead_transport_revision")
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_central_lead_smtp_transports "
        "ON central_lead_smtp_transports"
    )
    op.drop_table("central_lead_smtp_transports")
