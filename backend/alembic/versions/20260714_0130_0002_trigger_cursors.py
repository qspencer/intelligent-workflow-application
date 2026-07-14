"""Trigger poll-cursor persistence (G9): trigger_cursors.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-14 01:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trigger_cursors",
        sa.Column("trigger_id", sa.String(length=255), primary_key=True),
        sa.Column("cursor", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seen_ids", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("trigger_cursors")
