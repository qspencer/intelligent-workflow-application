"""Raw-trace read privilege (docs/TRACE_GOVERNANCE_PLAN.md §2, TG1).

The raw-trace grant is a state machine, distinct from ordinary
administration and default-off for everyone. Active-grant uniqueness per
(principal, org scope) is enforced authoritatively in the grant service's
activation compare-and-set; the partial unique index here is
defense-in-depth for the org-scoped case (platform-wide rows have a NULL
org_id and don't collide under ordinary NULL semantics — the service is the
authority there).

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-01 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "raw_trace_grants",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "principal_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("org_id", sa.String(length=64), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("approval_mode", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.String(length=64), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by", sa.String(length=64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_approval_ref", sa.String(length=128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("ticket_ref", sa.String(length=128), nullable=True),
        sa.Column("revoked_by", sa.String(length=64), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_raw_trace_grants_principal_id", "raw_trace_grants", ["principal_id"])
    # Defense-in-depth: at most one ACTIVE org-scoped grant per (principal,
    # org). The service's activation CAS is the authority (and covers the
    # platform-wide NULL-org case, which this index cannot).
    op.create_index(
        "uq_raw_trace_grants_active_scope",
        "raw_trace_grants",
        ["principal_id", "org_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active' AND org_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_raw_trace_grants_active_scope", table_name="raw_trace_grants")
    op.drop_index("ix_raw_trace_grants_principal_id", table_name="raw_trace_grants")
    op.drop_table("raw_trace_grants")
