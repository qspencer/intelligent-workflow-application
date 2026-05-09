"""Tests for the Bedrock wrapper's record/replay behavior."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from workflow_platform.bedrock import BedrockClient, BedrockMode, RecordingNotFoundError
from workflow_platform.bedrock.client import _hash_request

SAMPLE_REQUEST: dict[str, Any] = {
    "modelId": "anthropic.claude-3-haiku-20240307-v1:0",
    "messages": [{"role": "user", "content": [{"text": "Say hi"}]}],
}

SAMPLE_RESPONSE: dict[str, Any] = {
    "output": {
        "message": {
            "role": "assistant",
            "content": [{"text": "Hi there!"}],
        }
    },
    "stopReason": "end_turn",
    "usage": {"inputTokens": 5, "outputTokens": 3, "totalTokens": 8},
}


def _seed_recording(recordings_dir: Path, request: dict[str, Any], response: dict[str, Any]) -> str:
    request_hash = _hash_request(request)
    recordings_dir.mkdir(parents=True, exist_ok=True)
    (recordings_dir / f"{request_hash}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "request_hash": request_hash,
                "recorded_at": "2026-05-09T00:00:00+00:00",
                "request": request,
                "response": response,
            }
        )
    )
    return request_hash


async def test_replay_returns_recorded_response(tmp_path: Path) -> None:
    _seed_recording(tmp_path, SAMPLE_REQUEST, SAMPLE_RESPONSE)
    client = BedrockClient(mode=BedrockMode.REPLAY, recordings_dir=tmp_path)

    response = await client.converse(
        model_id=SAMPLE_REQUEST["modelId"],
        messages=SAMPLE_REQUEST["messages"],
    )

    assert response == SAMPLE_RESPONSE


async def test_replay_misses_raise_with_helpful_message(tmp_path: Path) -> None:
    client = BedrockClient(mode=BedrockMode.REPLAY, recordings_dir=tmp_path)

    with pytest.raises(RecordingNotFoundError) as excinfo:
        await client.converse(
            model_id="anthropic.claude-3-haiku-20240307-v1:0",
            messages=[{"role": "user", "content": [{"text": "no recording exists"}]}],
        )

    assert "BEDROCK_MODE=record" in str(excinfo.value)
    assert excinfo.value.recordings_dir == tmp_path


async def test_record_mode_persists_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = BedrockClient(mode=BedrockMode.RECORD, recordings_dir=tmp_path)
    fake_boto = MagicMock()
    fake_boto.converse.return_value = {**SAMPLE_RESPONSE, "ResponseMetadata": {"RequestId": "x"}}
    monkeypatch.setattr(client, "_client", fake_boto)

    response = await client.converse(
        model_id=SAMPLE_REQUEST["modelId"],
        messages=SAMPLE_REQUEST["messages"],
    )

    assert "ResponseMetadata" not in response
    assert response == SAMPLE_RESPONSE
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text())
    assert saved["request"] == {
        "modelId": SAMPLE_REQUEST["modelId"],
        "messages": SAMPLE_REQUEST["messages"],
    }
    assert saved["response"] == SAMPLE_RESPONSE


async def test_record_then_replay_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = BedrockClient(mode=BedrockMode.RECORD, recordings_dir=tmp_path)
    fake_boto = MagicMock()
    fake_boto.converse.return_value = SAMPLE_RESPONSE
    monkeypatch.setattr(recorder, "_client", fake_boto)
    await recorder.converse(model_id=SAMPLE_REQUEST["modelId"], messages=SAMPLE_REQUEST["messages"])

    replayer = BedrockClient(mode=BedrockMode.REPLAY, recordings_dir=tmp_path)
    response = await replayer.converse(
        model_id=SAMPLE_REQUEST["modelId"], messages=SAMPLE_REQUEST["messages"]
    )

    assert response == SAMPLE_RESPONSE


def test_hash_is_stable_across_key_order() -> None:
    a = {"modelId": "m", "messages": [{"role": "user", "content": [{"text": "x"}]}]}
    b = {"messages": [{"role": "user", "content": [{"text": "x"}]}], "modelId": "m"}
    assert _hash_request(a) == _hash_request(b)


def test_hash_differs_when_payload_differs() -> None:
    a = {"modelId": "m", "messages": [{"role": "user", "content": [{"text": "x"}]}]}
    b = {"modelId": "m", "messages": [{"role": "user", "content": [{"text": "y"}]}]}
    assert _hash_request(a) != _hash_request(b)


def test_replay_mode_refuses_to_create_boto_client(tmp_path: Path) -> None:
    client = BedrockClient(mode=BedrockMode.REPLAY, recordings_dir=tmp_path)
    with pytest.raises(RuntimeError, match="REPLAY mode"):
        _ = client.client
