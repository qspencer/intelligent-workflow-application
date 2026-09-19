"""The scheduler — G12 C1 (`docs/ASK_THE_USER_PLAN.md` §3).

**One implementation, two states.** Round 2: *"identical rules do not
require shared mutable state."* This code is what C2 will run against
live state; C1 binds it to a shadow store, so the rules being exercised
are the real ones and nothing they do consumes a live slot.

What C1 can show: candidate frequency, suppression reasons and their
distribution, cap enforcement, duplicate prevention, recovery. What it
cannot show: whether asking benefits anyone. Round 3 narrowed the claim
and the narrower one is the honest one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from workflow_platform.elicitation.catalog import (
    QuestionCandidate,
    QuestionCatalog,
    validate_candidate,
)
from workflow_platform.elicitation.store import QuestionStore, ShadowQuestion, default_expiry

#: Every way a candidate can fail to become a question. C1's primary
#: output is the distribution of these, so they are stable tokens.
SUPPRESSION_REASONS = (
    "topic_not_catalogued",
    "branch_answer_not_offered",
    "outcome_not_in_vocabulary",
    "answer_already_known",
    "answer_lookup_unavailable",
    "already_asked",
    "recipient_at_capacity",
)


class AnswerLookup(Protocol):
    """The eligible-answer lookup (§1a). It is the authority for "does an
    answer exist?", NOT the truncated profile rendering — an answer
    omitted from the prompt for space is still an existing answer."""

    async def has_current_answer(self, subject: str, topic: str) -> bool: ...


@dataclass(frozen=True)
class SchedulingDecision:
    """`asked` xor `suppressed_because`. Recorded either way: a
    suppression is the datum C1 exists to collect."""

    topic: str | None
    asked: bool
    question: ShadowQuestion | None
    suppressed_because: str | None


class _LookupFailed(Exception):
    pass


async def schedule(
    candidate: QuestionCandidate,
    *,
    catalog: QuestionCatalog,
    store: QuestionStore,
    answers: AnswerLookup,
    org_id: str,
    subject: str,
    recipient: str,
    now: datetime | None = None,
) -> SchedulingDecision:
    """Validate a classifier candidate and, if every rule permits, create
    the question atomically.

    Order matters and is the cheap-first order: catalogue validation
    needs no I/O, the answer lookup is one read, and the atomic create is
    the only write. Nothing is written before the last step, so a crash
    anywhere earlier leaves nothing at all (§3a).
    """
    topic, reason = validate_candidate(candidate, catalog)
    if topic is None:
        return SchedulingDecision(candidate.topic, False, None, reason)

    try:
        known = await answers.has_current_answer(subject, topic.id)
    except Exception:
        # ROUND 3: a lookup that FAILED must not be recorded as "no
        # current answer". Counting it as absent would inflate the
        # candidate-demand figure in the one direction that looks like
        # the feature working.
        return SchedulingDecision(topic.id, False, None, "answer_lookup_unavailable")
    if known:
        return SchedulingDecision(topic.id, False, None, "answer_already_known")

    result = await store.create_if_permitted(
        org_id=org_id,
        subject=subject,
        recipient=recipient,
        topic=topic.id,
        catalog_revision=topic.revision,
        prompt_shown=topic.prompt,
        answers_offered=list(topic.answers),
        fact_mapping=dict(topic.fact),
        max_outstanding=catalog.max_outstanding_per_recipient,
        expires_at=default_expiry(catalog.pending_expiry_hours),
    )
    if result.question is None:
        return SchedulingDecision(topic.id, False, None, result.reason)
    return SchedulingDecision(topic.id, True, result.question, None)
