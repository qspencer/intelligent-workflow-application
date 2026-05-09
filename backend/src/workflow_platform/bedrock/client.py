"""Bedrock `converse` client with record/replay modes.

The wrapper exists so that every agent test in the project can run deterministically
without Bedrock credentials. It supports three modes:

- LIVE: real `bedrock-runtime` calls. Default for application use.
- RECORD: real calls plus persistence of every (request, response) pair.
- REPLAY: never calls Bedrock. Loads responses from disk by hashing the request.

Tests default to REPLAY (configured in conftest). To regenerate fixtures, run with
BEDROCK_MODE=record and Bedrock credentials in the environment.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class BedrockMode(StrEnum):
    LIVE = "live"
    RECORD = "record"
    REPLAY = "replay"


class RecordingNotFoundError(LookupError):
    """Raised when REPLAY mode cannot find a recording for a request."""

    def __init__(self, request_hash: str, recordings_dir: Path, request: dict[str, Any]):
        self.request_hash = request_hash
        self.recordings_dir = recordings_dir
        self.request = request
        preview = json.dumps(request, indent=2, sort_keys=True)[:500]
        super().__init__(
            f"No recording found for request hash {request_hash} in {recordings_dir}.\n"
            f"To regenerate, set BEDROCK_MODE=record and re-run the test.\n"
            f"Request preview:\n{preview}"
        )


def _canonicalize(payload: dict[str, Any]) -> str:
    """Stable JSON serialization for hashing: sorted keys, no whitespace."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash_request(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonicalize(payload).encode("utf-8")).hexdigest()


class BedrockClient:
    """Async wrapper around Bedrock `converse` with record/replay support."""

    def __init__(
        self,
        mode: BedrockMode | None = None,
        recordings_dir: Path | str | None = None,
        region: str | None = None,
    ) -> None:
        self.mode = mode or BedrockMode(os.environ.get("BEDROCK_MODE", BedrockMode.LIVE))
        self.recordings_dir = Path(
            recordings_dir or os.environ.get("BEDROCK_RECORDINGS_DIR", "recordings")
        )
        self.region = region or os.environ.get("AWS_REGION", "us-east-1")
        self._client: Any = None

    @property
    def client(self) -> Any:
        """Lazily-initialized boto3 client. Not used in REPLAY mode."""
        if self.mode == BedrockMode.REPLAY:
            raise RuntimeError("REPLAY mode does not call Bedrock; no client is created")
        if self._client is None:
            import boto3

            self._client = boto3.client("bedrock-runtime", region_name=self.region)
        return self._client

    async def converse(
        self,
        model_id: str,
        messages: list[dict[str, Any]],
        system: list[dict[str, Any]] | None = None,
        tool_config: dict[str, Any] | None = None,
        inference_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call Bedrock `converse`, recording or replaying as configured.

        Returns the raw boto3 response dict (without recording metadata).
        """
        request = self._build_request(
            model_id=model_id,
            messages=messages,
            system=system,
            tool_config=tool_config,
            inference_config=inference_config,
        )
        request_hash = _hash_request(request)

        if self.mode == BedrockMode.REPLAY:
            return self._load_recording(request_hash, request)

        response = await asyncio.to_thread(self._call_live, request)

        if self.mode == BedrockMode.RECORD:
            self._save_recording(request_hash, request, response)

        return response

    def _build_request(
        self,
        *,
        model_id: str,
        messages: list[dict[str, Any]],
        system: list[dict[str, Any]] | None,
        tool_config: dict[str, Any] | None,
        inference_config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {"modelId": model_id, "messages": messages}
        if system is not None:
            request["system"] = system
        if tool_config is not None:
            request["toolConfig"] = tool_config
        if inference_config is not None:
            request["inferenceConfig"] = inference_config
        return request

    def _call_live(self, request: dict[str, Any]) -> dict[str, Any]:
        response = self.client.converse(**request)
        return _strip_metadata(dict(response))

    def _recording_path(self, request_hash: str) -> Path:
        return self.recordings_dir / f"{request_hash}.json"

    def _load_recording(self, request_hash: str, request: dict[str, Any]) -> dict[str, Any]:
        path = self._recording_path(request_hash)
        if not path.is_file():
            raise RecordingNotFoundError(request_hash, self.recordings_dir, request)
        with path.open() as f:
            data = json.load(f)
        response = data["response"]
        if not isinstance(response, dict):
            raise ValueError(f"Recording {path} has non-dict response")
        return response

    def _save_recording(
        self, request_hash: str, request: dict[str, Any], response: dict[str, Any]
    ) -> None:
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        path = self._recording_path(request_hash)
        record = {
            "schema_version": SCHEMA_VERSION,
            "request_hash": request_hash,
            "recorded_at": datetime.now(UTC).isoformat(),
            "request": request,
            "response": response,
        }
        path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


def _strip_metadata(response: dict[str, Any]) -> dict[str, Any]:
    """Drop boto3-specific noise (ResponseMetadata) so recordings are stable across runs."""
    return {k: v for k, v in response.items() if k != "ResponseMetadata"}
