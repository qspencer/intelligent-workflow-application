"""Trace initialisation, shared by EVERY entry point that can write traces.

Round 12 finding 2: `main.py` installed the vault master key from Secrets
Manager at startup; the six operator tools that build a `WorkflowEngine` did
not. `tools/fire.py` had just been taught to read the safe-only flip, so a
CLI run against production enabled safe-only, found no key, and wrote
**plaintext vault rows** — six of them reached our own production database
before the reviewer reproduced it. An encryption-enabled reader then rejects
those rows as a downgrade, so the raw is unreadable by anyone.

This is the same shape as the flip itself (`trace_flip`): a control enforced
at one entry point and absent at the others is not enforced. So both live in
one place and `test_trace_flip_is_enforced_everywhere` requires every engine
construction to use them.

**Fail closed.** A CONFIGURED key that cannot be resolved stops the process
before any trace write. The alternative — carry on unencrypted — silently
downgrades the contract precisely when the operator asked for it.
"""

from __future__ import annotations

import logging
import os

from workflow_platform.secrets import AwsSecretsManagerStore
from workflow_platform.trace_cipher import (
    ENV_MASTER_KEY,
    ENV_MASTER_KEY_SECRET,
    install_master_key,
)

logger = logging.getLogger(__name__)


class TraceKeyUnavailable(RuntimeError):
    """A master key is configured but could not be resolved. Fatal by design."""


def install_trace_master_key() -> bool:
    """Resolve and install the vault master key. Returns True if one is now
    installed from the secret manager, False if none is configured.

    Raises `TraceKeyUnavailable` when a secret IS named but cannot be
    fetched — never returns quietly, because the caller would then write
    plaintext under a configuration that asked for encryption.
    """
    secret_name = os.environ.get(ENV_MASTER_KEY_SECRET)
    if not secret_name:
        return False
    try:
        # SYNC boto3 on purpose: `create_app` runs inside uvicorn's running
        # loop, so asyncio.run() is unavailable at this call site.
        response = AwsSecretsManagerStore().client.get_secret_value(SecretId=secret_name)
    except Exception as exc:
        raise TraceKeyUnavailable(
            f"{ENV_MASTER_KEY_SECRET}={secret_name!r} is configured but the secret could "
            f"not be read ({exc.__class__.__name__}). Refusing to continue: trace writes "
            "would be unencrypted under a configuration that requires encryption."
        ) from exc
    install_master_key(str(response["SecretString"]))
    logger.info("raw-trace master key installed from secret manager")
    return True


def init_tracing() -> None:
    """The one call every entry point makes before it can write traces.

    Safe to call more than once; installing the same key twice is a no-op in
    effect. Entry points that legitimately run without encryption (tests,
    replay against a mock world) simply have neither variable set.
    """
    installed = install_trace_master_key()
    if not installed and not os.environ.get(ENV_MASTER_KEY):
        logger.debug("no raw-trace master key configured; vault writes are plaintext")
