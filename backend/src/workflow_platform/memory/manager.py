"""Agent memory — Phase A from `docs/LEARNING_IMPLEMENTATION.md`.

Per-agent text files on disk. Loaded into an agent's system prompt at startup
so it sees its prior observations. Append-only; future weeks add compaction
(Phase A "summarize"), contextual retrieval (Phase B-C), and an active query
tool (Phase E). Path: `{base_dir}/{agent_id}.md`. agent_id can contain `/`
to scope by workflow + step (per ARCHITECTURE D5).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

OBSERVATIONS_HEADING = "## Recent Observations"


class MemoryManager:
    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)

    def _path(self, agent_id: str) -> Path:
        return self.base_dir / f"{agent_id}.md"

    async def load(self, agent_id: str) -> str:
        """Return the agent's memory file as a string, or empty string if none yet."""
        path = self._path(agent_id)
        if not await asyncio.to_thread(path.is_file):
            return ""
        return await asyncio.to_thread(path.read_text)

    async def append(self, agent_id: str, observation: str) -> None:
        """Append a timestamped observation under `## Recent Observations`."""
        timestamp = datetime.now(UTC).isoformat()
        await asyncio.to_thread(self._append_sync, agent_id, observation, timestamp)

    async def write_raw(self, agent_id: str, content: str) -> None:
        """Replace the agent's memory file with `content` (overwriting).

        Used to seed a pinned rubric from a workflow's adjacent
        `agent_memory.md` at load time. The rubric is part of the agent's
        memory rather than the step's `system_prompt` so the engine's
        `memory_hash` reflects rubric edits — letting audit-log consumers
        correlate behavior changes with the edit that caused them.
        """
        await asyncio.to_thread(self._write_raw_sync, agent_id, content)

    def _write_raw_sync(self, agent_id: str, content: str) -> None:
        path = self._path(agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def _append_sync(self, agent_id: str, observation: str, timestamp: str) -> None:
        path = self._path(agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists():
            content = path.read_text()
        else:
            content = f"# Agent Memory: {agent_id}\n\n{OBSERVATIONS_HEADING}\n"

        if OBSERVATIONS_HEADING not in content:
            content = (
                content.rstrip() + f"\n\n{OBSERVATIONS_HEADING}\n"
                if content.strip()
                else f"# Agent Memory: {agent_id}\n\n{OBSERVATIONS_HEADING}\n"
            )

        marker = OBSERVATIONS_HEADING + "\n"
        idx = content.index(marker) + len(marker)
        new_entry = f"- [{timestamp}] {observation}\n"
        path.write_text(content[:idx] + new_entry + content[idx:])
