"""Freeze finalized prompt revision content."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0030_prompt_revision_immutability"
down_revision: str | None = "0029_worker_heartbeat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION prevent_finalized_prompt_revision_mutation() RETURNS trigger AS $$
        BEGIN
          IF OLD.state <> 'draft' AND (
            NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR
            NEW.key IS DISTINCT FROM OLD.key OR
            NEW.version IS DISTINCT FROM OLD.version OR
            NEW.tier IS DISTINCT FROM OLD.tier OR
            NEW.template IS DISTINCT FROM OLD.template OR
            NEW.schema_json IS DISTINCT FROM OLD.schema_json OR
            NEW.created_by IS DISTINCT FROM OLD.created_by OR
            NEW.submitted_at IS DISTINCT FROM OLD.submitted_at OR
            NEW.created_at IS DISTINCT FROM OLD.created_at
          ) THEN
            RAISE EXCEPTION 'Finalized prompt revision content is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER prompt_revision_finalized_immutable
        BEFORE UPDATE ON prompt_registry
        FOR EACH ROW EXECUTE FUNCTION prevent_finalized_prompt_revision_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS prompt_revision_finalized_immutable ON prompt_registry")
    op.execute("DROP FUNCTION IF EXISTS prevent_finalized_prompt_revision_mutation()")
