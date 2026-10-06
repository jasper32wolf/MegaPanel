"""Mark first-party telemetry and retain only bounded raw events and daily counts."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0059_telemetry_retention"
down_revision: str | None = "0058_scheduler_lease_fencing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "analytics_events",
        sa.Column("source", sa.String(16), nullable=False, server_default="legacy"),
    )
    op.create_check_constraint(
        "ck_analytics_event_source", "analytics_events", "source IN ('legacy', 'first_party')"
    )
    # Older first-party events had no marker. Migrate only rows with the exact
    # allowlisted shape; arbitrary legacy payloads must never enter the rollup.
    op.execute("SELECT set_config('app.bypass_rls', 'on', true)")
    op.execute(
        """
        UPDATE analytics_events AS e SET source = 'first_party'
        FROM sites AS s
        WHERE s.id = e.site_id AND s.tenant_id = e.tenant_id
          AND e.event IN ('page_view', 'form_open', 'form_submit_result',
                          'cta_click', 'assistant_open', 'assistant_submit',
                          'exit_offer_shown', 'exit_offer_accepted')
          AND left(e.path, 1) = '/' AND char_length(e.path) <= 512
          AND strpos(e.path, '?') = 0 AND strpos(e.path, '#') = 0
          AND jsonb_typeof(e.payload) = 'object'
          AND e.payload - 'session' = '{}'::jsonb
          AND e.payload->>'session' ~ '^[0-9a-f]{64}$'
        """
    )
    op.execute("SELECT set_config('app.bypass_rls', 'off', true)")
    op.create_index("ix_analytics_events_created_at", "analytics_events", ["created_at"])
    op.create_index(
        "ix_analytics_events_source_created", "analytics_events", ["source", "created_at"]
    )
    op.create_index(
        "ix_analytics_events_site_day",
        "analytics_events",
        ["tenant_id", "site_id", "event", "created_at"],
    )
    op.create_index(
        "ix_analytics_events_session",
        "analytics_events",
        ["site_id", sa.text("(payload->>'session')")],
        postgresql_where=sa.text("source = 'first_party'"),
    )
    op.create_table(
        "analytics_daily_aggregates",
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("session_days", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "site_id",
            "event_date",
            "event",
            "path",
            name="pk_analytics_daily_aggregates",
        ),
        sa.CheckConstraint(
            "event_count >= 0 AND session_days >= 0", name="ck_analytics_daily_counts"
        ),
        sa.CheckConstraint("left(path, 1) = '/'", name="ck_analytics_daily_path"),
    )
    op.create_table(
        "analytics_revoked_sessions",
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_digest", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id", "site_id", "session_digest", name="pk_analytics_revoked_sessions"
        ),
        sa.CheckConstraint("session_digest ~ '^[0-9a-f]{64}$'", name="ck_analytics_revoked_digest"),
    )
    op.create_index(
        "ix_analytics_revoked_sessions_expires",
        "analytics_revoked_sessions",
        ["expires_at"],
    )
    for table in ("analytics_daily_aggregates", "analytics_revoked_sessions"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table}
            ON {table}
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
    op.execute("SELECT set_config('app.bypass_rls', 'on', true)")
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM analytics_daily_aggregates) THEN
            RAISE EXCEPTION 'Export or explicitly discard daily telemetry before downgrade';
          END IF;
          IF EXISTS (
            SELECT 1 FROM analytics_revoked_sessions WHERE expires_at > now()
          ) THEN
            RAISE EXCEPTION 'Active session revocations must expire before downgrade';
          END IF;
        END $$;
        """
    )
    for table in ("analytics_revoked_sessions", "analytics_daily_aggregates"):
        op.execute(f"DROP POLICY tenant_isolation_{table} ON {table}")
    op.drop_index("ix_analytics_revoked_sessions_expires", table_name="analytics_revoked_sessions")
    op.drop_table("analytics_revoked_sessions")
    op.drop_table("analytics_daily_aggregates")
    op.drop_index("ix_analytics_events_session", table_name="analytics_events")
    op.drop_index("ix_analytics_events_site_day", table_name="analytics_events")
    op.drop_index("ix_analytics_events_source_created", table_name="analytics_events")
    op.drop_index("ix_analytics_events_created_at", table_name="analytics_events")
    op.drop_constraint("ck_analytics_event_source", "analytics_events", type_="check")
    op.drop_column("analytics_events", "source")
    op.execute("SELECT set_config('app.bypass_rls', 'off', true)")
