"""Local auth (docs/AUTH_PLAN.md): user credentials/roles + auth_sessions.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-18 21:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column("roles", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    # Login is by email; 0003 left email nullable and non-unique (fine for
    # JIT-provisioned SSO rows, fatal for login). Only local-credentialed
    # users must be unique by canonical email.
    op.create_index(
        "uq_users_login_email",
        "users",
        [sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("password_hash IS NOT NULL"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("uq_users_login_email", table_name="users")
    op.drop_column("users", "is_active")
    op.drop_column("users", "roles")
    op.drop_column("users", "password_hash")
