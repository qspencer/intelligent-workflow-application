FROM python:3.14-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# System deps for PyMuPDF / OCR
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        poppler-utils \
        libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

# Built from the REPO ROOT (`docker build -f backend/Dockerfile .`; compose does
# this) so the image can carry the system capability layer, which lives at
# <repo>/config/. backend/Dockerfile.dockerignore is an allowlist: nothing else
# from the repo — .secrets, mail data, the memory store — reaches the daemon.
#
# The layout mirrors the repo (/app/backend, /app/config) so the service finds
# the shipped layer exactly as it does on the host. Without it the service
# refuses to start; it never runs unrestricted (G30 external review R1).
WORKDIR /app/backend

COPY backend/pyproject.toml ./
RUN uv sync --no-dev --no-install-project

COPY backend/src ./src
RUN uv sync --no-dev

COPY config/system_capabilities.yaml /app/config/system_capabilities.yaml

EXPOSE 8001

CMD ["uv", "run", "uvicorn", "workflow_platform.main:app", "--host", "0.0.0.0", "--port", "8001"]
