"""Trusted-header authentication policy (EMAIL_TRIAGE_CODIFY_PLAN §6).

Evaluates Gmail's own `Authentication-Results` verdict for a message:

- Only AR entries whose authserv-id is Google's receiving infrastructure
  (`mx.google.com`) are considered, and only the FIRST such entry
  (top-to-bottom) — Gmail prepends its verdict, so a matching entry deeper
  in the header block is attacker-supplied and ignored.
- `dmarc=pass` suffices (DMARC already enforces From alignment).
- Absent DMARC, an aligned `dkim=pass` is accepted with STRICT alignment:
  the signing domain (`header.i=@d` / `header.d=d`) must equal the From
  registrable domain exactly, compared punycode-normalized. No
  relaxed-subdomain acceptance in the first release.
- Anything else — no AR header, untrusted authserv-id, parse failure,
  misaligned dkim — is False. False routes to the classifier (fail toward
  judgment), never blocks delivery.

Limit (stated in the plan): a pass proves domain/signing control, not that
this message matches the sender's historical category — that is what
universal attention judgment + sampling + the disable overlay are for.
"""

from __future__ import annotations

import re

TRUSTED_AUTHSERV = "mx.google.com"

_DKIM_DOMAIN_RE = re.compile(r"dkim=pass\b[^;]*?header\.(?:i=@|d=)([^\s;]+)", re.IGNORECASE)
_DMARC_PASS_RE = re.compile(r"\bdmarc=pass\b", re.IGNORECASE)


def _normalize_domain(domain: str) -> str:
    domain = domain.strip().strip(">").strip().lower().rstrip(".")
    try:
        return domain.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        return domain


def authentication_pass(auth_results: list[str], from_address: str) -> bool:
    """True iff Gmail's own AR verdict authenticates `from_address`."""
    if "@" not in from_address:
        return False
    from_domain = _normalize_domain(from_address.rsplit("@", 1)[-1])
    if not from_domain:
        return False

    for value in auth_results:
        head = value.split(";", 1)[0].strip().split()
        authserv = head[0].lower() if head else ""
        if authserv != TRUSTED_AUTHSERV:
            continue  # attacker-supplied or foreign relay — never trusted
        # First trusted entry decides; do not fall through to later ones.
        if _DMARC_PASS_RE.search(value):
            return True
        for match in _DKIM_DOMAIN_RE.finditer(value):
            if _normalize_domain(match.group(1)) == from_domain:
                return True
        return False
    return False
