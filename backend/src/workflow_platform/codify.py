"""Sender-codification eligibility (EMAIL_TRIAGE_CODIFY_PLAN v2 §2-§3).

Pure decision logic — no I/O. The CLI (`tools/codify_senders.py`) feeds it
verdict rows from step_executions (the distinct-adjudicated-message unit)
and disqualifier facts from the veracium store, and writes the rule
artifact. Keeping the rule pure makes every §2 condition unit-testable
without a database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

#: Categories codifiable in the first release (v2 §2.3). personal/spam stay
#: classifier-only until a compelling case exists.
CODIFIABLE_CATEGORIES = frozenset({"newsletter", "promotion", "notification"})

#: Freemail/consumer domains where a shared domain implies NOTHING about
#: sender equivalence — exempt from domain-level disqualification (decision
#: 2026-07-30: a correction on one gmail.com sender must not taint every
#: gmail.com sender; a correction on info@vendor.com SHOULD taint
#: news@vendor.com — same organization, same codification risk).
FREEMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "msn.com",
        "aol.com",
        "icloud.com",
        "me.com",
        "proton.me",
        "protonmail.com",
        "gmx.com",
        "mail.com",
        "comcast.net",
        "att.net",
        "verizon.net",
    }
)

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def registrable_domain(address: str) -> str:
    """Best-effort registrable domain: last two labels (three for known
    ccTLD second-levels). Good enough for same-organization grouping of
    mail domains; not a general PSL implementation."""
    host = address.rsplit("@", 1)[-1].lower()
    parts = host.split(".")
    if (
        len(parts) >= 3
        and parts[-2] in ("co", "com", "org", "net", "ac", "gov")
        and len(parts[-1]) == 2
    ):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


@dataclass
class VerdictRow:
    """One adjudicated message (from step_executions x instance trigger)."""

    sender: str
    message_id: str
    category: str
    attention: list[str]
    schema_version: int
    decision_source: str  # "classifier" | "codified_sender_rule"
    at: datetime


@dataclass
class SenderFacts:
    """Disqualifier-side facts (veracium outcome events + operator input)."""

    corrected: int = 0
    legacy_confirmed: int = 0
    attention_bearing: int = 0


@dataclass
class Eligibility:
    sender: str
    eligible: bool = False
    category: str | None = None
    reasons: list[str] = field(default_factory=list)
    distinct_messages: int = 0
    verdict_events_seen: int = 0
    current_schema_messages: int = 0
    legacy_confirmed: int = 0
    first_evidence_at: datetime | None = None
    last_evidence_at: datetime | None = None


def evaluate_sender(
    sender: str,
    rows: list[VerdictRow],
    facts: SenderFacts,
    *,
    mailbox_owner: str,
    corrected_domains: frozenset[str],
    now: datetime,
    min_current_schema: int = 5,
    min_span_days: float = 1.0,
    recency_days: float = 30.0,
) -> Eligibility:
    """Apply the §2 promotion rule to one sender. Every condition that fails
    appends a reason — `--explain` prints them verbatim."""
    out = Eligibility(sender=sender)
    reasons = out.reasons

    # §2.4 entity shape: email-shaped, normalized, never the mailbox owner.
    if not _EMAIL_RE.match(sender):
        reasons.append("key is not an email address")
    elif sender != sender.strip().lower():
        reasons.append("key is not normalized (must be a normalize_entity fixpoint)")
    if sender == mailbox_owner.strip().lower():
        reasons.append("sender is the mailbox owner")

    # Distinct-message collapse: last verdict per message id wins;
    # human corrections arrive via `facts`, not rows.
    by_message: dict[str, VerdictRow] = {}
    for row in sorted(rows, key=lambda r: r.at):
        by_message[row.message_id] = row
    classifier_rows = [r for r in by_message.values() if r.decision_source == "classifier"]
    out.verdict_events_seen = len(rows)
    out.distinct_messages = len(classifier_rows)

    current = [r for r in classifier_rows if r.schema_version >= 2]
    out.current_schema_messages = len(current)
    out.legacy_confirmed = facts.legacy_confirmed

    # §2.1 current-schema floor.
    if len(current) < min_current_schema:
        reasons.append(
            f"current-schema floor not met ({len(current)}/{min_current_schema} distinct schema-2 messages)"
        )
    categories = {r.category for r in current}
    if len(categories) > 1:
        reasons.append(f"not category-unanimous in current schema ({sorted(categories)})")
    if any(r.attention for r in current):
        reasons.append("attention-bearing current-schema verdicts exist")

    # §2.3 category allowlist.
    category = next(iter(categories)) if len(categories) == 1 else None
    if category is not None and category not in CODIFIABLE_CATEGORIES:
        reasons.append(f"category {category!r} is not codifiable in the first release")

    # §2.2 diversity + recency.
    if current:
        times = [r.at for r in current]
        out.first_evidence_at = min(times)
        out.last_evidence_at = max(times)
        if (out.last_evidence_at - out.first_evidence_at) < timedelta(days=min_span_days):
            reasons.append(f"evidence spans < {min_span_days} day(s) (single-batch)")
        if (now - out.last_evidence_at) > timedelta(days=recency_days):
            reasons.append(f"no classifier judgment in the last {recency_days:g} days")

    # §2.5 disqualifiers.
    if facts.corrected:
        reasons.append(f"{facts.corrected} human correction(s) on record")
    if facts.attention_bearing:
        reasons.append(f"{facts.attention_bearing} attention-bearing observation(s) on record")
    domain = registrable_domain(sender) if _EMAIL_RE.match(sender) else ""
    if domain and domain not in FREEMAIL_DOMAINS and domain in corrected_domains:
        reasons.append(
            f"domain-level disqualification: a corrected sender shares registrable domain {domain!r}"
        )

    out.eligible = not reasons
    out.category = category if out.eligible else None
    return out


def corrected_domain_set(corrected_senders: list[str]) -> frozenset[str]:
    """Registrable domains of corrected senders, freemail-exempt (the
    2026-07-30 domain-level disqualification decision)."""
    domains = set()
    for sender in corrected_senders:
        if _EMAIL_RE.match(sender):
            domain = registrable_domain(sender)
            if domain not in FREEMAIL_DOMAINS:
                domains.add(domain)
    return frozenset(domains)
