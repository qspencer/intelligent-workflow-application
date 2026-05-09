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

from fastapi import FastAPI

from workflow_platform import __version__
from workflow_platform.api.workflows import build_router
from workflow_platform.auth import AuthMiddleware
from workflow_platform.persistence import Repositories, in_memory_repositories
from workflow_platform.persistence.db import make_engine, make_session_factory
from workflow_platform.persistence.postgres import postgres_repositories

logger = logging.getLogger(__name__)


def _build_repositories() -> tuple[Repositories, object | None]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        logger.info("DATABASE_URL not set; using in-memory repositories.")
        return in_memory_repositories(), None
    logger.info("Using Postgres repositories (DATABASE_URL=%s).", url)
    engine = make_engine(url)
    session_factory = make_session_factory(engine)
    return postgres_repositories(session_factory), engine


def create_app(repositories: Repositories | None = None) -> FastAPI:
    engine: object | None = None
    if repositories is None:
        repositories, engine = _build_repositories()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if engine is not None and hasattr(engine, "dispose"):
                await engine.dispose()

    app = FastAPI(
        title="Intelligent Workflow Platform",
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(AuthMiddleware)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(build_router(repositories))
    return app


app = create_app()
