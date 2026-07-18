"""Tests for the authoring catalog + GET /api/catalog (C7.2)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from workflow_platform.catalog import build_catalog
from workflow_platform.engine import ToolCatalog, default_function_registry
from workflow_platform.main import create_app
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.tools import FileReadTool, FileWriteTool

_H = {"X-Dev-User": "a", "X-Dev-Groups": "org-viewers"}


def test_build_catalog_functions_have_descriptions() -> None:
    cat = build_catalog(default_function_registry(), ToolCatalog([]))
    names = {f.name for f in cat.functions}
    assert {"noop", "append_file", "pdf_extract"} <= names
    noop = next(f for f in cat.functions if f.name == "noop")
    assert noop.description  # pulled from the function docstring


def test_build_catalog_tools_are_categorized() -> None:
    cat = build_catalog(default_function_registry(), ToolCatalog([FileReadTool(), FileWriteTool()]))
    tools = {t.name: t for t in cat.tools}
    assert tools["file_read"].category == "filesystem"
    assert tools["file_write"].description


def test_build_catalog_has_all_trigger_types() -> None:
    cat = build_catalog(default_function_registry(), ToolCatalog([]))
    assert {t.type for t in cat.triggers} == {
        "manual",
        "filesystem",
        "schedule",
        "webhook",
        "email",
    }
    fs = next(t for t in cat.triggers if t.type == "filesystem")
    path = next(f for f in fs.config_fields if f.name == "path")
    assert path.required is True


def test_catalog_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app(repositories=in_memory_repositories()))

    body = client.get("/api/catalog", headers=_H).json()
    assert any(t["type"] == "manual" for t in body["triggers"])
    assert "noop" in {f["name"] for f in body["functions"]}
    assert "file_read" in {t["name"] for t in body["tools"]}
