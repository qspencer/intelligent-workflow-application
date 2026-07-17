"""Users + organizations skeleton: users, organizations, ownership columns.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-19 23:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("iss", sa.String(length=255), nullable=False),
        sa.Column("sub", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            server_default="default",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("iss", "sub", name="uq_users_iss_sub"),
    )
    # Bootstrap the default org here — idempotent, single-run, no startup
    # race under multiple workers.
    op.execute(
        "INSERT INTO organizations (id, name, created_at) "
        "VALUES ('default', 'default', now()) ON CONFLICT (id) DO NOTHING"
    )
    op.add_column(
        "workflow_definitions",
        sa.Column("org_id", sa.String(length=64), nullable=False, server_default="default"),
    )
    op.add_column(
        "workflow_definitions",
        sa.Column("owner_user_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "workflow_instances",
        sa.Column("org_id", sa.String(length=64), nullable=False, server_default="default"),
    )


def downgrade() -> None:
    op.drop_column("workflow_instances", "org_id")
    op.drop_column("workflow_definitions", "owner_user_id")
    op.drop_column("workflow_definitions", "org_id")
    op.drop_table("users")
    op.drop_table("organizations")
