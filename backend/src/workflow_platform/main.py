"""FastAPI application entrypoint.

Phase 0 / Week 3: a `/health` endpoint plus read-only `/api/workflows`,
`/api/workflow-instances/{id}`, and `/api/audit` endpoints. The app picks
between Postgres-backed and in-memory repositories based on `DATABASE_URL`
so it remains usable without a database for early development.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import Response

from workflow_platform import __version__
from workflow_platform.api.auth import build_auth_router
from workflow_platform.api.dev import build_dev_router
from workflow_platform.api.organizations import build_organizations_router
from workflow_platform.api.users import build_users_router
from workflow_platform.api.workflows import build_router
from workflow_platform.api.ws import build_ws_router
from workflow_platform.auth import AuthMiddleware, LocalAuthService, auth_mode
from workflow_platform.auth.bootstrap import ensure_seed_users
from workflow_platform.auth.provisioning import UserProvisioner
from workflow_platform.bedrock import BedrockClient
from workflow_platform.connectors.email import maybe_build_gmail_connector
from workflow_platform.connectors.email.bootstrap import credentialed_accounts
from workflow_platform.engine import (
    ToolCatalog,
    WorkflowEngine,
    default_function_registry,
)
from workflow_platform.engine.functions import TRIAGE_CATEGORIES
from workflow_platform.events import EventBus
from workflow_platform.memory import LearnedMemoryService, MemoryManager
from workflow_platform.observability import (
    CONTENT_TYPE,
    ErrorBuffer,
    ErrorCaptureHandler,
    PrometheusMetrics,
    configure_logging,
)
from workflow_platform.orchestrator import TriggerOrchestrator
from workflow_platform.persistence import Repositories, in_memory_repositories
from workflow_platform.persistence.db import make_engine, make_session_factory
from workflow_platform.persistence.postgres import postgres_repositories
from workflow_platform.secrets import AwsSecretsManagerStore, EnvSecretStore, SecretStore
from workflow_platform.templates import default_examples_dir
from workflow_platform.tools import (
    EmailLabelApplyTool,
    EmailSendTool,
    FileReadTool,
    FileWriteTool,
    PdfExtractTool,
    Tool,
)
from workflow_platform.triggers import WebhookRegistry
from workflow_platform.world import real_world

configure_logging(
    level=logging.INFO,
    json_output=os.environ.get("LOG_FORMAT", "json").lower() != "text",
)
logger = logging.getLogger(__name__)


def _build_repositories() -> tuple[Repositories, Any | None]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        logger.info("DATABASE_URL not set; using in-memory repositories.")
        return in_memory_repositories(), None
    logger.info("Using Postgres repositories (DATABASE_URL=%s).", url)
    db_engine = make_engine(url)
    session_factory = make_session_factory(db_engine)
    return postgres_repositories(session_factory), db_engine


def _default_engine(
    repositories: Repositories,
    metrics: PrometheusMetrics,
    events: EventBus | None,
    memory: MemoryManager,
    secret_store: SecretStore,
) -> WorkflowEngine:
    """Construct an engine wired to the same repos the API serves from.

    Real Bedrock / real filesystem; the BedrockClient defaults to `live` but
    respects the `BEDROCK_MODE` env var so tests / replay still work. If
    `WORKFLOW_PLATFORM_GMAIL_ACCOUNT` is set and credentials are reachable,
    `EmailSendTool` + `EmailLabelApplyTool` join the default catalog."""
    bedrock = BedrockClient()
    learned_db = os.environ.get(
        "WORKFLOW_PLATFORM_LEARNED_MEMORY_DB",
        str(Path(os.environ.get("WORKFLOW_PLATFORM_MEMORY_DIR", ".memory")) / "learned.db"),
    )
    return WorkflowEngine(
        repositories=repositories,
        functions=default_function_registry(),
        tools=ToolCatalog(_build_default_tools(secret_store)),
        bedrock=bedrock,
        world=real_world(),
        metrics=metrics,
        events=events,
        memory=memory,
        learned_memory=LearnedMemoryService(bedrock, learned_db),
    )


def _build_default_tools(secret_store: SecretStore) -> list[Tool]:
    """Always-on tools + optional Gmail tools (gated on
    `WORKFLOW_PLATFORM_GMAIL_ACCOUNT` + reachable credentials)."""
    tools: list[Tool] = [PdfExtractTool(), FileReadTool(), FileWriteTool()]
    account = os.environ.get("WORKFLOW_PLATFORM_GMAIL_ACCOUNT")
    gmail_connector = maybe_build_gmail_connector(account=account, secret_store=secret_store)
    if gmail_connector is not None:
        tools.extend([EmailSendTool(gmail_connector), EmailLabelApplyTool(gmail_connector)])
        logger.info("Wired Gmail tools (email_send, email_label_apply) for account %r.", account)
    # Per-account label tools (EMAIL_TRIAGE_ACT_PLAN §4): every credentialed
    # account gets `email_label_apply:<account>`, allowlisted to the wf/*
    # triage namespace — the capability allowlist then names WHICH mailbox a
    # step may write, and the C6 panel shows it. Add-only by construction.
    for extra_account in credentialed_accounts():
        connector = maybe_build_gmail_connector(account=extra_account, secret_store=secret_store)
        if connector is None:
            continue
        tools.append(
            EmailLabelApplyTool(
                connector,
                name=f"email_label_apply:{extra_account}",
                allowed_labels=[f"wf/{c}" for c in TRIAGE_CATEGORIES],
            )
        )
        logger.info("Wired email_label_apply:%s (wf/* labels only).", extra_account)
    return tools


def _default_secret_store() -> SecretStore:
    """Pick a SecretStore by env: `aws` for the deployed stack,
    `env` (default) for solo-dev."""
    backend = os.environ.get("WORKFLOW_PLATFORM_SECRET_BACKEND", "env").lower()
    if backend == "aws":
        return AwsSecretsManagerStore()
    return EnvSecretStore()


_DEV_ERROR_BUFFER: ErrorBuffer | None = None


def _dev_error_buffer() -> ErrorBuffer:
    """Process-wide error buffer for the dev dashboard header. Created on first
    use, with the capturing handler attached to the root logger exactly once so
    repeated ``create_app()`` calls (tests) don't stack handlers."""
    global _DEV_ERROR_BUFFER
    if _DEV_ERROR_BUFFER is None:
        _DEV_ERROR_BUFFER = ErrorBuffer()
        logging.getLogger().addHandler(ErrorCaptureHandler(_DEV_ERROR_BUFFER))
    return _DEV_ERROR_BUFFER


async def _instance_org(repositories: Repositories, instance_id: str) -> str | None:
    instance = await repositories.instances.get(instance_id)
    return instance.org_id if instance else None


def create_app(
    repositories: Repositories | None = None,
    *,
    engine: WorkflowEngine | None = None,
    webhook_registry: WebhookRegistry | None = None,
    events: EventBus | None = None,
    metrics: PrometheusMetrics | None = None,
    definitions_dir: Path | None = None,
    start_triggers: bool | None = None,
    secret_store: SecretStore | None = None,
) -> FastAPI:
    db_engine: Any | None = None
    if repositories is None:
        repositories, db_engine = _build_repositories()

    metrics = metrics or PrometheusMetrics()
    webhook_registry = webhook_registry or WebhookRegistry()
    events = events or EventBus()
    # ROLES_PLAN §4b: stamp events with their instance's org at emit time so
    # the WS filter is a plain field compare.
    events.set_org_resolver(lambda iid: _instance_org(repositories, iid))
    memory_dir = os.environ.get("WORKFLOW_PLATFORM_MEMORY_DIR", ".memory")
    memory = MemoryManager(memory_dir)
    secret_store = secret_store or _default_secret_store()
    engine = engine or _default_engine(repositories, metrics, events, memory, secret_store)

    # Default-on in production; off in tests unless a test explicitly opts in.
    if start_triggers is None:
        start_triggers = os.environ.get("WORKFLOW_PLATFORM_START_TRIGGERS", "1") != "0"
    if definitions_dir is None:
        env_dir = os.environ.get("WORKFLOW_DEFINITIONS_DIR")
        definitions_dir = Path(env_dir) if env_dir else default_examples_dir()

    orchestrator = TriggerOrchestrator(
        definitions_dir=definitions_dir,
        repositories=repositories,
        engine=engine,
        webhook_registry=webhook_registry,
        secret_store=secret_store,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await ensure_seed_users(repositories)
        if start_triggers:
            await orchestrator.start()
        try:
            yield
        finally:
            await orchestrator.stop()
            if db_engine is not None and hasattr(db_engine, "dispose"):
                await db_engine.dispose()

    app = FastAPI(
        title="Intelligent Workflow Platform",
        version=__version__,
        lifespan=lifespan,
    )
    local_auth = LocalAuthService(
        repositories.users, repositories.auth_sessions, repositories.audit
    )
    app.add_middleware(
        AuthMiddleware,
        provisioner=UserProvisioner(repositories.users),
        local_auth=local_auth,
    )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        return Response(content=metrics.render(), media_type=CONTENT_TYPE)

    app.include_router(
        build_router(
            repositories,
            engine=engine,
            webhook_registry=webhook_registry,
            templates_dir=definitions_dir,
            secret_store=secret_store,
        )
    )
    app.include_router(build_ws_router(events, local_auth=local_auth, repositories=repositories))
    app.include_router(build_users_router(repositories))
    app.include_router(build_organizations_router(repositories))
    if auth_mode() == "local":
        app.include_router(build_auth_router(local_auth))

    # Dev-only: capture ERROR logs into a ring buffer the dashboard header polls.
    # Attach the handler once per process (create_app may run many times in
    # tests); never mounted behind a real IdP.
    if auth_mode() == "dev":
        app.include_router(build_dev_router(_dev_error_buffer()))

    return app


app = create_app()
