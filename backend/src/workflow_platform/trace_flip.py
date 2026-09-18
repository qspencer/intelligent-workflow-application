"""The ONE reader of the safe-only flip (TG3b).

Found 2026-09-18: `main.py` read `WORKFLOW_PLATFORM_TRACE_SAFE_ONLY` and every
operator tool that builds a `WorkflowEngine` did not, so `tools/fire.py` and
friends ran workflows against the PRODUCTION database with trace governance
off — writing unprojected raw into the operational tables the service is
careful to keep projected. A security control enforced in one entry point and
absent in five others is not enforced.

So the flip is read here and nowhere else, and
`test_trace_flip_is_read_from_one_place` enumerates the engine constructions
that must use it.
"""

from __future__ import annotations

import os

ENV_VAR = "WORKFLOW_PLATFORM_TRACE_SAFE_ONLY"

#: Accepted truthy spellings. Anything else — including "on" — is OFF, and
#: deliberately so: a control that guesses at intent is worse than one that
#: requires the documented value.
_TRUTHY = ("1", "true", "yes")


def trace_safe_only_from_env() -> bool:
    """Whether the safe-only flip is on for this process.

    When on, the operational store persists only the safe projection and raw
    lives in the vault (rehydrated on resume/fork). Default OFF (dark
    dual-write). See docs/TRACE_GOVERNANCE_PLAN.md §4.
    """
    return os.environ.get(ENV_VAR, "").lower() in _TRUTHY
