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
from typing import Any

from fastapi import FastAPI

from workflow_platform import __version__
from workflow_platform.api.workflows import build_router
from workflow_platform.auth import AuthMiddleware
from workflow_platform.engine import WorkflowEngine
from workflow_platform.persistence import Repositories, in_memory_repositories
from workflow_platform.persistence.db import make_engine, make_session_factory
from workflow_platform.persistence.postgres import postgres_repositories
from workflow_platform.triggers import WebhookRegistry

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


def create_app(
    repositories: Repositories | None = None,
    *,
    engine: WorkflowEngine | None = None,
    webhook_registry: WebhookRegistry | None = None,
) -> FastAPI:
    db_engine: Any | None = None
    if repositories is None:
        repositories, db_engine = _build_repositories()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if db_engine is not None and hasattr(db_engine, "dispose"):
                await db_engine.dispose()

    app = FastAPI(
        title="Intelligent Workflow Platform",
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(AuthMiddleware)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(build_router(repositories, engine=engine, webhook_registry=webhook_registry))
    return app


app = create_app()
