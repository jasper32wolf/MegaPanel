"""Bind manual competitor research to projects and approved evidence."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025_project_competitor_evidence"
down_revision: str | None = "0024_email_delivery_targets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "competitor_scans",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_competitor_scans_project_id", "competitor_scans", ["project_id"])

    op.add_column(
        "knowledge_docs",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column(
        "knowledge_docs",
        sa.Column("state", sa.String(length=16), nullable=False, server_default="draft"),
    )
    op.add_column("knowledge_docs", sa.Column("approved_by", postgresql.UUID(as_uuid=True)))
    op.add_column("knowledge_docs", sa.Column("approved_at", sa.DateTime(timezone=True)))
    op.create_foreign_key(
        "fk_knowledge_docs_source_scan_id",
        "knowledge_docs",
        "competitor_scans",
        ["source_scan_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_knowledge_docs_project_id", "knowledge_docs", ["project_id"])
    op.create_index("ix_knowledge_docs_state", "knowledge_docs", ["state"])
    op.create_index(
        "uq_knowledge_docs_competitor_evidence_scan",
        "knowledge_docs",
        ["source_scan_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'competitor_evidence'"),
    )
    op.create_check_constraint(
        "ck_knowledge_docs_competitor_evidence_approved",
        "knowledge_docs",
        "kind <> 'competitor_evidence' OR "
        "(project_id IS NOT NULL AND source_scan_id IS NOT NULL AND state = 'approved' "
        "AND approved_by IS NOT NULL AND approved_at IS NOT NULL)",
    )
    op.execute(
        """
        CREATE FUNCTION prevent_competitor_evidence_mutation() RETURNS trigger AS $$
        BEGIN
          IF OLD.kind = 'competitor_evidence' THEN
            RAISE EXCEPTION 'Approved competitor evidence is immutable';
          END IF;
          RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER competitor_evidence_immutable
        BEFORE UPDATE OR DELETE ON knowledge_docs
        FOR EACH ROW EXECUTE FUNCTION prevent_competitor_evidence_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS competitor_evidence_immutable ON knowledge_docs")
    op.execute("DROP FUNCTION IF EXISTS prevent_competitor_evidence_mutation()")
    op.drop_constraint("ck_knowledge_docs_competitor_evidence_approved", "knowledge_docs")
    op.drop_index("uq_knowledge_docs_competitor_evidence_scan", table_name="knowledge_docs")
    op.drop_index("ix_knowledge_docs_state", table_name="knowledge_docs")
    op.drop_index("ix_knowledge_docs_project_id", table_name="knowledge_docs")
    op.drop_constraint("fk_knowledge_docs_source_scan_id", "knowledge_docs", type_="foreignkey")
    op.drop_column("knowledge_docs", "approved_at")
    op.drop_column("knowledge_docs", "approved_by")
    op.drop_column("knowledge_docs", "state")
    op.drop_column("knowledge_docs", "project_id")
    op.drop_index("ix_competitor_scans_project_id", table_name="competitor_scans")
    op.drop_column("competitor_scans", "project_id")
