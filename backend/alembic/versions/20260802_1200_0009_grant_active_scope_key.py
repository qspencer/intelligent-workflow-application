"""Platform-wide-covering active-grant uniqueness (external code review
2026-08-02 F5).

The 0006 partial unique index was `state = 'active' AND org_id IS NOT NULL`,
so two concurrent PLATFORM-WIDE (NULL org) activations for the same principal
could both win. Replace it with an EXPRESSION index over
`COALESCE(org_id, '__platform_wide__')`, so the platform-wide scope has a
non-NULL discriminator and the unique constraint covers it too — the
cross-process backstop for the service's in-process activation lock.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-02 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SENTINEL = "__platform_wide__"


def upgrade() -> None:
    bind = op.get_bind()
    op.drop_index("uq_raw_trace_grants_active_scope", table_name="raw_trace_grants")
    # Postgres supports expression + partial unique indexes; SQLite (dev/tests)
    # does not enforce the COALESCE expression form, so keep the plain-column
    # partial index there (the in-process lock is the test-path guard).
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                "CREATE UNIQUE INDEX uq_raw_trace_grants_active_scope "
                "ON raw_trace_grants (principal_id, COALESCE(org_id, '"
                + _SENTINEL
                + "')) WHERE state = 'active'"
            )
        )
    else:
        op.create_index(
            "uq_raw_trace_grants_active_scope",
            "raw_trace_grants",
            ["principal_id", "org_id"],
            unique=True,
            sqlite_where=sa.text("state = 'active'"),
        )


def downgrade() -> None:
    op.drop_index("uq_raw_trace_grants_active_scope", table_name="raw_trace_grants")
    op.create_index(
        "uq_raw_trace_grants_active_scope",
        "raw_trace_grants",
        ["principal_id", "org_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active' AND org_id IS NOT NULL"),
    )
