"""Vault content commitment (P4 — external code re-review 2026-08-03 finding 7).

`put` was idempotent on `idempotency_key` alone: an existing row was returned as
success WITHOUT checking its content, so a durable write could believe it stored
new raw while the vault retained an older payload. Ciphertext cannot be compared
directly (a fresh nonce per seal), so the vault now commits to a sha256 of the
PLAINTEXT and the repository compares that. NULL on pre-P4 rows, which fall back
to comparing the payload (correct for the plaintext Contract-A vault).

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-03 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "raw_traces",
        sa.Column("content_commitment", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("raw_traces", "content_commitment")
