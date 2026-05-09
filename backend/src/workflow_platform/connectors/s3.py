"""S3Connector — AWS S3 as an event source and a destination.

For Phase 2 / Week 7:
- send(payload) → PutObject. Payload: { "key": str, "body": bytes|str,
  optional "content_type": str }.
- query(params) → ListObjectsV2 (params.kind="list", prefix=...) or
  GetObject (kind="get", key=...). Returns list of keys / object body.
- trigger_poll() → list new keys since the last poll, using an in-memory
  cursor of "keys we've already seen". Sufficient for week-7 polling
  workflows; EventBridge-driven push triggers land later.

Authentication uses boto3's default credential chain (env vars, ~/.aws,
instance profile). A SecretStore-stored credential override is out of scope
for week 7.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from workflow_platform.connectors.base import Connector


class S3Connector(Connector):
    type: ClassVar[str] = "s3"

    def __init__(
        self,
        bucket: str,
        *,
        region: str | None = None,
        list_prefix: str = "",
        client: Any = None,  # injectable for tests
    ) -> None:
        self.bucket = bucket
        self.region = region
        self.list_prefix = list_prefix
        self._client: Any = client
        self._seen_keys: set[str] = set()

    @property
    def client(self) -> Any:
        if self._client is None:
            import boto3

            self._client = boto3.client("s3", region_name=self.region)
        return self._client

    async def authenticate(self) -> None:
        # boto3's default credential chain runs on first call; verify by hitting
        # head_bucket so misconfigurations surface here rather than mid-workflow.
        await asyncio.to_thread(self.client.head_bucket, Bucket=self.bucket)

    async def health_check(self) -> bool:
        try:
            await asyncio.to_thread(self.client.head_bucket, Bucket=self.bucket)
            return True
        except Exception:
            return False

    async def trigger_poll(self) -> list[dict[str, Any]]:
        response = await asyncio.to_thread(
            self.client.list_objects_v2, Bucket=self.bucket, Prefix=self.list_prefix
        )
        contents = response.get("Contents") or []
        new_events: list[dict[str, Any]] = []
        for obj in contents:
            key = obj["Key"]
            if key in self._seen_keys:
                continue
            self._seen_keys.add(key)
            new_events.append(
                {
                    "bucket": self.bucket,
                    "key": key,
                    "size": obj.get("Size"),
                    "etag": obj.get("ETag"),
                }
            )
        return new_events

    async def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        key = payload.get("key")
        body = payload.get("body")
        if not isinstance(key, str) or not key:
            raise ValueError("S3Connector.send requires `key`")
        if body is None:
            raise ValueError("S3Connector.send requires `body`")
        body_bytes = body.encode("utf-8") if isinstance(body, str) else bytes(body)
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": key, "Body": body_bytes}
        if "content_type" in payload:
            kwargs["ContentType"] = payload["content_type"]
        await asyncio.to_thread(self.client.put_object, **kwargs)
        return {"bucket": self.bucket, "key": key, "bytes": len(body_bytes)}

    async def query(self, params: dict[str, Any]) -> dict[str, Any]:
        kind = params.get("kind", "list")
        if kind == "list":
            prefix = params.get("prefix", self.list_prefix)
            response = await asyncio.to_thread(
                self.client.list_objects_v2, Bucket=self.bucket, Prefix=prefix
            )
            return {"keys": [obj["Key"] for obj in response.get("Contents") or []]}
        if kind == "get":
            key = params.get("key")
            if not isinstance(key, str) or not key:
                raise ValueError("S3Connector.query kind='get' requires `key`")
            response = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
            body = await asyncio.to_thread(response["Body"].read)
            return {"key": key, "body": body.decode("utf-8", errors="replace")}
        raise ValueError(f"Unknown S3Connector query kind: {kind!r}")
