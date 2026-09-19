"""G12 C1 — the catalog, candidate validation and shadow scheduling.

`docs/ASK_THE_USER_PLAN.md`. Round 3 accepted C1 and named five
invariants the implementation must demonstrate; §3 below is those five,
one test each, with the concurrency one on Postgres because an
in-memory double cannot exhibit a database race (ledger M8).

What these tests do NOT establish, stated because the temptation is
real: whether asking anyone anything is useful. C1 exercises the
mechanism and measures demand. Value needs C4.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from workflow_platform.elicitation import (
    InMemoryShadowStore,
    QuestionCandidate,
    QuestionCatalog,
    QuestionTopic,
    schedule,
    validate_candidate,
)

EMPLOYMENT = QuestionTopic(
    id="employment_status",
    revision=3,
    prompt="Are you currently seeking work?",
    answers=["seeking", "employed", "unknown"],
    fact={
        "seeking": "The owner is currently seeking work.",
        "employed": "The owner is not currently seeking work.",
        "unknown": "The owner's employment status is unknown.",
    },
    valid_for_days=90,
)


def _catalog(**over: Any) -> QuestionCatalog:
    return QuestionCatalog(topics=[EMPLOYMENT], **over)


def _candidate(**over: Any) -> QuestionCandidate:
    base: dict[str, Any] = {
        "topic": "employment_status",
        "if_answer": "seeking",
        "then": {"priority": "relevant"},
    }
    base.update(over)
    return QuestionCandidate(**base)


class _NoAnswers:
    async def has_current_answer(self, subject: str, topic: str) -> bool:
        return False


class _HasAnswer:
    async def has_current_answer(self, subject: str, topic: str) -> bool:
        return True


class _LookupBroken:
    async def has_current_answer(self, subject: str, topic: str) -> bool:
        raise RuntimeError("SYNTHETIC answer store unavailable")


async def _schedule(store: Any, answers: Any = None, catalog: Any = None, **over: Any) -> Any:
    return await schedule(
        over.pop("candidate", _candidate()),
        catalog=catalog or _catalog(),
        store=store,
        answers=answers or _NoAnswers(),
        org_id=over.pop("org_id", "default"),
        subject=over.pop("subject", "user-1"),
        recipient=over.pop("recipient", "user-1"),
    )


# --- 1. the catalog refuses what §2a says it must ---------------------------


def test_a_prompt_may_not_interpolate_message_text() -> None:
    """§2a. This is the byte-level path from a hostile email into a
    question, so it is refused at LOAD rather than reviewed."""
    with pytest.raises(ValueError, match="may not interpolate"):
        QuestionTopic(
            id="t",
            prompt="Did you mean {trigger.subject}?",
            answers=["y"],
            fact={"y": "yes"},
        )


def test_a_stored_assertion_may_not_interpolate_model_output() -> None:
    with pytest.raises(ValueError, match="may not interpolate"):
        QuestionTopic(
            id="t",
            prompt="ok?",
            answers=["y"],
            fact={"y": "The owner said {steps.record.summary}"},
        )


def test_every_offered_answer_must_map_to_an_assertion() -> None:
    """A user confirms the assertion, not the token (§2a) — so an answer
    with no assertion is an answer nobody can confirm."""
    with pytest.raises(ValueError, match="no fact mapping"):
        QuestionTopic(id="t", prompt="ok?", answers=["y", "n"], fact={"y": "yes"})


def test_a_fact_for_an_unoffered_answer_is_refused() -> None:
    with pytest.raises(ValueError, match="unoffered"):
        QuestionTopic(id="t", prompt="ok?", answers=["y"], fact={"y": "yes", "n": "no"})


# --- 2. candidate validation ------------------------------------------------


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"topic": "not_in_catalog"}, "topic_not_catalogued"),
        ({"if_answer": "SYNTHETIC"}, "branch_answer_not_offered"),
        ({"then": {"priority": "review"}}, "outcome_not_in_vocabulary"),
        ({"then": {}}, "outcome_not_in_vocabulary"),
    ],
)
def test_a_candidate_outside_the_vocabulary_is_rejected(over: Any, reason: str) -> None:
    """The classifier's only output into this path is a topic id and a
    branch in catalogue vocabulary. `priority: review` is rejected
    specifically: C1 must not be able to redefine the two-axis rubric."""
    topic, got = validate_candidate(_candidate(**over), _catalog())
    assert topic is None and got == reason


def test_a_valid_candidate_passes() -> None:
    topic, reason = validate_candidate(_candidate(), _catalog())
    assert reason is None and topic is not None and topic.id == "employment_status"


# --- 3. the five invariants round 3 named -----------------------------------


async def test_INVARIANT_a_crash_leaves_nothing_or_a_complete_record() -> None:
    """§3a. Nothing is written before the atomic create, so a failure
    anywhere earlier leaves no partial state — in particular no reserved
    topic that can never be asked again."""
    store = InMemoryShadowStore()
    decision = await _schedule(store, answers=_LookupBroken())

    assert not decision.asked
    assert await store.list_questions() == [], "a failed run left a question"
    assert not await store.was_ever_asked("user-1", "employment_status"), (
        "a failed run consumed the once-ever history, so the topic can never be asked"
    )


async def test_INVARIANT_expiry_releases_capacity_once_and_keeps_the_history() -> None:
    """§3a. The two records are separate for exactly this: capacity must
    recover, and 'asked once ever' must not."""
    store = InMemoryShadowStore()
    catalog = _catalog(max_outstanding_per_recipient=1, pending_expiry_hours=1)
    assert (await _schedule(store, catalog=catalog)).asked

    later = datetime.now(UTC) + timedelta(hours=2)
    assert await store.expire_pending(now=later) == 1
    assert await store.expire_pending(now=later) == 0, "expiry released capacity twice"
    assert await store.outstanding("user-1") == 0, "capacity was not released"
    assert await store.was_ever_asked("user-1", "employment_status"), (
        "expiry erased the once-ever history; the question would be re-asked"
    )


async def test_INVARIANT_a_failed_answer_lookup_is_unavailable_not_absent() -> None:
    """Round 3. Counting a failed lookup as "no answer" inflates
    candidate demand in the one direction that looks like the feature
    working."""
    store = InMemoryShadowStore()
    decision = await _schedule(store, answers=_LookupBroken())
    assert decision.suppressed_because == "answer_lookup_unavailable"
    assert decision.suppressed_because != "answer_already_known"


async def test_INVARIANT_shadow_activity_touches_no_live_state() -> None:
    """§3b. The shadow store is the only thing the scheduler can write
    to — it holds no reference to any live repository, so 'it changed
    nothing live' is a property of the wiring, not of the test."""
    import inspect

    from workflow_platform.elicitation import scheduler

    source = inspect.getsource(scheduler)
    for forbidden in ("Repositories", "repositories", "audit.append", "instances."):
        assert forbidden not in source, (
            f"the scheduler reaches {forbidden!r}; shadow state must be its only writable state"
        )
    params = inspect.signature(scheduler.schedule).parameters
    assert set(params) == {
        "candidate",
        "catalog",
        "store",
        "answers",
        "org_id",
        "subject",
        "recipient",
        "now",
    }


async def test_INVARIANT_the_same_topic_is_never_asked_twice() -> None:
    store = InMemoryShadowStore()
    assert (await _schedule(store)).asked
    second = await _schedule(store)
    assert not second.asked and second.suppressed_because == "already_asked"


# --- 4. the remaining suppression reasons -----------------------------------


async def test_a_known_answer_suppresses_the_question() -> None:
    store = InMemoryShadowStore()
    decision = await _schedule(store, answers=_HasAnswer())
    assert decision.suppressed_because == "answer_already_known"
    assert await store.list_questions() == []


async def test_capacity_suppresses_a_DIFFERENT_topic() -> None:
    """The recipient cap is across topics — a per-(subject, topic) rule
    would not bound the operator's load at all."""
    other = EMPLOYMENT.model_copy(update={"id": "working_pattern"})
    catalog = QuestionCatalog(topics=[EMPLOYMENT, other], max_outstanding_per_recipient=1)
    store = InMemoryShadowStore()

    assert (await _schedule(store, catalog=catalog)).asked
    second = await _schedule(store, catalog=catalog, candidate=_candidate(topic="working_pattern"))
    assert not second.asked and second.suppressed_because == "recipient_at_capacity"


async def test_the_question_freezes_the_wording_and_the_mapping() -> None:
    """§2a — a later catalog edit cannot reach a question already
    asked, because the question carries its own copy."""
    store = InMemoryShadowStore()
    decision = await _schedule(store)
    q = decision.question
    assert q is not None
    assert q.catalog_revision == 3
    assert q.prompt_shown == EMPLOYMENT.prompt
    assert q.answers_offered == EMPLOYMENT.answers
    assert q.fact_mapping == EMPLOYMENT.fact


# --- 5. the concurrency invariant, on a real backend ------------------------

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.mark.integration
@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="TEST_DATABASE_URL not set")
async def test_INVARIANT_two_topics_cannot_both_take_the_last_slot() -> None:
    """Round 2's race, and round 3's insistence that "inside one
    transaction" needs an enforcing mechanism.

    A `(subject, topic)` constraint stops two runs asking the SAME
    question and does nothing here: these are two DIFFERENT topics
    competing for one remaining slot. The enforcement is the conditional
    insert — the capacity predicate is evaluated inside the statement
    that writes — and this is the test that establishes it.

    **An in-memory double cannot exhibit this** (ledger M8), which is why
    it runs against Postgres or not at all.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from workflow_platform.elicitation import PostgresShadowStore

    engine = create_async_engine(TEST_DATABASE_URL or "")
    sf = async_sessionmaker(engine, expire_on_commit=False)
    store = PostgresShadowStore(sf)

    topics = [EMPLOYMENT, EMPLOYMENT.model_copy(update={"id": "working_pattern"})]
    catalog = QuestionCatalog(topics=topics, max_outstanding_per_recipient=1)
    subject = f"race-{datetime.now(UTC).timestamp()}"

    results = await asyncio.gather(
        _schedule(store, catalog=catalog, subject=subject, recipient=subject),
        _schedule(
            store,
            catalog=catalog,
            subject=subject,
            recipient=subject,
            candidate=_candidate(topic="working_pattern"),
        ),
    )
    asked = [r for r in results if r.asked]
    assert len(asked) == 1, (
        f"{len(asked)} questions took the last slot; the capacity predicate is not "
        "being evaluated inside the writing statement"
    )
    assert any(r.suppressed_because == "recipient_at_capacity" for r in results)
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="TEST_DATABASE_URL not set")
async def test_INVARIANT_the_capacity_update_SERIALIZES_deterministically() -> None:
    """The test above races two schedulers and asserts one wins. **That
    is opportunistic**: it caught the original count-based predicate
    letting two questions take one slot, but when the broken predicate
    was put back deliberately it passed — two `gather`ed coroutines do
    not reliably interleave inside their transactions.

    A race test that passes is not evidence the race cannot happen. So
    this one proves the mechanism instead of hoping for the schedule: it
    holds transaction A open past its capacity update and shows that B's
    identical update BLOCKS on the row lock rather than reading a stale
    count. That is the serialization the conditional counter update buys
    and the counting predicate did not.
    """
    from sqlalchemy import text as sql
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(TEST_DATABASE_URL or "")
    sf = async_sessionmaker(engine, expire_on_commit=False)
    recipient = f"lock-{datetime.now(UTC).timestamp()}"
    bump = sql(
        "update shadow_capacity set outstanding = outstanding + 1 "
        "where recipient = :r and outstanding < :cap returning outstanding"
    )

    async with sf() as setup, setup.begin():
        await setup.execute(
            sql("insert into shadow_capacity (recipient, outstanding) values (:r, 0)"),
            {"r": recipient},
        )

    a = sf()
    await a.begin()
    took_a = (await a.execute(bump, {"r": recipient, "cap": 1})).first()
    assert took_a is not None, "A did not take the only slot"

    async def _b() -> Any:
        async with sf() as b, b.begin():
            return (await b.execute(bump, {"r": recipient, "cap": 1})).first()

    task = asyncio.create_task(_b())
    await asyncio.sleep(0.25)
    assert not task.done(), (
        "B's capacity update did not block while A held the row — the statement is "
        "reading a snapshot instead of serializing, which is what let two questions "
        "take one slot"
    )

    await a.commit()
    await a.close()
    assert await asyncio.wait_for(task, timeout=5) is None, (
        "B took a slot that A had already committed"
    )
    await engine.dispose()
