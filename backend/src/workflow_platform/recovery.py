"""Boot-time recovery of instances a previous process left RUNNING.

`WorkflowEngine._drive_inner` marks an interrupted run PAUSED on its way out,
so an orderly shutdown no longer strands anything. That handler cannot run
when the process does not get to run anything — SIGKILL, OOM, a host reset —
and it did not exist before 2026-09-19, so the table already held 171
stranded rows, the oldest from July.

Both cases look identical at boot and have the same answer: nothing is
executing, so an instance still marked RUNNING is not running. The sweep
turns it into the state the in-process handler would have left, which makes
it visible, resumable, and no longer a permanent `alert_stuck_workflow`
source.

SINGLE-PROCESS ASSUMPTION, stated because it is load-bearing. "Nothing is
executing" is true of this deployment and false of a multi-process one,
where these rows may belong to a peer that is mid-run. `EXECUTION_SEMANTICS`
§7 already records that recovery here is restart-triggered rather than
lease-arbitrated, and names leases (G21) as the prerequisite for horizontal
scale. This sweep is part of what G21 has to replace, not an obstacle to it:
`WORKFLOW_PLATFORM_DISABLE_BOOT_RECOVERY=1` turns it off for an operator who
gets there first.
"""

from __future__ import annotations

import logging
import os

from workflow_platform.audit_writer import AuditWriter
from workflow_platform.persistence import (
    Repositories,
    StepExecutionState,
    WorkflowInstanceState,
)
from workflow_platform.persistence.models import _utcnow

logger = logging.getLogger(__name__)

DISABLE_ENV_VAR = "WORKFLOW_PLATFORM_DISABLE_BOOT_RECOVERY"

#: Upper bound on one sweep. A cap rather than an unbounded scan, because the
#: sweep runs inside the app's startup path: a pathological table must delay
#: boot by a bounded amount and get finished by the next one, not hold the
#: service down.
SWEEP_LIMIT = 5000

INTERRUPTED_ERROR = "interrupted: process exited while running (recovered at boot)"


async def sweep_interrupted_instances(
    repositories: Repositories,
    audit_writer: AuditWriter,
    *,
    limit: int = SWEEP_LIMIT,
) -> int:
    """Mark every still-RUNNING instance PAUSED, and its RUNNING steps
    CANCELLED. Returns how many instances were recovered.

    Never raises: this runs in the startup path, and a service that refuses
    to boot because a recovery scan failed is a worse outcome than one that
    boots with the rows still stranded. Failures are logged per instance so
    one bad row does not abort the rest.
    """
    if os.environ.get(DISABLE_ENV_VAR, "").lower() in ("1", "true", "yes"):
        logger.info("boot recovery sweep disabled by %s", DISABLE_ENV_VAR)
        return 0

    try:
        stranded = await repositories.instances.list_by_state(
            [WorkflowInstanceState.RUNNING.value], limit=limit
        )
    except Exception:
        logger.exception("boot recovery sweep could not list RUNNING instances")
        return 0

    recovered = 0
    for instance in stranded:
        try:
            # Steps first: an instance that is PAUSED while a step row still
            # says RUNNING is the same lie in a smaller place, and resume
            # keys off step state (CANCELLED re-runs, RUNNING does not exist
            # as a resumable state).
            for execution in await repositories.steps.list_by_instance(instance.id):
                if execution.state is StepExecutionState.RUNNING:
                    execution.state = StepExecutionState.CANCELLED
                    execution.completed_at = _utcnow()
                    await repositories.steps.update(execution)

            instance.state = WorkflowInstanceState.PAUSED
            instance.error = INTERRUPTED_ERROR
            instance.completed_at = None
            await repositories.instances.update(instance)
            # Same action the in-process handler writes, distinguished by
            # actor: one observed the cancellation, the other inferred it.
            await audit_writer.append(
                "workflow_interrupted",
                actor_type="recovery",
                actor_id="boot_recovery_sweep",
                instance_id=instance.id,
            )
            recovered += 1
        except Exception:
            logger.exception("boot recovery sweep failed for instance %s; continuing", instance.id)

    if recovered:
        logger.warning(
            "boot recovery: %d instance(s) were left RUNNING by a previous process "
            "and are now PAUSED (resumable)",
            recovered,
        )
    return recovered
