"""Tests for OIDC validation, RBAC mapping, auth middleware, and endpoint guards."""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import MagicMock

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from workflow_platform.auth import (
    OidcConfig,
    OidcValidator,
    Role,
    UserIdentity,
    assign_roles,
    load_group_to_role_map,
)
from workflow_platform.main import create_app
from workflow_platform.persistence import (
    AuditEntry,
    WorkflowInstance,
    in_memory_repositories,
)
from workflow_platform.workflow import load_definition

# --- RBAC mapping ---


def test_default_group_to_role_map() -> None:
    mapping = load_group_to_role_map()
    assert mapping["admins"] == Role.ADMIN.value
    assert mapping["operators"] == Role.OPERATOR.value


def test_assign_roles_uses_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OIDC_GROUP_TO_ROLE", '{"finance-leads": "Admin"}')
    assert assign_roles(["finance-leads", "unknown"]) == ["Admin"]


def test_assign_roles_drops_unmapped() -> None:
    assert assign_roles(["unmapped-group"]) == []


# --- OIDC validation ---


def _make_keypair() -> tuple[bytes, Any]:
    """Generate an RSA keypair, return (PEM private key, public key object)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return private_pem, private_key.public_key()


@pytest.fixture
def signed_token_factory(monkeypatch: pytest.MonkeyPatch) -> Any:
    private_pem, public_key = _make_keypair()
    issuer = "https://idp.example.com"
    audience = "workflow-platform"
    monkeypatch.setenv("OIDC_ISSUER", issuer)
    monkeypatch.setenv("OIDC_AUDIENCE", audience)
    monkeypatch.setenv("OIDC_JWKS_URL", "https://idp.example.com/jwks")

    def _make(claims_overrides: dict[str, Any] | None = None) -> tuple[str, OidcValidator]:
        now = dt.datetime.now(tz=dt.UTC)
        claims = {
            "sub": "user-123",
            "aud": audience,
            "iss": issuer,
            "iat": int(now.timestamp()),
            "exp": int((now + dt.timedelta(minutes=5)).timestamp()),
            "email": "alice@example.com",
            "name": "Alice",
            "groups": ["operators"],
        }
        claims.update(claims_overrides or {})
        token = pyjwt.encode(claims, private_pem, algorithm="RS256")

        validator = OidcValidator()
        # Stub the JWKClient so it returns our public key without an HTTP fetch.
        signing_key = MagicMock()
        signing_key.key = public_key
        client = MagicMock()
        client.get_signing_key_from_jwt.return_value = signing_key
        validator._jwks_client = client
        return token, validator

    return _make


async def test_oidc_validator_accepts_valid_token(signed_token_factory: Any) -> None:
    token, validator = signed_token_factory()
    user = await validator.validate(token)
    assert isinstance(user, UserIdentity)
    assert user.sub == "user-123"
    assert user.email == "alice@example.com"
    assert user.groups == ["operators"]
    assert user.roles == [Role.OPERATOR.value]


async def test_oidc_validator_rejects_expired_token(signed_token_factory: Any) -> None:
    past = dt.datetime.now(tz=dt.UTC) - dt.timedelta(hours=1)
    token, validator = signed_token_factory(
        {"iat": int(past.timestamp()), "exp": int(past.timestamp()) + 60}
    )
    with pytest.raises(pyjwt.ExpiredSignatureError):
        await validator.validate(token)


async def test_oidc_validator_rejects_wrong_issuer(signed_token_factory: Any) -> None:
    token, validator = signed_token_factory({"iss": "https://attacker.example"})
    with pytest.raises(pyjwt.InvalidIssuerError):
        await validator.validate(token)


async def test_oidc_validator_rejects_wrong_audience(signed_token_factory: Any) -> None:
    token, validator = signed_token_factory({"aud": "different-app"})
    with pytest.raises(pyjwt.InvalidAudienceError):
        await validator.validate(token)


def test_oidc_unconfigured_raises_clear_error() -> None:
    validator = OidcValidator(OidcConfig(issuer="", audience="", jwks_url=""))
    with pytest.raises(RuntimeError, match="OIDC is not configured"):
        _ = validator.jwks_client


# --- Auth middleware + endpoint guards (dev mode) ---


@pytest.fixture
def dev_app(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    # Seed an instance + audit entries we can read through the API.
    instance = WorkflowInstance(workflow_id="wf-1", state="completed")

    async def _seed() -> None:
        await repos.instances.create(instance)
        await repos.audit.append(
            AuditEntry(
                actor_type="engine",
                actor_id="x",
                action="workflow_started",
                workflow_instance_id=instance.id,
            )
        )
        await repos.definitions.save(
            load_definition(
                {
                    "id": "wf-1",
                    "name": "wf-1",
                    "trigger": {"type": "manual"},
                    "steps": [{"id": "a", "type": "deterministic", "function": "noop"}],
                    "edges": [],
                }
            )
        )

    import asyncio

    asyncio.run(_seed())
    app = create_app(repositories=repos)
    return TestClient(app)


def test_health_does_not_require_auth(dev_app: TestClient) -> None:
    response = dev_app.get("/api/health")
    assert response.status_code == 200


def test_protected_endpoint_requires_dev_user(dev_app: TestClient) -> None:
    response = dev_app.get("/api/workflows")
    assert response.status_code == 401
    assert "X-Dev-User" in response.json()["detail"]


def test_protected_endpoint_with_dev_user(dev_app: TestClient) -> None:
    response = dev_app.get(
        "/api/workflows", headers={"X-Dev-User": "alice", "X-Dev-Groups": "viewers"}
    )
    assert response.status_code == 200
    assert response.json()[0]["id"] == "wf-1"


def test_audit_endpoint_forbidden_to_viewer(dev_app: TestClient) -> None:
    response = dev_app.get("/api/audit", headers={"X-Dev-User": "alice", "X-Dev-Groups": "viewers"})
    assert response.status_code == 403


def test_audit_endpoint_allowed_for_admin(dev_app: TestClient) -> None:
    response = dev_app.get("/api/audit", headers={"X-Dev-User": "root", "X-Dev-Groups": "admins"})
    assert response.status_code == 200


def test_audit_endpoint_allowed_for_auditor(dev_app: TestClient) -> None:
    response = dev_app.get(
        "/api/audit", headers={"X-Dev-User": "audit", "X-Dev-Groups": "auditors"}
    )
    assert response.status_code == 200


def test_oidc_mode_rejects_missing_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = create_app(repositories=in_memory_repositories())
    client = TestClient(app)
    response = client.get("/api/workflows")
    assert response.status_code == 401
    assert "Missing Bearer" in response.json()["detail"]


def test_oidc_mode_rejects_malformed_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = create_app(repositories=in_memory_repositories())
    client = TestClient(app)
    response = client.get("/api/workflows", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401
    assert "Invalid token" in response.json()["detail"]
