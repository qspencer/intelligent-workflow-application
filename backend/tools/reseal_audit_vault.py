"""Re-seal audit-detail vault rows under the entry-bound AEAD identity.

Round-12 finding 3: audit ciphertext was sealed without `audit_entry_id` in
the associated data, so every audit row in an instance shared one identity
and entry B's content opened under entry A's id. The binding is now part of
the AAD.

**Compatibility policy, stated explicitly (the return asked for one):** there
is NO silent legacy-AAD fallback on the read path. A fallback would keep
accepting exactly the substitution the binding closes, for precisely the rows
an attacker would target. So rows sealed before the change do not open, and
this tool migrates them: open with the legacy AAD, re-seal with the bound
one. Plaintext rows (written by a CLI run before trace init was shared —
finding 2) are sealed for the first time.

Rows that open under NEITHER are left untouched and reported; they are not
guessed at.

Usage (from backend/), dry run first:
    DATABASE_URL=... WORKFLOW_PLATFORM_TRACE_MASTER_KEY_SECRET=... \
        uv run python tools/reseal_audit_vault.py
    ... --apply        # actually write
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os

from workflow_platform.persistence.db import make_engine, make_session_factory
from workflow_platform.persistence.models import RAW_SCHEMA_VERSION, RawTraceKind
from workflow_platform.persistence.postgres import postgres_repositories
from workflow_platform.trace_bootstrap import init_tracing
from workflow_platform.trace_cipher import build_trace_cipher, is_sealed_payload


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write; otherwise dry run")
    ap.add_argument("--limit", type=int, default=100_000)
    args = ap.parse_args()

    init_tracing()
    cipher = build_trace_cipher()
    if cipher is None:
        print("No master key available — nothing to re-seal (plaintext vault).")
        return 1

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is required.")
        return 1
    engine = make_engine(url)
    repos = postgres_repositories(make_session_factory(engine))

    already = migrated = plaintext_sealed = unopenable = 0
    instances = await repos.instances.list_recent(limit=args.limit)
    for inst in instances:
        for row in await repos.raw_trace_vault.list_by_instance(inst.id):
            if row.kind is not RawTraceKind.AUDIT_DETAIL or row.audit_entry_id is None:
                continue
            ident = {
                "org_id": row.org_id,
                "instance_id": row.instance_id,
                "step_attempt_id": None,
                "kind": row.kind.value,
                "schema_version": RAW_SCHEMA_VERSION,
            }
            if not is_sealed_payload(row.payload):
                plaintext = row.payload
                plaintext_sealed += 1
            else:
                try:  # already bound?
                    cipher.open(row.payload, **ident, audit_entry_id=row.audit_entry_id)
                    already += 1
                    continue
                except Exception:
                    pass
                try:  # legacy, unbound
                    plaintext = cipher.open(row.payload, **ident)
                    migrated += 1
                except Exception:
                    unopenable += 1
                    print(f"  UNOPENABLE {row.id} (entry {row.audit_entry_id}) — left as-is")
                    continue
            if args.apply:
                resealed = cipher.seal(plaintext, **ident, audit_entry_id=row.audit_entry_id)
                # `reseal`, NOT `put`: put is idempotent on the natural key,
                # so it returns the existing row and the update silently does
                # nothing. The first version of this tool did exactly that and
                # would have reported success while changing nothing.
                commitment = hashlib.sha256(
                    json.dumps(plaintext, sort_keys=True, default=str).encode()
                ).hexdigest()
                if not await repos.raw_trace_vault.reseal(
                    row.id, payload=resealed, content_commitment=commitment
                ):
                    print(f"  VANISHED {row.id} — row disappeared mid-run, skipped")

    verb = "re-sealed" if args.apply else "would re-seal"
    print(
        f"\n{verb}: {migrated} legacy-AAD, {plaintext_sealed} plaintext; "
        f"{already} already bound; {unopenable} unopenable."
    )
    if not args.apply:
        print("Dry run — pass --apply to write.")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
