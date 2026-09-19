"""Directory resolution for subject references (G-Trace-Subject-Identity
stage 3; `docs/TRACE_SUBJECT_IDENTITY_PLAN.md` §6).

The reviewer's specification, and every clause of it is load-bearing:

> Display resolution is server-side, under explicit DIRECTORY permissions,
> **independent of raw-trace grants**. Request-level access records,
> batch-friendly — not one event per rendered label. Record identifiers and
> outcomes, **never copy display identities into the records**.

- **Independent of raw-trace grants.** `DIRECTORY_ROLES` below is its own
  named permission, deliberately not `has_raw_trace_grant`. Reading a
  person's name is a different question from reading a message body, and
  tying them together would mean either over-granting the first or
  making the second routine.
- **Batch and request-level.** One `directory_resolved` entry per REQUEST,
  carrying counts and per-ref outcomes. A per-label event would make the
  audit log a record of what a UI happened to render.
- **Never copy display identities into the records.** The audit entry
  carries refs and outcomes. The names go in the RESPONSE and nowhere else
  — a directory lookup that wrote names into the audit log would put the
  identities back into the store the subject reference exists to keep them
  out of.

WHAT RESOLVES, AND WHAT HONESTLY CANNOT. A `platform_user` ref is
`users.id` and resolves from the users table. A `mailbox` ref is an HMAC
and is **not reversible** — and there is deliberately no mapping table to
reverse it with (plan §4: a table that survived renames would be a second
identity that drifts from the memory store's own). So a mailbox ref
resolves to `unresolvable`, with the reason stated: the address lives in
the raw-trace vault, which is grant-gated rather than directory-gated.
That is not a gap in this endpoint — it is the boundary between the two
permissions doing its job.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from workflow_platform.audit_writer import AuditWriter
from workflow_platform.auth.identity import UserIdentity
from workflow_platform.auth.rbac import Role, require_roles
from workflow_platform.persistence import Repositories
from workflow_platform.trace_flip import trace_safe_only_from_env

#: THE DIRECTORY PERMISSION. Its own constant so the grant it is NOT is
#: visible at the call site: holding a raw-trace grant does not confer it,
#: and holding it does not confer a raw-trace grant.
DIRECTORY_ROLES = (Role.ADMINISTRATOR, Role.ORG_ADMIN)

#: Bound on one request. A directory endpoint with no bound is an
#: enumeration endpoint.
MAX_REFS = 200

RESOLVED = "resolved"
NOT_FOUND = "not_found"
NOT_RESOLVABLE = "not_resolvable_by_directory"


class ResolveRequest(BaseModel):
    refs: list[str] = Field(default_factory=list)


def build_directory_router(repositories: Repositories) -> APIRouter:
    router = APIRouter(prefix="/api")
    audit = AuditWriter(repositories, trace_safe_only=trace_safe_only_from_env())

    @router.post("/directory/resolve")
    async def resolve(
        body: ResolveRequest,
        actor: UserIdentity = Depends(require_roles(*DIRECTORY_ROLES)),
    ) -> dict[str, Any]:
        refs = list(dict.fromkeys(body.refs))  # de-duplicated, order kept
        if len(refs) > MAX_REFS:
            raise HTTPException(status_code=400, detail=f"At most {MAX_REFS} refs per request")

        results: list[dict[str, Any]] = []
        for ref in refs:
            if ref.startswith("subj:"):
                # A mailbox pseudonym. Not reversible, and no table exists
                # to reverse it with — by design, not by omission.
                results.append({"ref": ref, "outcome": NOT_RESOLVABLE})
                continue
            user = await repositories.users.get(ref)
            if user is None:
                results.append({"ref": ref, "outcome": NOT_FOUND})
                continue
            results.append(
                {
                    "ref": ref,
                    "outcome": RESOLVED,
                    "display_name": user.display_name or user.email,
                    "org_id": user.org_id,
                }
            )

        outcomes: dict[str, int] = {}
        for row in results:
            outcomes[row["outcome"]] = outcomes.get(row["outcome"], 0) + 1
        # IDENTIFIERS AND OUTCOMES, never the names that were returned.
        await audit.append(
            "directory_resolved",
            actor_type="human",
            actor_id=actor.sub,
            detail={
                "requested": len(refs),
                "resolved": outcomes.get(RESOLVED, 0),
                "not_found": outcomes.get(NOT_FOUND, 0),
                "not_resolvable": outcomes.get(NOT_RESOLVABLE, 0),
            },
        )
        return {"results": results}

    return router
