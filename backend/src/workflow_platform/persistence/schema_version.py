"""Schema-drift detection (NEXT_STEPS G26.1).

The service runs under uvicorn `--reload`, which hot-loads changed Python but
does NOT re-run `alembic upgrade head`. Committing a migration and letting
`--reload` pick up the code that references the new column leaves the **code
ahead of the schema**, and every write then fails with `UndefinedColumnError`.

That has happened twice, the second time breaking every workflow run on the
deployed box for four days — silently, because nothing compared the two. Tests
cannot catch it: they build a fresh schema from the models.

So the comparison is made explicit and cheap:

- `expected_revisions()` reads the migration scripts on disk (`alembic heads`).
- `current_revision()` reads `alembic_version` from the live database.
- `check_schema_drift()` returns a verdict the callers can act on — surfaced by
  `/api/health` and asserted as a pre-flight by the operator one-shots, so drift
  fails fast and loudly instead of once per row.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# backend/src/workflow_platform/persistence/ → backend/
_BACKEND_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class SchemaStatus:
    """`ok` is the only state a caller should proceed on. `unknown` means we
    could not determine it (no DB configured, alembic unreadable) — reported,
    never silently treated as fine."""

    state: str  # "ok" | "drift" | "unknown"
    current: str | None
    expected: list[str]
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.state == "ok"


def expected_revisions(script_location: Path | None = None) -> list[str]:
    """The head revision(s) of the migration scripts on disk — what the CODE
    expects. Empty list if alembic isn't importable or configured."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
    except Exception:  # pragma: no cover - alembic is a hard dep in practice
        return []
    root = script_location or _BACKEND_ROOT
    ini = root / "alembic.ini"
    if not ini.exists():
        return []
    try:
        cfg = Config(str(ini))
        cfg.set_main_option("script_location", str(root / "alembic"))
        return list(ScriptDirectory.from_config(cfg).get_heads())
    except Exception:
        logger.warning("could not read alembic heads", exc_info=True)
        return []


async def current_revision(session_factory: Any) -> str | None:
    """The revision stamped in the live database, or None if the table is
    absent (never migrated)."""
    from sqlalchemy import text

    async with session_factory() as s:
        result = await s.execute(text("SELECT version_num FROM alembic_version"))
        row = result.first()
        return str(row[0]) if row else None


async def check_schema_drift(session_factory: Any | None) -> SchemaStatus:
    """Compare the DB's stamped revision against the migrations on disk.

    In-memory / no-DB deployments have no schema to drift, so they report `ok`
    with an explanatory detail rather than a scary `unknown`."""
    expected = expected_revisions()
    if session_factory is None:
        return SchemaStatus("ok", None, expected, "no database configured (in-memory repositories)")
    if not expected:
        return SchemaStatus("unknown", None, [], "could not read alembic heads")
    try:
        current = await current_revision(session_factory)
    except Exception as exc:
        return SchemaStatus("unknown", None, expected, f"could not read alembic_version: {exc}")
    if current is None:
        return SchemaStatus("drift", None, expected, "database has never been migrated")
    if current in expected:
        return SchemaStatus("ok", current, expected)
    return SchemaStatus(
        "drift",
        current,
        expected,
        f"database at {current}, code expects {'/'.join(expected)} — run `alembic upgrade head` "
        "(or restart the service, whose startup script does)",
    )


async def assert_schema_current(session_factory: Any | None) -> None:
    """Pre-flight for operator one-shots (G26.1): refuse to run against a
    database whose revision is behind the migrations this code was built for.

    Without it the failure surfaces once per row as an opaque
    `UndefinedColumnError` — which is exactly how four days of failed runs went
    unnoticed. An inconclusive check WARNS rather than exits: not being able to
    tell is not the same as being broken, but it must still be said."""
    status = await check_schema_drift(session_factory)
    if status.state == "drift":
        raise SystemExit(f"ERROR  schema drift — refusing to run: {status.detail}")
    if status.state == "unknown":
        print(f"WARN   schema check inconclusive: {status.detail}")
