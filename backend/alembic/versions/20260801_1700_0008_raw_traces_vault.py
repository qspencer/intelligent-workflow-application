"""Raw-trace vault (docs/TRACE_GOVERNANCE_PLAN.md §4.1, TG3a).

The storage-inversion vault. TG3a dark-dual-writes raw here while the
operational store keeps its inline copy (the flip to safe-only is TG3b), so
this table is additive and populated in the background. Keyed on the
immutable step-attempt; `idempotency_key` is the unique natural key.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-01 17:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = JSONB().with_variant(JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "raw_traces",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column(
            "instance_id",
            sa.String(length=64),
            sa.ForeignKey("workflow_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_attempt_id", sa.String(length=64), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("raw_schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("projection_schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("projector_version", sa.String(length=64), nullable=False),
        sa.Column("payload", _JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_raw_traces_org_id", "raw_traces", ["org_id"])
    op.create_index("ix_raw_traces_instance_id", "raw_traces", ["instance_id"])


def downgrade() -> None:
    op.drop_index("ix_raw_traces_instance_id", table_name="raw_traces")
    op.drop_index("ix_raw_traces_org_id", table_name="raw_traces")
    op.drop_table("raw_traces")
