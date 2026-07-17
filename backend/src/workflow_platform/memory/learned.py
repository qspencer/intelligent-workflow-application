"""Learned per-entity memory via veracium (write-only slice).

Wraps the `veracium` library (provenance-aware typed graph + episodes, SQLite)
behind the platform's operating constraints, per the adoption conditions in
`docs/SEMANTICS.md` ("veracium — adopted"):

- The **engine** writes observations after a successful run; agents have no
  memory-write tool (COALA N1 stands).
- veracium's internal LLM calls route through `BedrockClient`, so they inherit
  cost metering, audit attribution, and record/replay.
- The curated-wiki recompile is disabled (`wiki_recompile_after_writes=0`):
  this slice only writes, so the write path makes exactly one cheap-tier
  `distill` call per observation and recall-time machinery stays dormant.

veracium is sync (blocking SQLite + LLM calls), so `observe` dispatches to a
worker thread; the `Complete` adapter hops each LLM call back onto the event
loop where `BedrockClient.converse` lives. An `asyncio.Lock` serializes
observes across concurrent workflow runs — veracium's store is thread-safe,
but its ingest pipeline assumes a single writer per user.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from workflow_platform.bedrock import BedrockClient
from workflow_platform.cost.pricing import cost_for_usage

logger = logging.getLogger(__name__)

DEFAULT_LEARNED_MEMORY_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def normalize_entity(value: str) -> str:
    """Normalize an entity key before it touches recall (G10 security
    requirement #2: sender-derived keys are attacker-chosen strings).
    Email-shaped values get case folding and plus-addressing stripped
    (`User+Tag@Gmail.com` → `user@gmail.com`) so one sender can't split
    across memory partitions or dodge its own history. Non-email values
    just fold case. veracium treats ids as opaque; normalization is ours."""
    v = value.strip().lower()
    if v.count("@") == 1:
        local, domain = v.split("@")
        local = local.split("+", 1)[0]
        if local and domain:
            return f"{local}@{domain}"
    return v


class RecalledMemory(BaseModel):
    """Result of one recall, shaped for audit-log detail + prompt injection."""

    query: str
    context: str
    context_hash: str
    edges: int
    episodes: int
    token_budget: int
    # Ids of the recalled edges — the facts this run consulted. Act-time
    # outcome recording (veracium >=0.3.0b1) marks each as an `unreviewed`
    # use; later judgments upgrade those uses by evidence_ref.
    edge_ids: list[str] = []


class LearnedObservation(BaseModel):
    """Result of one memory write, shaped for audit-log detail."""

    text_hash: str
    author: str
    derived_from: str | None = None
    event_type: str
    evidence_ref: str | None
    facts: int
    quarantined: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str


class _BedrockComplete:
    """veracium `Complete` adapter over the platform's BedrockClient.

    Called synchronously from the worker thread `observe` runs in; each call
    is shipped back to the captured event loop where the async client lives.
    Usage from every call accumulates on `usage_records` so the service can
    meter cost per observation. Not safe for concurrent use — the service's
    lock guarantees one observe (hence one consumer) at a time.
    """

    def __init__(self, bedrock: BedrockClient, model_id: str) -> None:
        self._bedrock = bedrock
        self._model_id = model_id
        self.loop: asyncio.AbstractEventLoop | None = None
        self.usage_records: list[dict[str, int]] = []

    def __call__(
        self,
        prompt: str,
        *,
        system: str | None = None,
        role: str = "compile",
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        if self.loop is None:
            raise RuntimeError("_BedrockComplete used outside LearnedMemoryService.observe")
        future = asyncio.run_coroutine_threadsafe(
            self._bedrock.converse(
                model_id=self._model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                system=[{"text": system}] if system else None,
            ),
            self.loop,
        )
        response = future.result()
        usage = response.get("usage", {})
        self.usage_records.append(
            {
                "input_tokens": int(usage.get("inputTokens", 0)),
                "output_tokens": int(usage.get("outputTokens", 0)),
            }
        )
        content = response.get("output", {}).get("message", {}).get("content", [])
        return "".join(block.get("text", "") for block in content if isinstance(block, dict))


class LearnedMemoryService:
    """Async, metered facade over a veracium `Memory` (write-only slice)."""

    def __init__(
        self,
        bedrock: BedrockClient,
        db_path: str | Path,
        model_id: str | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.model_id = model_id or os.environ.get(
            "WORKFLOW_PLATFORM_LEARNED_MEMORY_MODEL", DEFAULT_LEARNED_MEMORY_MODEL
        )
        self._complete = _BedrockComplete(bedrock, self.model_id)
        self._memory: Any = None
        self._lock = asyncio.Lock()

    def _get_memory(self) -> Any:
        if self._memory is None:
            from veracium import Memory, MemoryConfig

            # SqliteStore doesn't create parent directories itself.
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._memory = Memory(
                llm=self._complete,
                config=MemoryConfig(
                    db_path=str(self.db_path),
                    wiki_recompile_after_writes=0,
                ),
            )
        return self._memory

    async def observe(
        self,
        user_id: str,
        text: str,
        *,
        author: str,
        event_type: str = "chat",
        date: str | None = None,
        evidence_ref: str | None = None,
        derived_from: str | None = None,
    ) -> LearnedObservation:
        """Ingest one event into `user_id`'s memory. `author` is the
        trust-critical input: "third_party" for received mail / external
        content, "system" for platform-derived content, "user" for the
        user's own words. `derived_from` declares mixed provenance (veracium
        ≥0.1.7): a system-authored event whose *content* embeds third-party
        text (a triage verdict quoting a subject line) passes
        `derived_from="third_party"` so trust caps at the minimum of the two —
        closing the system-event laundering channel we reported."""
        from veracium import EvidenceAuthor

        author_enum = EvidenceAuthor[author.upper()]
        async with self._lock:
            memory = self._get_memory()
            self._complete.loop = asyncio.get_running_loop()
            self._complete.usage_records = []
            try:
                kwargs: dict[str, Any] = {
                    "author": author_enum,
                    "event_type": event_type,
                    "evidence_ref": evidence_ref,
                }
                if date:
                    kwargs["date"] = date
                if derived_from:
                    kwargs["derived_from"] = EvidenceAuthor[derived_from.upper()]
                result = await asyncio.to_thread(memory.remember, user_id, text, **kwargs)
            finally:
                self._complete.loop = None
            input_tokens = sum(r["input_tokens"] for r in self._complete.usage_records)
            output_tokens = sum(r["output_tokens"] for r in self._complete.usage_records)
        cost = cost_for_usage(
            {"input_tokens": input_tokens, "output_tokens": output_tokens}, self.model_id
        )
        return LearnedObservation(
            text_hash="sha256:" + hashlib.sha256(text.encode()).hexdigest()[:16],
            author=author,
            derived_from=derived_from,
            event_type=event_type,
            evidence_ref=evidence_ref,
            facts=int(result.get("facts", 0)),
            quarantined=int(result.get("quarantined", 0)),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            model=self.model_id,
        )

    async def recall_context(
        self, user_id: str, query: str, *, token_budget: int = 600
    ) -> RecalledMemory:
        """Assemble the learned-memory context for a query (G10 read side).

        With the wiki disabled this renders the entity-matched subgraph
        directly — zero LLM calls, so recall is cost-free at read time. The
        returned `context` is veracium's pre-rendered block, unverified fence
        included; callers must inject it VERBATIM (G10 security requirement
        #1: no flattening, no LLM re-summarization — that would be
        laundering, one layer up)."""
        async with self._lock:
            memory = self._get_memory()
            recall = await asyncio.to_thread(
                memory.recall, user_id, query, token_budget=token_budget
            )
        context = str(recall.context or "")
        return RecalledMemory(
            query=query,
            context=context,
            context_hash="sha256:" + hashlib.sha256(context.encode()).hexdigest()[:16],
            edges=len(recall.edges),
            episodes=len(recall.episodes),
            token_budget=token_budget,
            edge_ids=[e.id for e in recall.edges],
        )

    async def record_outcomes(
        self,
        user_id: str,
        edge_ids: list[str],
        *,
        outcome: str,
        evidence_ref: str,
        actor: str = "system",
        corrected_value: str | None = None,
        context_ref: str | None = None,
        date: str | None = None,
    ) -> dict[str, int]:
        """Record one use-or-judgment outcome against each edge (veracium V4).

        Pure store writes — zero LLM calls. Upgrade-by-(edge_id, evidence_ref)
        makes this idempotent: replaying the same evidence_ref upgrades in
        place instead of inflating `times_used`, which matches the engine's
        restart/replay semantics. Batched in a single worker-thread hop.
        Returns {"recorded": n, "upgraded": n, "failed": n}."""
        async with self._lock:
            memory = self._get_memory()

            def _record_all() -> dict[str, int]:
                stats = {"recorded": 0, "upgraded": 0, "failed": 0}
                for edge_id in edge_ids:
                    try:
                        result = memory.record_outcome(
                            user_id,
                            edge_id,
                            outcome=outcome,
                            evidence_ref=evidence_ref,
                            actor=actor,
                            corrected_value=corrected_value,
                            context_ref=context_ref,
                            date=date,
                        )
                    except Exception:
                        logger.exception("record_outcome failed for edge %s", edge_id)
                        stats["failed"] += 1
                        continue
                    stats["upgraded" if result.get("upgraded") else "recorded"] += 1
                return stats

            return await asyncio.to_thread(_record_all)

    def close(self) -> None:
        if self._memory is not None:
            self._memory.close()
            self._memory = None
