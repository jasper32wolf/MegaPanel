"""Protect raw telemetry rows with tenant RLS."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0052_secure_first_party_telemetry"
down_revision: str | None = "0051_index_promotion_schedules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE analytics_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE analytics_events FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_analytics_events
        ON analytics_events
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
    op.execute("DROP POLICY IF EXISTS tenant_isolation_analytics_events ON analytics_events")
    op.execute("ALTER TABLE analytics_events NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE analytics_events DISABLE ROW LEVEL SECURITY")
