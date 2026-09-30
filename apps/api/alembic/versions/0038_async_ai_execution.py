"""Persist immutable async AI execution envelopes."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0038_async_ai_execution"
down_revision: str | None = "0037_build_legal_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_runs",
        sa.Column(
            "execution_envelope",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_ai_execution_envelope_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.execution_envelope IS DISTINCT FROM OLD.execution_envelope THEN
            RAISE EXCEPTION 'ai execution envelope is immutable';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER ai_runs_execution_envelope_immutable
        BEFORE UPDATE ON ai_runs
        FOR EACH ROW EXECUTE FUNCTION prevent_ai_execution_envelope_update()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ai_runs_execution_envelope_immutable ON ai_runs")
    op.execute("DROP FUNCTION IF EXISTS prevent_ai_execution_envelope_update()")
    op.drop_column("ai_runs", "execution_envelope")
