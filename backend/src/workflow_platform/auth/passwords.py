"""Password hashing for `AUTH_MODE=local` (docs/AUTH_PLAN.md §5).

Argon2id with the library's current defaults. The helpers are synchronous
and CPU-bound by design — callers on the event loop wrap them in
`asyncio.to_thread`.
"""

from __future__ import annotations

import contextlib

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """PHC-format Argon2id hash."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> tuple[bool, bool]:
    """Return `(ok, needs_rehash)`. `needs_rehash` is True when the stored
    hash predates the current parameters — the caller re-hashes on a
    successful login so parameter upgrades roll forward automatically."""
    try:
        _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False, False
    return True, _hasher.check_needs_rehash(password_hash)


def dummy_verify() -> None:
    """Burn comparable CPU on the unknown-email login path so response
    timing doesn't distinguish 'no such user' from 'wrong password'
    (AUTH_PLAN §9.1)."""
    with contextlib.suppress(VerifyMismatchError):
        _hasher.verify(_DUMMY_HASH, "not-the-password")


def _make_dummy_hash() -> str:
    return _hasher.hash("dummy")


_DUMMY_HASH = _make_dummy_hash()
