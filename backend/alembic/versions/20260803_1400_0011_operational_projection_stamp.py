"""Projection stamp on operational rows (P3a — §4.1 / external code re-review
2026-08-03 finding 3).

Rehydration decided whether raw was needed by SCANNING the operational value for
redaction markers. Deleting a marker from a row therefore bypassed the vault
fetch entirely and resume/fork ran on tampered projected data with no system
audit. Persisted metadata — not the payload's own content — must drive that
decision, so a row written as a projection now carries the projector +
projection-schema versions it was written under. NULL = never projected.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-03 14:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("workflow_instances", "step_executions")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("projector_version", sa.String(length=64), nullable=True))
        op.add_column(table, sa.Column("projection_schema_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "projection_schema_version")
        op.drop_column(table, "projector_version")
