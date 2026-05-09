"""Smoke test for the replay CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_replay_cli_runs_definition_against_recordings(tmp_path: Path) -> None:
    """The CLI loads a definition, runs it with the supplied trigger payload,
    and exits 0 when the workflow completes."""
    definition = {
        "id": "replay-cli-test",
        "name": "Replay CLI test",
        "trigger": {"type": "manual"},
        "steps": [{"id": "a", "type": "deterministic", "function": "noop"}],
        "edges": [],
    }
    definition_path = tmp_path / "wf.json"
    definition_path.write_text(json.dumps(definition))

    recordings = tmp_path / "recordings"
    recordings.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            "tools/replay.py",
            "--definition",
            str(definition_path),
            "--trigger",
            "{}",
            "--recordings-dir",
            str(recordings),
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert result.returncode == 0, f"CLI failed:\n{result.stdout}\n{result.stderr}"
    assert "State:    completed" in result.stdout
    assert "workflow_completed" in result.stdout
