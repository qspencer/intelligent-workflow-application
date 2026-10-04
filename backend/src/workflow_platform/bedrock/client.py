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
import logging
import os
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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


logger = logging.getLogger(__name__)

#: Errors that say "try again later", not "this request is wrong". Everything
#: else (validation, access, quota, unknown model) fails on the first attempt.
RETRYABLE_ERROR_CODES = frozenset(
    {
        "ServiceUnavailableException",
        "ThrottlingException",
        "InternalServerException",
        "ModelNotReadyException",
    }
)


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with equal jitter for one model call.

    Separate from step retries (`runtime.retries`, EXECUTION_SEMANTICS §4),
    and safe for EVERY step — tool-holding ones included — because a call
    that failed returned nothing: no tool ran for that turn, so repeating
    the request repeats no effect. Motivated by 2026-09-29: a Bedrock
    outage failed seven triage runs with `ServiceUnavailableException`
    after botocore's own retries, whose whole window is a few seconds.

    Defaults: 7 attempts, delays doubling from 2 s capped at 60 s (about
    2-4 min in all), and never more than `max_total_delay` of waiting.
    botocore's quick retries still run inside each attempt."""

    max_attempts: int = 7
    base_delay: float = 2.0
    max_delay: float = 60.0
    max_total_delay: float = 300.0

    def delay(self, retry: int, rand: Callable[[], float] = random.random) -> float:
        """Wait before retry number `retry` (1-based): half the capped
        exponential step, plus up to the other half at random, so parallel
        runs do not retry in lockstep but the wait still grows."""
        step = min(self.max_delay, self.base_delay * 2.0 ** (retry - 1))
        return step / 2 + rand() * step / 2


def _retryable(exc: Exception) -> str | None:
    """The transient-error name to log, or None for a permanent error."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if code in RETRYABLE_ERROR_CODES:
            return str(code)
    # botocore's network-level failures: the request may never have arrived.
    name = type(exc).__name__
    if name in (
        "EndpointConnectionError",
        "ConnectionClosedError",
        "ReadTimeoutError",
        "ConnectTimeoutError",
    ):
        return name
    return None


class BedrockClient:
    """Async wrapper around Bedrock `converse` with record/replay support."""

    def __init__(
        self,
        mode: BedrockMode | None = None,
        recordings_dir: Path | str | None = None,
        region: str | None = None,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self.mode = mode or BedrockMode(os.environ.get("BEDROCK_MODE", BedrockMode.LIVE))
        self.retry_policy = retry_policy or RetryPolicy()
        self._sleep = sleep
        self._rand = rand
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

        response = await self._call_live_with_retry(request)

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

    async def _call_live_with_retry(self, request: dict[str, Any]) -> dict[str, Any]:
        policy = self.retry_policy
        waited = 0.0
        attempt = 1
        while True:
            try:
                return await asyncio.to_thread(self._call_live, request)
            except Exception as exc:
                reason = _retryable(exc)
                if reason is None or attempt >= policy.max_attempts:
                    if reason is not None:
                        logger.error(
                            "Bedrock %s on %s: giving up after %d attempts (%.0fs waited)",
                            reason,
                            request.get("modelId"),
                            attempt,
                            waited,
                        )
                    raise
                delay = policy.delay(attempt, self._rand)
                if waited + delay > policy.max_total_delay:
                    logger.error(
                        "Bedrock %s on %s: giving up, retry budget of %.0fs spent (%d attempts)",
                        reason,
                        request.get("modelId"),
                        policy.max_total_delay,
                        attempt,
                    )
                    raise
                logger.warning(
                    "Bedrock %s on %s: retry %d/%d in %.1fs",
                    reason,
                    request.get("modelId"),
                    attempt,
                    policy.max_attempts - 1,
                    delay,
                )
                await self._sleep(delay)
                waited += delay
                attempt += 1

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
