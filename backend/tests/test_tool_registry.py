"""Tests for ToolRegistry."""

from __future__ import annotations

import pytest

from workflow_platform.agent import ToolRegistry
from workflow_platform.tools import FileReadTool, FileWriteTool, PdfExtractTool


def test_registry_register_and_get() -> None:
    registry = ToolRegistry([FileReadTool(), FileWriteTool()])
    assert "file_read" in registry
    assert "file_write" in registry
    assert isinstance(registry.get("file_read"), FileReadTool)
    assert registry.get("nope") is None


def test_registry_rejects_duplicate_names() -> None:
    registry = ToolRegistry([FileReadTool()])
    with pytest.raises(ValueError, match="already registered"):
        registry.register(FileReadTool())


def test_registry_to_bedrock_tool_config_empty() -> None:
    assert ToolRegistry().to_bedrock_tool_config() is None


def test_registry_to_bedrock_tool_config_renders_specs() -> None:
    registry = ToolRegistry([FileReadTool(), PdfExtractTool()])
    config = registry.to_bedrock_tool_config()
    assert config is not None
    names = {t["toolSpec"]["name"] for t in config["tools"]}
    assert names == {"file_read", "pdf_extract"}


def test_registry_names_sorted() -> None:
    registry = ToolRegistry([PdfExtractTool(), FileReadTool(), FileWriteTool()])
    assert registry.names() == ["file_read", "file_write", "pdf_extract"]
    assert len(registry) == 3
