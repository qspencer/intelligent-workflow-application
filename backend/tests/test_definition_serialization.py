"""YAML and JSON round-tripping for workflow definitions."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from workflow_platform.main import create_app
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.workflow import (
    dump_definition_to_json,
    dump_definition_to_yaml,
    load_definition,
    load_definition_from_yaml,
)


def _sample_definition() -> dict[str, Any]:
    return {
        "id": "round-trip",
        "name": "Round Trip",
        "description": "A demo flow",
        "trigger": {"type": "manual", "config": {}},
        "steps": [
            {
                "id": "extract",
                "type": "deterministic",
                "function": "noop",
                "config": {"k": "v"},
            },
            {
                "id": "act",
                "type": "agentic",
                "goal": "do the thing",
                "model": "anthropic.claude-3-haiku-20240307-v1:0",
                "tools": [],
            },
        ],
        "edges": [{"from": "extract", "to": "act"}],
    }


def test_yaml_round_trip_preserves_definition() -> None:
    original = load_definition(_sample_definition())
    yaml_text = dump_definition_to_yaml(original)
    assert "round-trip" in yaml_text
    assert "extract" in yaml_text
    rebuilt = load_definition_from_yaml(yaml_text)
    assert rebuilt.model_dump() == original.model_dump()


def test_json_round_trip_preserves_definition() -> None:
    original = load_definition(_sample_definition())
    json_text = dump_definition_to_json(original)
    rebuilt = load_definition(json.loads(json_text))
    assert rebuilt.model_dump() == original.model_dump()


def test_ui_only_fields_round_trip() -> None:
    """`label` / `output_renderer` / `condition_label` survive YAML+JSON round trips."""
    spec = _sample_definition()
    spec["steps"][0]["label"] = "Pull out the text"
    spec["steps"][0]["output_renderer"] = "triage"
    spec["steps"][1]["label"] = "Decide the category"
    spec["edges"][0]["condition_label"] = "always"
    spec["edges"][0]["condition"] = "True"

    original = load_definition(spec)
    rebuilt = load_definition_from_yaml(dump_definition_to_yaml(original))
    assert rebuilt.model_dump() == original.model_dump()

    rebuilt_json = load_definition(json.loads(dump_definition_to_json(original)))
    assert rebuilt_json.steps[0].label == "Pull out the text"
    assert rebuilt_json.steps[0].output_renderer == "triage"
    assert rebuilt_json.edges[0].condition_label == "always"


def test_yaml_loader_rejects_non_mapping() -> None:
    from workflow_platform.workflow import WorkflowDefinitionError

    with pytest.raises(WorkflowDefinitionError, match="mapping"):
        load_definition_from_yaml("- one\n- two\n")


def _designer() -> dict[str, str]:
    return {"X-Dev-User": "alice", "X-Dev-Groups": "designers"}


def _viewer() -> dict[str, str]:
    return {"X-Dev-User": "bob", "X-Dev-Groups": "viewers"}


@pytest.fixture
def dev_app(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Any]:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    app = create_app(repositories=repos)
    return TestClient(app), repos


def test_export_endpoint_returns_json(dev_app: tuple[TestClient, Any]) -> None:
    import asyncio

    client, repos = dev_app
    asyncio.run(repos.definitions.save(load_definition(_sample_definition())))
    r = client.get("/api/workflows/round-trip/export?format=json", headers=_viewer())
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = json.loads(r.content)
    assert body["id"] == "round-trip"


def test_export_endpoint_returns_yaml(dev_app: tuple[TestClient, Any]) -> None:
    import asyncio

    client, repos = dev_app
    asyncio.run(repos.definitions.save(load_definition(_sample_definition())))
    r = client.get("/api/workflows/round-trip/export?format=yaml", headers=_viewer())
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/yaml")
    rebuilt = load_definition_from_yaml(r.content.decode("utf-8"))
    assert rebuilt.id == "round-trip"


def test_export_unknown_format_400(dev_app: tuple[TestClient, Any]) -> None:
    import asyncio

    client, repos = dev_app
    asyncio.run(repos.definitions.save(load_definition(_sample_definition())))
    r = client.get("/api/workflows/round-trip/export?format=xml", headers=_viewer())
    assert r.status_code == 400


def test_import_yaml_persists_definition(dev_app: tuple[TestClient, Any]) -> None:
    import asyncio

    client, repos = dev_app
    yaml_text = dump_definition_to_yaml(load_definition(_sample_definition()))
    r = client.post(
        "/api/workflows/import",
        content=yaml_text,
        headers={**_designer(), "Content-Type": "application/yaml"},
    )
    assert r.status_code == 200
    assert r.json() == {"status": "imported", "workflow_id": "round-trip"}
    fetched = asyncio.run(repos.definitions.get("round-trip"))
    assert fetched is not None


def test_import_json_persists_definition(dev_app: tuple[TestClient, Any]) -> None:
    import asyncio

    client, repos = dev_app
    body = json.dumps(_sample_definition())
    r = client.post(
        "/api/workflows/import",
        content=body,
        headers={**_designer(), "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert asyncio.run(repos.definitions.get("round-trip")) is not None


def test_import_requires_designer_role(dev_app: tuple[TestClient, Any]) -> None:
    client, _repos = dev_app
    r = client.post(
        "/api/workflows/import",
        content=json.dumps(_sample_definition()),
        headers={**_viewer(), "Content-Type": "application/json"},
    )
    assert r.status_code == 403


def test_import_invalid_returns_400(dev_app: tuple[TestClient, Any]) -> None:
    client, _repos = dev_app
    r = client.post(
        "/api/workflows/import",
        content=json.dumps({"id": "x"}),  # missing required fields
        headers={**_designer(), "Content-Type": "application/json"},
    )
    assert r.status_code == 400


def test_import_empty_body_returns_400(dev_app: tuple[TestClient, Any]) -> None:
    client, _repos = dev_app
    r = client.post(
        "/api/workflows/import",
        content=b"",
        headers={**_designer(), "Content-Type": "application/json"},
    )
    assert r.status_code == 400
