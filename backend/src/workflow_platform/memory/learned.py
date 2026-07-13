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
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from workflow_platform.bedrock import BedrockClient
from workflow_platform.cost.pricing import cost_for_usage

DEFAULT_LEARNED_MEMORY_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


class LearnedObservation(BaseModel):
    """Result of one memory write, shaped for audit-log detail."""

    text_hash: str
    author: str
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
    ) -> LearnedObservation:
        """Ingest one event into `user_id`'s memory. `author` is the
        trust-critical input: "third_party" for received mail / external
        content, "system" for platform-derived content, "user" for the
        user's own words."""
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
            event_type=event_type,
            evidence_ref=evidence_ref,
            facts=int(result.get("facts", 0)),
            quarantined=int(result.get("quarantined", 0)),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            model=self.model_id,
        )

    def close(self) -> None:
        if self._memory is not None:
            self._memory.close()
            self._memory = None
