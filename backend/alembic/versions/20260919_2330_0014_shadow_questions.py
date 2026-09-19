"""G12 C1 shadow state (ASK_THE_USER_PLAN §3b).

Round 2 dissolved the shadow/live dilemma — *"identical rules do not
require shared mutable state"* — so C1 runs the real scheduling code
against tables of its own. These are NOT the C2 live tables and must not
become them: C2 gets its own, because the point of the separation is that
a shadow run cannot consume a live slot even by mistake.

`shadow_asked` is the permanent `(subject, topic)` duplicate-suppression
history, deliberately a different table from `shadow_questions`. Expiry
releases a question's outstanding capacity; it must NOT release the
"once ever" record, and two tables is the cheapest way to make that
impossible to get wrong (§3a).

Revision ID: 0014
Revises: 0013
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "shadow_questions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("org_id", sa.String(length=128), nullable=False),
        sa.Column("subject", sa.String(length=256), nullable=False),
        sa.Column("recipient", sa.String(length=256), nullable=False),
        sa.Column("topic", sa.String(length=128), nullable=False),
        sa.Column("catalog_revision", sa.Integer(), nullable=False),
        # The wording the user WOULD have seen, frozen at ask time (§2a).
        sa.Column("prompt_shown", sa.Text(), nullable=False),
        sa.Column("answers_offered", sa.JSON(), nullable=False),
        sa.Column("fact_mapping", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    # The capacity predicate counts pending rows for one recipient, so it
    # is the index that makes the conditional insert cheap under load —
    # which is exactly when the race it guards actually happens.
    op.create_index(
        "ix_shadow_questions_recipient_status",
        "shadow_questions",
        ["recipient", "status"],
    )
    # THE CAPACITY COUNTER, and it is a counter rather than a COUNT(*) for
    # a reason the concurrency test established rather than predicted: a
    # conditional INSERT whose predicate counts rows does NOT serialize
    # under READ COMMITTED — each transaction's subselect reads a snapshot
    # without the other's uncommitted row, so two runs both see the last
    # slot and both take it. Measured: 2 questions, cap 1.
    #
    # A conditional UPDATE of one row does serialize, because the second
    # transaction blocks on the row lock and re-evaluates its predicate
    # against the committed value. That is the "conditional counter update
    # or appropriate locking" the review named, and counting rows was not
    # it.
    op.create_table(
        "shadow_capacity",
        sa.Column("recipient", sa.String(length=256), primary_key=True),
        sa.Column("outstanding", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "shadow_asked",
        sa.Column("subject", sa.String(length=256), primary_key=True),
        sa.Column("topic", sa.String(length=128), primary_key=True),
        sa.Column("first_asked_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("shadow_asked")
    op.drop_table("shadow_capacity")
    op.drop_index("ix_shadow_questions_recipient_status", table_name="shadow_questions")
    op.drop_table("shadow_questions")
