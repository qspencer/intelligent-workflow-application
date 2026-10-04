"""Exponential-backoff retry around a single Bedrock call.

2026-09-29: a Bedrock outage failed seven triage runs with
`ServiceUnavailableException` after botocore's own retries, whose window is a
few seconds. These pin the wider, logged, transient-only retry layer.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from workflow_platform.bedrock import BedrockClient, BedrockMode, RetryPolicy

OK = {"output": {"message": {"role": "assistant", "content": [{"text": "hi"}]}}}


def _err(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "SYNTHETIC"}}, "Converse")


class _Fake:
    """A boto client whose `converse` raises the queued errors, then answers."""

    def __init__(self, errors: list[Exception]) -> None:
        self.errors = list(errors)
        self.calls = 0

    def converse(self, **request: Any) -> dict[str, Any]:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return dict(OK)


def _client(errors: list[Exception], **policy: Any) -> tuple[BedrockClient, _Fake, list[float]]:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    client = BedrockClient(
        mode=BedrockMode.LIVE,
        retry_policy=RetryPolicy(**policy),
        sleep=sleep,
        rand=lambda: 0.5,
    )
    fake = _Fake(errors)
    client._client = fake
    return client, fake, slept


async def _call(client: BedrockClient) -> dict[str, Any]:
    return await client.converse(
        model_id="m", messages=[{"role": "user", "content": [{"text": "x"}]}]
    )


async def test_transient_errors_back_off_exponentially_then_succeed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, fake, slept = _client(
        [
            _err("ServiceUnavailableException"),
            _err("ThrottlingException"),
            _err("InternalServerException"),
        ]
    )
    caplog.set_level(logging.WARNING)
    assert await _call(client) == OK
    assert fake.calls == 4
    # base 2 s doubling, equal jitter at rand=0.5 → 3/4 of each step
    assert slept == [1.5, 3.0, 6.0]
    # Every retry is LOGGED with its cause — the 09-29 failures left no trace.
    retries = [r.getMessage() for r in caplog.records if "retry" in r.getMessage()]
    assert len(retries) == 3 and "ServiceUnavailableException" in retries[0]
    assert all("SYNTHETIC" not in r for r in retries), "no provider message text in logs"


async def test_a_permanent_error_fails_at_once() -> None:
    client, fake, slept = _client([_err("ValidationException")])
    with pytest.raises(ClientError, match="ValidationException"):
        await _call(client)
    assert fake.calls == 1 and slept == []


async def test_exhausted_retries_reraise_the_original_error() -> None:
    client, fake, slept = _client([_err("ServiceUnavailableException")] * 10, max_attempts=4)
    with pytest.raises(ClientError, match="ServiceUnavailableException"):
        await _call(client)
    assert fake.calls == 4 and len(slept) == 3


async def test_the_delay_is_capped_and_so_is_the_total_wait() -> None:
    policy = RetryPolicy(base_delay=2.0, max_delay=10.0)
    assert [policy.delay(n, lambda: 1.0) for n in (1, 2, 3, 4, 5)] == [2.0, 4.0, 8.0, 10.0, 10.0]
    client, _, slept = _client(
        [_err("ThrottlingException")] * 10, max_attempts=10, max_delay=10.0, max_total_delay=12.0
    )
    with pytest.raises(ClientError):
        await _call(client)
    assert sum(slept) <= 12.0, slept


async def test_a_dropped_connection_is_retried() -> None:
    client, fake, _ = _client([EndpointConnectionError(endpoint_url="https://bedrock")])
    assert await _call(client) == OK
    assert fake.calls == 2


async def test_replay_mode_never_calls_or_retries(tmp_path: Any) -> None:
    client = BedrockClient(mode=BedrockMode.REPLAY, recordings_dir=tmp_path)
    with pytest.raises(Exception, match=r"(?i)recording"):
        await _call(client)
