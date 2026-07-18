#!/usr/bin/env python3
"""One-shot migration of the learned-memory (veracium) store to org-aware
namespaced keys (docs/ROLES_PLAN.md §9): `<key>` → `org:default:user:<key>`.

Everything in the store predates multi-tenancy, so every existing key belongs
to the default org. Idempotent: keys already namespaced are skipped.

Usage:
    uv run python tools/migrate_learned_namespace.py [--db PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

TABLES = ["edges", "episodes", "wiki", "write_counter"]
DEFAULT_DB = Path(__file__).resolve().parent.parent / ".memory" / "learned.db"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"no store at {db}; nothing to migrate")
        return 0
    conn = sqlite3.connect(db)
    try:
        total = 0
        for table in TABLES:
            (count,) = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE user_id NOT LIKE 'org:%'"
            ).fetchone()
            print(f"{table:14s}: {count} row(s) to namespace")
            total += count
            if not args.dry_run and count:
                conn.execute(
                    f"UPDATE {table} SET user_id = 'org:default:user:' || user_id "
                    "WHERE user_id NOT LIKE 'org:%'"
                )
        if args.dry_run:
            print(f"dry run: {total} row(s) would be rewritten")
        else:
            conn.commit()
            print(f"done: {total} row(s) rewritten")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
