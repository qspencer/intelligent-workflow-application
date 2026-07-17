"""JIT user provisioning: persist IdP identities on first authenticated sight.

Per ARCHITECTURE D4 the IdP owns authentication and roles — this module never
stores credentials and never persists roles. It exists so features (resource
ownership, per-user memory) have a **stable platform user id** to reference:
on any authenticated request the identity is upserted into the `users` table,
keyed by `(iss, sub)` (sub alone is not globally unique across issuers).

The `last_seen_at` refresh is throttled: within `ttl_seconds` of the last
write for an identity, provisioning is a no-op — otherwise every request
becomes a database write.

Dev mode note: `iss` is the literal "dev" and subs come from X-Dev-User
headers, so flipping identities in the RoleSwitcher mints real user rows.
That's accepted — dev rows are cheap and the behavior mirrors production JIT.
"""

from __future__ import annotations

import logging
import os
import time

from workflow_platform.auth.identity import UserIdentity
from workflow_platform.persistence.models import DEFAULT_ORG_ID, User, _utcnow
from workflow_platform.persistence.repository import UserRepo

logger = logging.getLogger(__name__)

_DEFAULT_TTL_SECONDS = 300.0


def current_issuer() -> str:
    """The identity namespace for `(iss, sub)` keys. Single-IdP assumption:
    dev mode uses the literal "dev"; OIDC mode uses the configured issuer."""
    from workflow_platform.auth.middleware import auth_mode

    if auth_mode() == "dev":
        return "dev"
    return os.environ.get("OIDC_ISSUER", "oidc")


class UserProvisioner:
    """Best-effort JIT upsert of authenticated identities. Never raises into
    the request path — a provisioning failure must not turn into a 500."""

    def __init__(self, users: UserRepo, *, ttl_seconds: float = _DEFAULT_TTL_SECONDS) -> None:
        self._users = users
        self._ttl = ttl_seconds
        self._last_write: dict[tuple[str, str], float] = {}

    async def provision(self, identity: UserIdentity) -> None:
        iss = current_issuer()
        key = (iss, identity.sub)
        now = time.monotonic()
        if now - self._last_write.get(key, -self._ttl) < self._ttl:
            return
        try:
            await self._users.upsert_seen(
                User(
                    iss=iss,
                    sub=identity.sub,
                    email=identity.email,
                    display_name=identity.name,
                    org_id=DEFAULT_ORG_ID,
                    last_seen_at=_utcnow(),
                )
            )
            self._last_write[key] = now
        except Exception:
            logger.exception("JIT user provisioning failed for sub=%r", identity.sub)
