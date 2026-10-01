"""Bind approved competitor crawl evidence to immutable knowledge documents."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042_competitor_crawl_evidence"
down_revision: str | None = "0041_competitor_domain_research"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_docs",
        sa.Column(
            "source_crawl_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("competitor_crawl_runs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index("ix_knowledge_docs_source_crawl_id", "knowledge_docs", ["source_crawl_id"])
    op.create_index(
        "uq_knowledge_docs_competitor_crawl_evidence",
        "knowledge_docs",
        ["source_crawl_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'competitor_crawl_evidence'"),
    )
    op.create_check_constraint(
        "ck_knowledge_docs_competitor_crawl_evidence_approved",
        "knowledge_docs",
        "kind <> 'competitor_crawl_evidence' OR "
        "(project_id IS NOT NULL AND source_crawl_id IS NOT NULL AND state = 'approved' "
        "AND approved_by IS NOT NULL AND approved_at IS NOT NULL)",
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_competitor_evidence_mutation() RETURNS trigger AS $$
        BEGIN
          IF OLD.kind IN ('competitor_evidence', 'competitor_crawl_evidence') THEN
            RAISE EXCEPTION 'Approved competitor evidence is immutable';
          END IF;
          RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_competitor_evidence_mutation() RETURNS trigger AS $$
        BEGIN
          IF OLD.kind = 'competitor_evidence' THEN
            RAISE EXCEPTION 'Approved competitor evidence is immutable';
          END IF;
          RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.drop_constraint("ck_knowledge_docs_competitor_crawl_evidence_approved", "knowledge_docs")
    op.drop_index("uq_knowledge_docs_competitor_crawl_evidence", table_name="knowledge_docs")
    op.drop_index("ix_knowledge_docs_source_crawl_id", table_name="knowledge_docs")
    op.drop_column("knowledge_docs", "source_crawl_id")
