"""Add immutable bounded operational verification evidence."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_operational_verifications"
down_revision: str | None = "0038_async_ai_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CONTROL_KEYS = (
    "controlled_fixture_contract",
    "controlled_ci_candidate_flow",
    "production_compose_localhost",
    "staging_restore_drill",
    "vps_external_origin",
)
MODES = ("fixture", "local_compose", "ci", "staging", "vps")
OUTCOMES = ("passed", "failed")
SOURCE_KINDS = ("github_actions", "controlled_runner", "operator_attestation")


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "operational_verifications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("check_key", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=24), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("source_kind", sa.String(length=24), nullable=False),
        sa.Column("source_ref", sa.String(length=128), nullable=False),
        sa.Column("code_sha", sa.String(length=64), nullable=True),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            f"check_key IN ({_values(CONTROL_KEYS)})", name="ck_operational_verification_check_key"
        ),
        sa.CheckConstraint(f"mode IN ({_values(MODES)})", name="ck_operational_verification_mode"),
        sa.CheckConstraint(
            f"outcome IN ({_values(OUTCOMES)})", name="ck_operational_verification_outcome"
        ),
        sa.CheckConstraint(
            f"source_kind IN ({_values(SOURCE_KINDS)})",
            name="ck_operational_verification_source_kind",
        ),
        sa.CheckConstraint("length(source_ref) > 0", name="ck_operational_verification_source_ref"),
    )
    op.create_index(
        "ix_operational_verifications_check_mode_observed",
        "operational_verifications",
        ["check_key", "mode", "observed_at"],
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_operational_verification_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'operational verification evidence is immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER operational_verifications_immutable
        BEFORE UPDATE OR DELETE ON operational_verifications
        FOR EACH ROW EXECUTE FUNCTION prevent_operational_verification_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS operational_verifications_immutable ON operational_verifications"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_operational_verification_mutation()")
    op.drop_index(
        "ix_operational_verifications_check_mode_observed",
        table_name="operational_verifications",
    )
    op.drop_table("operational_verifications")
