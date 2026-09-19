"""The question catalog — G12 C1 (`docs/ASK_THE_USER_PLAN.md` §2, §5a).

Operator-authored, loaded from the workflow definition. The classifier
cannot write a question: its only output into this path is a **topic
id**, and everything a user ever sees or agrees to comes from here.

Two rules are enforced at LOAD rather than reviewed:

- a `fact` or `prompt` template may not interpolate message text or model
  output (§2a) — a `{trigger.*}` or `{steps.*}` placeholder is refused,
  because that is the byte-level path from a hostile email into a
  user-authored assertion;
- the answer set is closed and non-empty, so "pick one of these" is a
  real constraint rather than a suggestion.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

#: Placeholders that would carry email text or model output into a
#: question or a stored assertion. §2a — refused, not stripped.
_TAINTED_PLACEHOLDER = re.compile(r"\{\s*(trigger|steps)\b[^}]*\}")

#: The C1 experimental outcome vocabulary (§5a). Deliberately NOT
#: `attention`: C1 must not be able to redefine the two-axis rubric by
#: accident, and this value is provisional until the product decision.
PRIORITY_VALUES = ("relevant", "not_relevant")


class QuestionTopic(BaseModel):
    """One catalogued question. Its `revision` is frozen onto every
    question asked from it (§2a), so a later edit cannot reach a question
    already displayed."""

    id: str
    revision: int = 1
    prompt: str
    answers: list[str] = Field(min_length=1)
    #: Answer → the assertion that would be stored. The user confirms THIS
    #: sentence, not the bare answer token (§2a).
    fact: dict[str, str]
    #: Ordering for the profile rendering when it must truncate (§1a).
    #: Lower sorts first. Does not affect the eligible-answer lookup.
    priority: int = 100
    #: §6b — a changeable topic must expire, so an unnoticed stale badge
    #: cannot license indefinite use. None = no expiry (profile facts that
    #: do not change).
    valid_for_days: int | None = None
    review_after_days: int | None = None

    @field_validator("prompt")
    @classmethod
    def _prompt_is_untainted(cls, v: str) -> str:
        if _TAINTED_PLACEHOLDER.search(v):
            raise ValueError(
                "a question prompt may not interpolate message text or model output "
                "(ASK_THE_USER_PLAN §2a); found a {trigger.*}/{steps.*} placeholder"
            )
        return v

    @model_validator(mode="after")
    def _facts_cover_answers_and_are_untainted(self) -> QuestionTopic:
        missing = [a for a in self.answers if a not in self.fact]
        if missing:
            raise ValueError(f"topic {self.id!r}: no fact mapping for answer(s) {missing}")
        extra = [a for a in self.fact if a not in self.answers]
        if extra:
            raise ValueError(f"topic {self.id!r}: fact mapping for unoffered answer(s) {extra}")
        for answer, sentence in self.fact.items():
            if _TAINTED_PLACEHOLDER.search(sentence):
                raise ValueError(
                    f"topic {self.id!r} answer {answer!r}: a stored assertion may not "
                    "interpolate message text or model output (§2a)"
                )
        return self


class QuestionCatalog(BaseModel):
    """The operator's catalog for one workflow, plus the experiment's
    limits. Limits are EXPLICIT and configurable, and C1 reports them
    beside its results — they are the experiment's parameters, not
    validated product choices (round 2/3)."""

    topics: list[QuestionTopic] = Field(default_factory=list)
    #: Max questions outstanding for one recipient, across every sender
    #: and workflow (§3) — per-sender caps do not bound the operator's
    #: combined load.
    max_outstanding_per_recipient: int = 3
    #: A pending question that nobody answers releases its capacity after
    #: this long (§3a). The "once ever" history is unaffected.
    pending_expiry_hours: int = 72

    @model_validator(mode="after")
    def _ids_are_unique(self) -> QuestionCatalog:
        seen = [t.id for t in self.topics]
        dupes = {i for i in seen if seen.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate topic id(s): {sorted(dupes)}")
        return self

    def get(self, topic_id: str) -> QuestionTopic | None:
        return next((t for t in self.topics if t.id == topic_id), None)


class QuestionCandidate(BaseModel):
    """What the CLASSIFIER may emit — a topic id and a conditional branch
    in catalogue vocabulary, nothing else (§3).

    There is no tool: this rides in the classifier's JSON verdict and the
    engine validates it afterwards, so there is no authorized call from
    model output into the question path at all.
    """

    topic: str
    if_answer: str
    then: dict[str, Any] = Field(default_factory=dict)


def validate_candidate(
    candidate: QuestionCandidate, catalog: QuestionCatalog
) -> tuple[QuestionTopic | None, str | None]:
    """`(topic, None)` when the candidate is usable, `(None, reason)`
    when it is not. The reason is a stable token, because C1's whole
    output is the distribution of these."""
    topic = catalog.get(candidate.topic)
    if topic is None:
        return None, "topic_not_catalogued"
    if candidate.if_answer not in topic.answers:
        return None, "branch_answer_not_offered"
    priority = candidate.then.get("priority")
    if priority not in PRIORITY_VALUES:
        # §5a: `then` has a defined meaning and a validation rule, so a
        # candidate cannot invent an outcome vocabulary.
        return None, "outcome_not_in_vocabulary"
    return topic, None
