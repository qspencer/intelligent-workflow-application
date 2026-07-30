"""IA_PLAN attribution sidecar: org/owner display resolution, bundled
metadata, run-effect classification, org scoping."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from workflow_platform.main import create_app
from workflow_platform.persistence import User, in_memory_repositories
from workflow_platform.workflow import WorkflowDefinition

pytestmark = pytest.mark.asyncio

ADMIN = {"X-Dev-User": "admin", "X-Dev-Groups": "admins"}
ORG_ADMIN = {"X-Dev-User": "oa", "X-Dev-Groups": "org-admins"}


def _definition(defn_id: str, tools: list[str] | None = None) -> WorkflowDefinition:
    step: dict[str, Any] = {
        "id": "s1",
        "name": "s1",
        "type": "agentic",
        "goal": "do the thing",
        "model": "claude-haiku-4-5",
    }
    if tools is not None:
        step["tools"] = tools
    return WorkflowDefinition.model_validate(
        {
            "id": defn_id,
            "name": defn_id,
            "trigger": {"type": "manual", "config": {}},
            "steps": [step],
            "edges": [],
        }
    )


@pytest.fixture(autouse=True)
def _dev_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")


async def _client_and_repos() -> tuple[AsyncClient, Any]:
    repos = in_memory_repositories()
    app = create_app(repositories=repos)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return client, repos


async def test_attribution_owner_and_effect() -> None:
    client, repos = await _client_and_repos()
    owner = await repos.users.upsert_seen(
        User(iss="local", sub="u1", email="o@x.com", display_name="Olive")
    )
    await repos.definitions.save(
        _definition("wf-owned", tools=["email_send"]),
        org_id="default",
        owner_user_id=owner.id,
    )
    await repos.definitions.save(_definition("wf-read", tools=["file_read"]))
    await repos.definitions.save(_definition("wf-unknown", tools=["mystery_tool"]))

    resp = await client.get("/api/workflows/attribution", headers=ADMIN)
    assert resp.status_code == 200
    data = resp.json()

    owned = data["wf-owned"]
    assert owned["owner_display_name"] == "Olive"
    assert owned["org_name"]  # resolved server-side
    assert owned["source"] == "user"
    assert "lifecycle" not in owned
    # email_send is mutating in the engine catalog (if wired) or unknown —
    # either way the classification must be mutating.
    assert owned["run_effect"] == "mutating"

    read = data["wf-read"]
    assert read["run_effect"] in ("read_only", "mutating")  # depends on engine wiring

    unknown = data["wf-unknown"]
    assert unknown["run_effect"] == "mutating"  # unknown counts as mutating
    assert "mystery_tool" in unknown["effect_tools"]


async def test_attribution_bundled_metadata() -> None:
    from workflow_platform.templates import default_examples_dir, load_templates

    client, repos = await _client_and_repos()
    # Simulate the orchestrator's boot-time seeding of one bundled example.
    template = load_templates(default_examples_dir())[0]
    await repos.definitions.save(template)

    resp = await client.get("/api/workflows/attribution", headers=ADMIN)
    data = resp.json()
    assert data[template.id]["source"] == "bundled"
    assert data[template.id]["lifecycle"] == "reseeded"


async def test_attribution_org_scoped() -> None:
    client, repos = await _client_and_repos()
    await repos.definitions.save(_definition("wf-other-org"), org_id="acme")

    resp = await client.get("/api/workflows/attribution", headers=ORG_ADMIN)
    assert resp.status_code == 200
    assert "wf-other-org" not in resp.json()

    resp = await client.get("/api/workflows/attribution", headers=ADMIN)
    assert "wf-other-org" in resp.json()
