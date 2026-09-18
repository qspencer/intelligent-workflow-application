"""Audit-detail vaulting (docs/TRACE_AUDIT_VAULT_DESIGN.md Part 1).

At rest, audit details were projected with no vault row behind them, so the
operational store kept strictly LESS than a grant holder could read — the
model-chosen tool name in `tool_param_override_blocked` was destroyed rather
than withheld, and that is exactly the probe-the-action-surface signal the
entry exists to capture.

The vault's existing key is one row per (org, instance, step_attempt, kind),
but ONE step attempt emits MANY audit entries, so reusing it would collapse
them onto a single row. `audit_entry_id` addresses the row by the entry
instead; `step_attempt_id IS NULL` keeps its existing meaning (instance-level).

Nullable and unbackfilled: pre-existing audit rows were already projected and
their raw is gone, so there is nothing to migrate. Only new writes vault.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-18 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "raw_traces",
        sa.Column("audit_entry_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_raw_traces_audit_entry",
        "raw_traces",
        ["org_id", "audit_entry_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_raw_traces_audit_entry", table_name="raw_traces")
    op.drop_column("raw_traces", "audit_entry_id")
