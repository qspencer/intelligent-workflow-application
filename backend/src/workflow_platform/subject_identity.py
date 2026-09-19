"""Typed subject identity for audit `user_id` (G-Trace-Subject-Identity,
stages 1-2; `docs/TRACE_SUBJECT_IDENTITY_PLAN.md`).

A subject is WHO an audit entry is ABOUT, as distinct from the actor who
caused it. The reviewer's constraint, and the reason this is a module
rather than a helper: *"Give those subjects their own typed identity
instead of assuming every `user_id` joins to `users`."* On this deployment
that is 6,351 of 6,354 rows — the memory-namespace keys are mailbox
addresses, not platform users.

TWO CONSTRUCTORS, NAMED BY SOURCE, and deliberately not one that sniffs the
value. Shape cannot tell a platform user from a correspondent: a `users.id`
and an opaque namespace key are both just tokens, and the whole ownership
registry exists because shape bounds damage rather than establishing
provenance. The CALLER knows what it holds; it says so.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

#: HMAC key for mailbox pseudonyms. From the environment, never the repo —
#: the pattern `_sampling_bucket` already uses. Unset (dev, tests) means no
#: pseudonym is minted at all rather than an unkeyed hash: an unkeyed digest
#: of a small address space is reversible by anyone who can guess an
#: address, which is exactly the disclosure the pseudonym exists to avoid.
KEY_ENV_VAR = "WORKFLOW_PLATFORM_SUBJECT_KEY"

#: Namespace shape written by `memory.memory_namespace`.
_NAMESPACE_PREFIX = "org:"
_NAMESPACE_MARKER = ":user:"


class SubjectKind(StrEnum):
    """Closed set. `UNKNOWN` is a classification, not a failure — a
    namespace key that is neither a platform user nor an address is a real
    case, and calling it unknown is more honest than guessing."""

    PLATFORM_USER = "platform_user"
    MAILBOX = "mailbox"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SubjectRef:
    """`{kind, ref, org_id}`.

    `org_id` is carried ON THE REFERENCE and recorded on the event, never
    inferred from `ref` — the reviewer's first constraint, because
    `users.id` survives an org transfer and so is not by itself a
    tenant-scoped identity.

    `ref` is None when no stable reference can be minted (an unknown
    subject, or a mailbox with no key configured). None is the honest
    answer; a placeholder would be a correlator that correlates wrongly.
    """

    kind: SubjectKind
    ref: str | None
    org_id: str

    def as_detail(self) -> dict[str, Any]:
        """The audit-detail shape. `ref` is omitted when absent rather than
        emitted as null, so a reader cannot mistake "not mintable" for a
        subject that happens to be called nothing."""
        out: dict[str, Any] = {"kind": self.kind.value, "org_id": self.org_id}
        if self.ref is not None:
            out["ref"] = self.ref
        return out


def _pseudonym(normalized_key: str, org_id: str) -> str | None:
    """`HMAC(key, address || org)`, truncated.

    KEYED OVER THE ORG, not the address alone (plan §3). Keyed over the
    address alone the pseudonym is a CROSS-TENANT JOIN KEY: two orgs
    corresponding with the same person would mint the same ref, and a
    reader entitled to one org's audit could correlate into another's.
    Tenant isolation is an invariant (THREAT_MODEL §5), so the correlator
    has to respect it.
    """
    secret = os.environ.get(KEY_ENV_VAR, "")
    if not secret:
        return None
    digest = hmac.new(secret.encode(), f"{normalized_key}||{org_id}".encode(), hashlib.sha256)
    return "subj:" + digest.hexdigest()[:24]


def subject_from_namespace(namespace: str) -> SubjectRef:
    """Classify a learned-memory namespace (`org:<org>:user:<key>`).

    The org comes out of the namespace because that is where the writer put
    it; the key is a mailbox address in production. Normalization is NOT
    re-implemented — `memory.normalize_entity` already folds case and
    strips plus-addressing, and a pseudonym that split on `User+Tag@x`
    when the memory store does not would make the two disagree about who
    the subject is.
    """
    from workflow_platform.memory import normalize_entity

    org_id, key = _split_namespace(namespace)
    if key is None:
        return SubjectRef(SubjectKind.UNKNOWN, None, org_id)
    normalized = normalize_entity(key)
    if normalized.count("@") != 1:
        return SubjectRef(SubjectKind.UNKNOWN, None, org_id)
    return SubjectRef(SubjectKind.MAILBOX, _pseudonym(normalized, org_id), org_id)


def subject_from_user_id(user_id: str, *, org_id: str) -> SubjectRef:
    """A platform user. `users.id` is already opaque and already stable
    across a rename, so it is the ref as-is — no pseudonym, because
    pseudonymizing an id that the directory must resolve anyway would buy
    nothing and cost the resolution."""
    return SubjectRef(SubjectKind.PLATFORM_USER, user_id, org_id)


def _split_namespace(namespace: str) -> tuple[str, str | None]:
    """`org:<org>:user:<key>` -> `(org, key)`; `(namespace, None)` when it
    is not that shape. `<key>` may itself contain `:`, so the split is on
    the FIRST marker."""
    if not namespace.startswith(_NAMESPACE_PREFIX):
        return namespace, None
    rest = namespace[len(_NAMESPACE_PREFIX) :]
    marker = rest.find(_NAMESPACE_MARKER)
    if marker < 0:
        return namespace, None
    org = rest[:marker]
    key = rest[marker + len(_NAMESPACE_MARKER) :]
    return (org or "default"), (key or None)
