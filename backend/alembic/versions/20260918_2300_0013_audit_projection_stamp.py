"""Audit-entry projection stamp (round-12 finding 1).

A grant holder could not recover a vaulted audit detail: the endpoints handed
back the STORED entry, which after the at-rest tightening is the projection.
The fix needs a reader to know "this entry's raw is in the vault", and that
signal cannot be re-derived — the stored detail is already projected, so
asking "would projection remove anything from this?" always answers no. Nor
can it live in the payload, where an operator could delete it to make
rehydration skip the vault (the P3a argument, already settled for step rows).

So it is a column, set only when the raw was actually vaulted.

Nullable and unbackfilled: entries written before this are either pre-flip
(raw inline, nothing vaulted) or were written in the window where at-rest was
tightened but no stamp existed. The latter are the ~80 audit-detail vault
rows already in production; they remain addressable by
`audit_idempotency_key` and can be re-linked by a sweep if ever needed, but
no reader will fetch them automatically. That is deliberate: inventing a
stamp for rows we did not stamp would be a claim we cannot verify.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-18 23:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audit_log",
        sa.Column("projector_version", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audit_log", "projector_version")
