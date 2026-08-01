"""Explicit step-attempt number (docs/EXECUTION_SEMANTICS.md §3a).

The engine already appends one `step_executions` row per attempt; this adds
the 1-based `attempt` number so the step-attempt is a first-class identity
(the raw-traces vault keys on the row id + attempt). Additive — pre-existing
rows read as attempt 1.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-01 15:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "step_executions",
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("step_executions", "attempt")
