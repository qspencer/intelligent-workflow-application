"""FastAPI application entrypoint.

Phase 0 / Week 1: only the health endpoint exists. Routers, auth, DB, WebSocket,
and the workflow API arrive in later weeks.
"""

from __future__ import annotations

from fastapi import FastAPI

from workflow_platform import __version__

app = FastAPI(title="Intelligent Workflow Platform", version=__version__)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
