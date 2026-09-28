"""Scope operator prompt revisions with PostgreSQL RLS."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0023_prompt_registry_rls"
down_revision: str | None = "0022_encrypt_private_lead_email"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE prompt_registry ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE prompt_registry FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_prompt_registry ON prompt_registry
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
    op.execute("DROP POLICY IF EXISTS tenant_isolation_prompt_registry ON prompt_registry")
    op.execute("ALTER TABLE prompt_registry NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE prompt_registry DISABLE ROW LEVEL SECURITY")
