"""Shadow state for C1 (`docs/ASK_THE_USER_PLAN.md` §3b).

Round 2 dissolved the shadow/live dilemma: *"identical rules do not
require shared mutable state."* So the scheduler is written once against
this interface, and C1 binds it to a store whose rows are its own —
its own questions, its own counters, its own "once ever" history.
Nothing C1 does consumes a live slot.

TWO IMPLEMENTATIONS, DELIBERATELY. The in-memory one runs in unit tests.
The Postgres one exists because **the invariant C1 must demonstrate is a
database property**: two concurrent transactions must not both take the
last recipient slot, and an in-memory double cannot exhibit that (ledger
M8 — "what can the double not exhibit?"). The concurrency test runs
against Postgres and is skipped without it, like the other integration
tests.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class ShadowQuestion:
    id: str
    org_id: str
    subject: str
    recipient: str
    topic: str
    catalog_revision: int
    prompt_shown: str
    answers_offered: list[str]
    fact_mapping: dict[str, str]
    status: str  # pending | expired (C1 simulates; answering is C2)
    created_at: datetime
    expires_at: datetime


@dataclass
class CreateResult:
    """`question` when one was created, else `reason` saying which rule
    refused. Never both."""

    question: ShadowQuestion | None = None
    reason: str | None = None


class QuestionStore(ABC):
    """The scheduler's whole view of state. Everything that must be
    atomic is ONE method, so no caller can reimplement the ordering."""

    @abstractmethod
    async def create_if_permitted(
        self,
        *,
        org_id: str,
        subject: str,
        recipient: str,
        topic: str,
        catalog_revision: int,
        prompt_shown: str,
        answers_offered: list[str],
        fact_mapping: dict[str, str],
        max_outstanding: int,
        expires_at: datetime,
    ) -> CreateResult:
        """Capacity check, capacity consume, duplicate suppression and
        the question record — **in one transaction** (§3a).

        A crash leaves nothing or a complete question; there is no
        reservation to strand, which is why this is one method and not
        three. Refusal reasons: `already_asked` (the permanent
        `(subject, topic)` history) or `recipient_at_capacity`.
        """

    @abstractmethod
    async def expire_pending(self, *, now: datetime | None = None) -> int:
        """Expire pending questions past `expires_at`, releasing their
        capacity EXACTLY ONCE (§3a). The `(subject, topic)` history is
        untouched — an expired question was still asked."""

    @abstractmethod
    async def outstanding(self, recipient: str) -> int: ...

    @abstractmethod
    async def was_ever_asked(self, subject: str, topic: str) -> bool: ...

    @abstractmethod
    async def list_questions(self) -> list[ShadowQuestion]: ...


@dataclass
class InMemoryShadowStore(QuestionStore):
    """Single-process double. It cannot exhibit the concurrency invariant
    — see the module docstring — so the race is tested on Postgres."""

    questions: dict[str, ShadowQuestion] = field(default_factory=dict)
    asked: set[tuple[str, str]] = field(default_factory=set)

    async def create_if_permitted(
        self,
        *,
        org_id: str,
        subject: str,
        recipient: str,
        topic: str,
        catalog_revision: int,
        prompt_shown: str,
        answers_offered: list[str],
        fact_mapping: dict[str, str],
        max_outstanding: int,
        expires_at: datetime,
    ) -> CreateResult:
        if (subject, topic) in self.asked:
            return CreateResult(reason="already_asked")
        if await self.outstanding(recipient) >= max_outstanding:
            return CreateResult(reason="recipient_at_capacity")
        q = ShadowQuestion(
            id=uuid.uuid4().hex,
            org_id=org_id,
            subject=subject,
            recipient=recipient,
            topic=topic,
            catalog_revision=catalog_revision,
            prompt_shown=prompt_shown,
            answers_offered=list(answers_offered),
            fact_mapping=dict(fact_mapping),
            status="pending",
            created_at=_now(),
            expires_at=expires_at,
        )
        self.questions[q.id] = q
        self.asked.add((subject, topic))
        return CreateResult(question=q)

    async def expire_pending(self, *, now: datetime | None = None) -> int:
        at = now or _now()
        n = 0
        for q in self.questions.values():
            if q.status == "pending" and q.expires_at <= at:
                q.status = "expired"
                n += 1
        return n

    async def outstanding(self, recipient: str) -> int:
        return sum(
            1 for q in self.questions.values() if q.recipient == recipient and q.status == "pending"
        )

    async def was_ever_asked(self, subject: str, topic: str) -> bool:
        return (subject, topic) in self.asked

    async def list_questions(self) -> list[ShadowQuestion]:
        return list(self.questions.values())


class PostgresShadowStore(QuestionStore):
    """Shadow rows in their own tables (`shadow_questions`,
    `shadow_asked`), so C1 cannot touch live state even by mistake.

    The capacity rule is a **conditional insert**, not a read followed by
    a write: the `INSERT ... SELECT ... WHERE (count) < :cap` evaluates
    the predicate inside the same statement that writes, so two
    concurrent transactions cannot both see the last slot. Round 3's
    point that "inside one transaction" is not by itself a mechanism.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create_if_permitted(
        self,
        *,
        org_id: str,
        subject: str,
        recipient: str,
        topic: str,
        catalog_revision: int,
        prompt_shown: str,
        answers_offered: list[str],
        fact_mapping: dict[str, str],
        max_outstanding: int,
        expires_at: datetime,
    ) -> CreateResult:
        qid = uuid.uuid4().hex
        try:
            return await self._create(
                qid=qid,
                org_id=org_id,
                subject=subject,
                recipient=recipient,
                topic=topic,
                catalog_revision=catalog_revision,
                prompt_shown=prompt_shown,
                answers_offered=answers_offered,
                fact_mapping=fact_mapping,
                max_outstanding=max_outstanding,
                expires_at=expires_at,
            )
        except _CapacityRefused:
            # The transaction unwound, so the `asked` row went with it —
            # a question that was never created is not recorded as asked.
            return CreateResult(reason="recipient_at_capacity")

    async def _create(
        self,
        *,
        qid: str,
        org_id: str,
        subject: str,
        recipient: str,
        topic: str,
        catalog_revision: int,
        prompt_shown: str,
        answers_offered: list[str],
        fact_mapping: dict[str, str],
        max_outstanding: int,
        expires_at: datetime,
    ) -> CreateResult:
        async with self._sf() as s, s.begin():
            # The permanent "asked" history, first and in the same
            # transaction, so a question that is refused for capacity
            # does not leave a row claiming it was asked.
            asked = (
                await s.execute(
                    text(
                        "insert into shadow_asked (subject, topic, first_asked_at) "
                        "values (:s, :t, now()) on conflict do nothing returning subject"
                    ),
                    {"s": subject, "t": topic},
                )
            ).first()
            if asked is None:
                return CreateResult(reason="already_asked")

            await s.execute(
                text(
                    "insert into shadow_capacity (recipient, outstanding) "
                    "values (:r, 0) on conflict do nothing"
                ),
                {"r": recipient},
            )
            # THE ENFORCING MECHANISM. A conditional UPDATE of ONE row,
            # not a count of many: the second concurrent transaction
            # blocks on this row's lock and then re-evaluates
            # `outstanding < :cap` against the committed value. A
            # predicate that COUNTS rows cannot do this under READ
            # COMMITTED — each snapshot misses the other's uncommitted
            # insert — which the concurrency test demonstrated by letting
            # two questions take one slot.
            took = (
                await s.execute(
                    text(
                        "update shadow_capacity set outstanding = outstanding + 1 "
                        "where recipient = :r and outstanding < :cap returning outstanding"
                    ),
                    {"r": recipient, "cap": max_outstanding},
                )
            ).first()
            if took is None:
                raise _CapacityRefused

            await s.execute(
                text(
                    """
                    insert into shadow_questions
                      (id, org_id, subject, recipient, topic, catalog_revision,
                       prompt_shown, answers_offered, fact_mapping, status,
                       created_at, expires_at)
                    values (:id, :org, :subject, :recipient, :topic, :rev, :prompt,
                            cast(:answers as jsonb), cast(:facts as jsonb), 'pending',
                            now(), :expires)
                    """
                ),
                {
                    "id": qid,
                    "org": org_id,
                    "subject": subject,
                    "recipient": recipient,
                    "topic": topic,
                    "rev": catalog_revision,
                    "prompt": prompt_shown,
                    "answers": _json(answers_offered),
                    "facts": _json(fact_mapping),
                    "expires": expires_at,
                },
            )
        return CreateResult(
            question=ShadowQuestion(
                id=qid,
                org_id=org_id,
                subject=subject,
                recipient=recipient,
                topic=topic,
                catalog_revision=catalog_revision,
                prompt_shown=prompt_shown,
                answers_offered=list(answers_offered),
                fact_mapping=dict(fact_mapping),
                status="pending",
                created_at=_now(),
                expires_at=expires_at,
            )
        )

    async def expire_pending(self, *, now: datetime | None = None) -> int:
        async with self._sf() as s, s.begin():
            # Expire and release in ONE transaction, keyed on the rows
            # actually expired — so capacity is released EXACTLY once
            # even if two sweeps run, and never for a question that was
            # already expired.
            rows = (
                await s.execute(
                    text(
                        "update shadow_questions set status = 'expired' "
                        "where status = 'pending' and expires_at <= coalesce(:at, now()) "
                        "returning recipient"
                    ),
                    {"at": now},
                )
            ).fetchall()
            for (recipient,) in rows:
                await s.execute(
                    text(
                        "update shadow_capacity set outstanding = greatest(outstanding - 1, 0) "
                        "where recipient = :r"
                    ),
                    {"r": recipient},
                )
        return len(rows)

    async def outstanding(self, recipient: str) -> int:
        async with self._sf() as s:
            row = (
                await s.execute(
                    text("select outstanding from shadow_capacity where recipient = :r"),
                    {"r": recipient},
                )
            ).scalar()
        return int(row or 0)

    async def was_ever_asked(self, subject: str, topic: str) -> bool:
        async with self._sf() as s:
            row = (
                await s.execute(
                    text("select 1 from shadow_asked where subject = :s and topic = :t"),
                    {"s": subject, "t": topic},
                )
            ).first()
        return row is not None

    async def list_questions(self) -> list[ShadowQuestion]:
        async with self._sf() as s:
            rows = (
                await s.execute(
                    text(
                        "select id, org_id, subject, recipient, topic, catalog_revision, "
                        "prompt_shown, answers_offered, fact_mapping, status, created_at, "
                        "expires_at from shadow_questions"
                    )
                )
            ).fetchall()
        return [
            ShadowQuestion(
                id=r[0],
                org_id=r[1],
                subject=r[2],
                recipient=r[3],
                topic=r[4],
                catalog_revision=r[5],
                prompt_shown=r[6],
                answers_offered=list(r[7] or []),
                fact_mapping=dict(r[8] or {}),
                status=r[9],
                created_at=r[10],
                expires_at=r[11],
            )
            for r in rows
        ]


class _CapacityRefused(Exception):
    """Internal: unwinds the transaction so a refused question leaves no
    `asked` row. Converted to a `CreateResult` by the wrapper below."""


def _json(value: Any) -> str:
    import json

    return json.dumps(value)


def default_expiry(hours: int) -> datetime:
    return _now() + timedelta(hours=hours)
