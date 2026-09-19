"""The ONE audit-append chokepoint: project at rest, vault what projection
would take away, then append.

Why it is a module rather than an engine method. The chokepoint was built on
`WorkflowEngine`, so it protected the engine's writes and nothing else. Every
other component reached `repositories.audit.append` directly, and one of them
— `MonitoringService` — emits continuously in production. The v12 ownership
registry made the consequence legible rather than theoretical: it classified
`alert_stale_trigger.account` as WITHHELD, the read path honoured that, and
the row in the table still read
`{"account": "qrsconsulting@quentinspencer.com", ...}`. A rule enforced on one
surface is a rule the other surface does not have.

This is the M3 class (two implementations of one policy) meeting the M9 class
(a path built from one end). The fix for both is the same: one implementation,
used by every caller, with a detector that fails the build when a new caller
appears.

THE INSTANCE-LESS RULE. The vault is instance-scoped — `raw_traces` rows are
addressed by `(org, instance, ...)` and their AEAD identity binds both — so an
entry with no instance has nowhere to put raw. Rather than widen the vault to
an org-level space (a migration, a second key space, and a second rehydration
path, all for the handful of writers that need it), the rule runs the other
way: **an instance-less audit entry must carry a projection-lossless detail.**
That is not a restriction in practice. An instance-less entry describes the
SYSTEM, not a run, so its detail is thresholds, counts and ids — and where it
did carry something else, the value was available elsewhere under the same
authorization (`alert_stale_trigger`'s mailbox address is a field of the
workflow definition, which the reader can already fetch).

Enforced, not hoped: an instance-less entry whose detail would lose something
RAISES here. Silently projecting it away is the one outcome that must not
happen, because the raw is then gone with nothing recording that it existed.
"""

from __future__ import annotations

import logging
from typing import Any

from workflow_platform.events import EventBus
from workflow_platform.persistence import AuditEntry, Repositories
from workflow_platform.persistence.models import _new_id
from workflow_platform.trace_projection import PROJECTOR_VERSION, project_audit_detail_at_rest
from workflow_platform.trace_vault import RawTraceVault, audit_detail_has_raw

logger = logging.getLogger(__name__)

#: Bound on the instance -> org memo. Cleared wholesale rather than evicted
#: one at a time: the cache exists to spare a lookup per audit write within a
#: run, and an LRU would be machinery for a saving that does not need it.
AUDIT_ORG_CACHE_MAX = 4096


class InstanceLessRawAudit(ValueError):
    """An instance-less audit entry carries raw that projection would remove.

    Its own type because the monitoring loop must be able to swallow it (one
    bad alert may not stop monitoring) while still refusing to write the
    entry, and a bare `ValueError` cannot be caught that narrowly.
    """


class AuditWriter:
    """Append audit entries under the trace-governance contract.

    `trace_safe_only` is passed in rather than read here so the flip keeps
    exactly one reader (`trace_flip.trace_safe_only_from_env`) — the control
    that was enforced in one entry point and absent in five others.
    """

    def __init__(
        self,
        repositories: Repositories,
        *,
        events: EventBus | None = None,
        trace_safe_only: bool = False,
        vault: RawTraceVault | None = None,
    ) -> None:
        self._repos = repositories
        #: PUBLIC and reassignable: the engine's `events` is a dataclass
        #: field callers set after construction, so the writer is told each
        #: time rather than snapshotting a bus that was None at build time.
        self.events = events
        self._trace_safe_only = trace_safe_only
        # Shared when the caller already has one (the engine does), so a
        # process holds one cipher and one vault rather than one per writer.
        self._vault = vault if vault is not None else RawTraceVault(repositories)
        self._org_cache: dict[str, str] = {}

    async def org_for(self, instance_id: str) -> str:
        """Owning org of the instance an audit entry belongs to, for the vault.

        FAILS CLOSED. Guessing `DEFAULT_ORG_ID` here would write another
        tenant's raw under the default org — an isolation violation, and a
        silent one. Every engine audit write happens after the instance row
        exists, so an unresolvable id is a bug; raising makes it the step's
        failure rather than a quietly misfiled vault row. Only reached when
        there is something to vault.
        """
        cached = self._org_cache.get(instance_id)
        if cached is not None:
            return cached
        instance = await self._repos.instances.get(instance_id)
        if instance is None:
            raise ValueError(f"cannot resolve the owning org of instance {instance_id!r}")
        if len(self._org_cache) >= AUDIT_ORG_CACHE_MAX:
            self._org_cache.clear()
        self._org_cache[instance_id] = instance.org_id
        return instance.org_id

    async def append(
        self,
        action: str,
        *,
        actor_type: str,
        actor_id: str,
        instance_id: str | None = None,
        step_id: str | None = None,
        detail: dict[str, Any] | None = None,
        entry_id: str | None = None,
    ) -> None:
        """Append one audit entry, vaulting its raw first under the flip.

        `entry_id` makes a RETRY of the same logical write addressable. R12
        finding 5: without it every call minted a fresh id, so re-driving a
        write whose append had failed produced a SECOND vault row and one
        entry — an orphan per attempt. Passing the id the first attempt used
        re-addresses the same vault object.

        Omitting it means "this is a new logical event", which is the right
        default: two identical tool calls on one step attempt are two events
        and must not collapse onto one row (criterion 1).
        """
        raw_detail = dict(detail or {})
        stored_detail: dict[str, Any] = raw_detail
        # R12 finding 1: set when the raw is vaulted, so a reader knows to
        # fetch it. The stored detail is already projected, so "would
        # projection remove anything from this?" always answers no — the
        # signal has to be persisted, not re-derived. Same reasoning as P3a
        # for step rows, and it must be a COLUMN so deleting a payload marker
        # cannot make rehydration skip the vault.
        vaulted_version: str | None = None
        # The entry id is minted HERE, before any I/O, because the vault row is
        # addressed BY it. Ordering matters and is the reverse of what this
        # method used to do (project, then construct): mint id -> vault the raw,
        # durable-or-fail -> project -> append. Vaulting first is the same rule
        # the step-output path follows — a lost raw write must FAIL the step,
        # not silently drop the raw.
        entry_id = entry_id or _new_id()
        # F4 (G-Trace-Review-4): under the flip EVERY raw audit write is projected
        # at rest — retry `str(exc)`, connector/timeout exceptions, memory-recall
        # errors, pin-override params, tool_call input/result. One shared
        # action-aware projection, the same the verifier uses, so no raw lands in
        # `audit_log.detail` and operational metadata is preserved.
        if self._trace_safe_only:
            stored_detail = project_audit_detail_at_rest(action, raw_detail)
            # Vault BEFORE the append, and ONLY when something would be lost.
            # Scoped by the FINAL policy, NOT by the at-rest diff above: today's
            # at-rest denylist passes the motivating detail through UNCHANGED,
            # so gating on it would vault nothing and the raw would be destroyed
            # the moment at-rest tightens. Round 11 named this trap; gating on
            # the at-rest diff is precisely how one walks into it.
            if audit_detail_has_raw(action, raw_detail):
                if instance_id is None:
                    # The vault is instance-scoped, so there is nowhere safe to
                    # put this raw. Fail rather than project it away silently.
                    raise InstanceLessRawAudit(
                        f"audit action {action!r} carries raw that projection would "
                        "remove, but the entry has no instance to vault it against"
                    )
                await self._vault.record_audit_detail(
                    org_id=await self.org_for(instance_id),
                    instance_id=instance_id,
                    audit_entry_id=entry_id,
                    action=action,
                    detail=raw_detail,
                )
                vaulted_version = PROJECTOR_VERSION
        entry = AuditEntry(
            id=entry_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            workflow_instance_id=instance_id,
            step_id=step_id,
            detail=stored_detail,
            projector_version=vaulted_version,
        )
        await self._repos.audit.append(entry)
        if self.events is not None:
            await self.events.publish(entry.model_dump(mode="json"))
