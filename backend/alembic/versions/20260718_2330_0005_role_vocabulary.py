"""Tenant-scoped role vocabulary (docs/ROLES_PLAN.md S1): rewrite users.roles.

Old (global) → new (tenant-scoped): Admin → Administrator; Workflow Designer
and Operator → Organization User; Viewer and Auditor → Organization Viewer.

Downgrade applies the inverse with documented loss: the Designer/Operator and
Viewer/Auditor distinctions cannot be reconstructed (Organization User →
Workflow Designer, Organization Viewer → Viewer), and Organization
Administrator — which has no five-role equivalent — becomes Admin.
Acceptable: no production tenant predates this migration.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-18 23:30:00
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FORWARD = {
    "Admin": "Administrator",
    "Workflow Designer": "Organization User",
    "Operator": "Organization User",
    "Viewer": "Organization Viewer",
    "Auditor": "Organization Viewer",
}
_BACKWARD = {
    "Administrator": "Admin",
    "Organization Administrator": "Admin",
    "Organization User": "Workflow Designer",
    "Organization Viewer": "Viewer",
}


def _rewrite(mapping: dict[str, str]) -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, roles FROM users")).fetchall()
    for user_id, roles in rows:
        parsed = roles if isinstance(roles, list) else json.loads(roles)
        rewritten = sorted({mapping.get(r, r) for r in parsed})
        if rewritten != sorted(parsed):
            conn.execute(
                sa.text("UPDATE users SET roles = CAST(:roles AS jsonb) WHERE id = :id"),
                {"roles": json.dumps(rewritten), "id": user_id},
            )


def upgrade() -> None:
    _rewrite(_FORWARD)


def downgrade() -> None:
    _rewrite(_BACKWARD)
