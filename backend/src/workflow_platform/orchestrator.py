"""Trigger orchestrator — loads workflow YAMLs from a directory and wires
their declared triggers against the running workflow engine.

Without this, filesystem and schedule triggers exist but nothing in the
running FastAPI process actually starts them — dropping a PDF in the inbox
folder doesn't do anything. The orchestrator closes that gap.

Lifecycle:

- `TriggerOrchestrator.start()` reads `definitions_dir`, parses each YAML,
  registers the definition in the repo, instantiates the right trigger
  plugin from `definition.trigger.type`, and starts it. Each trigger's
  `on_event` callback runs `engine.run(definition, payload)` with errors
  caught and logged (a misbehaving event source must not crash the server).
- `TriggerOrchestrator.stop()` stops every trigger started during `start()`.

Errors at any per-definition step (parse / unknown trigger type / trigger
constructor failure) are logged at WARNING and the orchestrator moves on
to the next file. The server stays up.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from workflow_platform.connectors.email import (
    GmailConnector,
    GmailOAuthProvider,
)
from workflow_platform.engine import WorkflowEngine
from workflow_platform.memory import MemoryManager
from workflow_platform.persistence import Repositories
from workflow_platform.secrets import SecretStore
from workflow_platform.triggers import (
    FilesystemTrigger,
    GmailPollTrigger,
    ScheduleTrigger,
    Trigger,
    WebhookRegistry,
    WebhookTrigger,
)
from workflow_platform.workflow import WorkflowDefinition, load_definition_from_file

logger = logging.getLogger(__name__)

TriggerFactory = Callable[[dict[str, Any]], Trigger]

MEMORY_FILE_NAME = "agent_memory.md"


async def seed_memory_from_workflow_dir(
    definition: WorkflowDefinition,
    yaml_path: Path,
    memory: MemoryManager | None,
) -> bool:
    """If `<yaml_path>.parent/agent_memory.md` exists, write it as the pinned
    memory for every agentic step in `definition`. No-op if `memory` is None
    or the file is missing.

    The rubric is the same for every agentic step in a workflow today;
    per-step rubrics can be added later via an `agent_memory/<step_id>.md`
    convention if a workload needs them. Returns True if any seeding happened.
    """
    if memory is None:
        return False
    memory_path = yaml_path.parent / MEMORY_FILE_NAME
    if not memory_path.is_file():
        return False
    content = memory_path.read_text()
    seeded = False
    for step in definition.steps:
        if step.type != "agentic":
            continue
        agent_id = f"steps/{definition.id}/{step.id}"
        await memory.write_raw(agent_id, content)
        seeded = True
    return seeded


class TriggerOrchestrator:
    def __init__(
        self,
        *,
        definitions_dir: Path,
        repositories: Repositories,
        engine: WorkflowEngine,
        webhook_registry: WebhookRegistry,
        secret_store: SecretStore | None = None,
    ) -> None:
        self.definitions_dir = definitions_dir
        self.repositories = repositories
        self.engine = engine
        self.webhook_registry = webhook_registry
        # `secret_store` is only required for triggers that need credentials
        # (e.g., `gmail_poll`). Workflows that don't use them work without it.
        self.secret_store = secret_store
        self._started: list[Trigger] = []

    async def start(self) -> None:
        if not self.definitions_dir.exists():
            logger.warning(
                "WORKFLOW_DEFINITIONS_DIR=%s does not exist; no triggers started.",
                self.definitions_dir,
            )
            return

        yaml_files = sorted(self.definitions_dir.rglob("*.yaml")) + sorted(
            self.definitions_dir.rglob("*.yml")
        )
        if not yaml_files:
            logger.warning(
                "No *.yaml or *.yml files under %s; no triggers started.",
                self.definitions_dir,
            )
            return

        for path in yaml_files:
            try:
                await self._register_one(path)
            except Exception:
                logger.exception("Failed to register workflow from %s; skipping.", path)

    async def stop(self) -> None:
        for trigger in self._started:
            try:
                await trigger.stop()
            except Exception:
                logger.exception("Error stopping trigger %r", trigger)
        self._started.clear()

    async def _register_one(self, path: Path) -> None:
        definition = load_definition_from_file(path)
        await self.repositories.definitions.save(definition)

        if await seed_memory_from_workflow_dir(definition, path, self.engine.memory):
            logger.info("Seeded agent memory for workflow %s from %s", definition.id, path.name)

        trigger = self._make_trigger(definition)
        if trigger is None:
            return

        callback = self._make_callback(definition)
        await trigger.start(callback)
        self._started.append(trigger)
        logger.info(
            "Started %s trigger for workflow %s (from %s)",
            definition.trigger.type,
            definition.id,
            path.name,
        )

    def _make_trigger(self, definition: WorkflowDefinition) -> Trigger | None:
        spec = definition.trigger
        config = dict(spec.config or {})
        try:
            if spec.type in ("file_watch", "filesystem"):
                return FilesystemTrigger(
                    folder=config.get("path") or config.get("folder", "."),
                    pattern=config.get("pattern", "*"),
                    recursive=bool(config.get("recursive", False)),
                )
            if spec.type == "schedule":
                tz = config.get("timezone") or config.get("timezone_name")
                return ScheduleTrigger(
                    cron=config.get("cron"),
                    interval_seconds=config.get("interval_seconds"),
                    timezone_name=tz,
                )
            if spec.type == "webhook":
                trigger_id = config.get("trigger_id") or definition.id
                return WebhookTrigger(
                    self.webhook_registry,
                    trigger_id,
                    secret_name=config.get("secret_name"),
                )
            if spec.type == "gmail_poll":
                if self.secret_store is None:
                    logger.warning(
                        "Workflow %s: gmail_poll trigger requires a SecretStore; "
                        "TriggerOrchestrator was constructed without one. Skipping.",
                        definition.id,
                    )
                    return None
                account = config.get("account")
                if not isinstance(account, str) or not account:
                    logger.warning(
                        "Workflow %s: gmail_poll trigger requires `account` in config; skipping.",
                        definition.id,
                    )
                    return None
                auth_provider = GmailOAuthProvider(account=account, secret_store=self.secret_store)
                connector = GmailConnector(account=account, auth_provider=auth_provider)
                return GmailPollTrigger(
                    connector=connector,
                    poll_interval_seconds=float(config.get("poll_interval_seconds", 60.0)),
                    label=config.get("label", "INBOX"),
                    max_messages=int(config.get("max_messages", 50)),
                    query=config.get("query"),
                    download_dir=config.get("download_dir"),
                )
            if spec.type == "manual":
                # `manual` is a deliberate no-op — definitions tagged this way
                # are fired via tools/fire.py or the (planned) HTTP endpoint.
                return None
            logger.warning(
                "Workflow %s: unknown trigger type %r; skipping.",
                definition.id,
                spec.type,
            )
        except Exception:
            logger.exception(
                "Workflow %s: failed to construct %s trigger; skipping.",
                definition.id,
                spec.type,
            )
        return None

    def _make_callback(
        self, definition: WorkflowDefinition
    ) -> Callable[[dict[str, Any]], Awaitable[None]]:
        engine = self.engine

        async def callback(payload: dict[str, Any]) -> None:
            try:
                instance = await engine.run(definition, trigger_payload=payload)
                logger.info(
                    "workflow %s fired by trigger %s → instance %s state=%s",
                    definition.id,
                    definition.trigger.type,
                    instance.id,
                    instance.state.value,
                )
            except Exception:
                # The engine itself catches step failures and marks the instance
                # FAILED. Anything that escapes engine.run is a bug at a layer
                # below the engine — log and continue so the trigger keeps
                # working for subsequent events.
                logger.exception(
                    "Unhandled error firing workflow %s; the trigger will keep running.",
                    definition.id,
                )

        return callback
