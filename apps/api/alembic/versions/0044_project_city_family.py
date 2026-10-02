"""Link city-specific child projects to a master project family."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044_project_city_family"
down_revision: str | None = "0043_site_structure_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_family_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "master_project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "child_project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "geo_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("geo_places.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column(
            "source_structure_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("site_structure_revisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("master_project_id", "geo_id", name="uq_project_family_master_geo"),
        sa.UniqueConstraint("child_project_id", name="uq_project_family_child"),
        sa.UniqueConstraint("hostname", name="uq_project_family_hostname"),
    )
    op.create_index("ix_project_family_members_tenant", "project_family_members", ["tenant_id"])
    op.create_index(
        "ix_project_family_members_master", "project_family_members", ["master_project_id"]
    )
    op.create_index(
        "ix_project_family_members_child", "project_family_members", ["child_project_id"]
    )
    op.create_index("ix_project_family_members_geo", "project_family_members", ["geo_id"])
    op.execute("ALTER TABLE project_family_members ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE project_family_members FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_project_family_members
        ON project_family_members
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
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_project_family_members ON project_family_members"
    )
    op.drop_index("ix_project_family_members_geo", table_name="project_family_members")
    op.drop_index("ix_project_family_members_child", table_name="project_family_members")
    op.drop_index("ix_project_family_members_master", table_name="project_family_members")
    op.drop_index("ix_project_family_members_tenant", table_name="project_family_members")
    op.drop_table("project_family_members")
