"""Schemathesis contract / fuzz validation against the in-process app (opt-in).

Derives test cases from FastAPI's own OpenAPI schema and fuzzes each endpoint:
the property we assert is `not_a_server_error` — no fuzzed input (bad query
params, junk path ids, odd headers) may ever produce a 5xx. This catches
unhandled-input crashes + contract drift that the hand-written endpoint tests
don't, with near-zero maintenance (the schema is generated from the app).

Scope: **read-only GET operations only.** That keeps it self-contained — no
Bedrock calls (scaffold / dry-run), no state mutation, no trigger firing, no
dependence on recordings — so a failure is a real bug, not an external miss.

Opt-in via `SCHEMA_TESTS=1`; deselected by default like the live / integration
suites. Run with:

    SCHEMA_TESTS=1 uv run pytest -m schema
"""

from __future__ import annotations

import os
from typing import Any

import pytest

pytestmark = pytest.mark.schema

if os.environ.get("SCHEMA_TESTS") != "1":
    pytest.skip(
        "SCHEMA_TESTS not set; skipping schemathesis contract tests",
        allow_module_level=True,
    )

# Imports below the skip so the default suite doesn't build the app / schema.
import schemathesis  # noqa: E402
from schemathesis.checks import not_a_server_error  # noqa: E402

from workflow_platform.main import create_app  # noqa: E402
from workflow_platform.persistence import in_memory_repositories  # noqa: E402

os.environ.setdefault("AUTH_MODE", "dev")
os.environ.setdefault("BEDROCK_MODE", "replay")

# Authenticate as admin so role gates pass and we exercise real handler logic
# rather than bouncing off the auth middleware.
_DEV_HEADERS = {"X-Dev-User": "schemathesis", "X-Dev-Groups": "admins"}

_app = create_app(repositories=in_memory_repositories())
schema = schemathesis.openapi.from_asgi("/openapi.json", _app).include(method="GET")


@schema.parametrize()
def test_get_endpoints_never_500(case: Any) -> None:
    # `case` is a schemathesis.Case (generic in v4); typed as Any to avoid
    # threading its type parameters through a test.
    case.call_and_validate(headers=_DEV_HEADERS, checks=[not_a_server_error])
