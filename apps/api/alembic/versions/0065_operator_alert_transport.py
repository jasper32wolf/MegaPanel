"""Store panel-owned SMTP.bz alert transport credentials encrypted."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0065_operator_alert_transport"
down_revision: str | None = "0064_operator_alert_recipient"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operator_alert_transports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("transport", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sender_email", sa.String(length=320), nullable=True),
        sa.Column("smtp_port", sa.Integer(), nullable=True),
        sa.Column("smtp_tls_mode", sa.String(length=16), nullable=True),
        sa.Column("smtp_username_enc", sa.String(length=1024), nullable=True),
        sa.Column("smtp_password_enc", sa.String(length=1024), nullable=True),
        sa.Column("api_authorization_enc", sa.String(length=1024), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(
            "transport IN ('none', 'smtp_bz_smtp', 'smtp_bz_api')",
            name="ck_operator_alert_transport_kind",
        ),
        sa.CheckConstraint(
            "smtp_tls_mode IS NULL OR smtp_tls_mode IN ('starttls', 'implicit_tls')",
            name="ck_operator_alert_transport_tls_mode",
        ),
    )
    op.create_index(
        "ix_operator_alert_transports_tenant_id",
        "operator_alert_transports",
        ["tenant_id"],
    )
    op.execute("ALTER TABLE operator_alert_transports ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE operator_alert_transports FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_operator_alert_transports
        ON operator_alert_transports
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
    op.add_column("alert_deliveries", sa.Column("transport_revision", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("alert_deliveries", "transport_revision")
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_operator_alert_transports "
        "ON operator_alert_transports"
    )
    op.drop_table("operator_alert_transports")
