"""G30 controls: sabotage each detector with the defect it claims to catch.

Run from `backend/`:   .venv/bin/python ../docs/review-artifacts/g30_controls.py

A detector that has never been seen to fail is a claim, not a control. Each
entry plants its defect on the check's OWN input, asserts the targeted test now
fails (exit 1), restores the file, and asserts it passes again (exit 0).

A sabotaged Python file is parsed with the project interpreter first, so a
syntax break cannot be read as a control firing — Python 3.14 accepts PEP 758
`except A, B:`, which the system 3.12 rejects, and an early version of this
harness mistook exactly that for a break. Only `.py` files are parsed: a later
version parsed the Markdown guide too, and recorded three controls as syntax
failures instead of running them.
"""

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
CONTROLS = [
    (
        "max_tokens_per_call: the clamp is bypassed",
        "src/workflow_platform/agent/agent.py",
        "                inference_config=inference_config,\n",
        "                inference_config=self.policy.inference_config,\n",
        "tests/test_g30_capability_enforcement.py",
    ),
    (
        "allowed_hosts: the step's policy never reaches the connector",
        "src/workflow_platform/tools/browser.py",
        "    conn.set_host_policy(caps.host_allowed if caps is not None and caps.restricts_hosts else None)\n",
        "    conn.set_host_policy(None)\n",
        "tests/test_g30_capability_enforcement.py",
    ),
    (
        "allowed_hosts: navigate stops checking the URL first",
        "src/workflow_platform/connectors/browser/playwright_connector.py",
        "        self.check_url(url)\n        await self._arm()\n",
        "        await self._arm()\n",
        "tests/test_g30_capability_enforcement.py",
    ),
    (
        "allowed_hosts: fetch_url lets the API context follow redirects again",
        "src/workflow_platform/connectors/browser/playwright_connector.py",
        "        response = await self._fetch_checked(url)\n",
        "        response = await self._page.context.request.get(url)\n",
        "tests/test_g30_capability_enforcement.py",
    ),
    (
        "allowed_hosts: read_text stops checking where the page is",
        "src/workflow_platform/connectors/browser/playwright_connector.py",
        "        await self._guard()\n        # `_page` is Any",
        "        # `_page` is Any",
        "tests/test_g30_capability_enforcement.py",
    ),
    (
        "loader: a misspelt key is accepted (fails open)",
        "src/workflow_platform/security/capabilities.py",
        "    if missing or unknown:\n",
        "    if missing:\n",
        "tests/test_system_capabilities.py",
    ),
    (
        "layer widened without updating the declarations",
        "../config/system_capabilities.yaml",
        "file_read: []\n",
        'file_read: ["/**"]\n',
        "tests/test_system_capabilities.py",
    ),
    (
        "the launcher stops naming the layer",
        "../scripts/run-local-be.sh",
        "export WORKFLOW_PLATFORM_SYSTEM_CAPABILITIES=",
        "# export WORKFLOW_PLATFORM_SYSTEM_CAPABILITIES=",
        "tests/test_system_capabilities.py",
    ),
    (
        "health leaks the tool names",
        "src/workflow_platform/security/capabilities.py",
        '        "tools": shape(policy.tools),\n',
        '        "tools": policy.tools,\n',
        "tests/test_system_capabilities.py",
    ),
    (
        "an armed workflow goes undeclared",
        "tests/test_system_capabilities.py",
        '    "webhook-echo": RUNS,\n',
        "",
        "tests/test_system_capabilities.py",
    ),
]


def pytest(target: str) -> int:
    r = subprocess.run(
        ["uv", "run", "pytest", target, "-q", "-p", "no:warnings"], capture_output=True, text=True
    )
    return r.returncode


ok = True
for label, rel, old, new, target in CONTROLS:
    f = ROOT / rel
    original = f.read_text()
    n = original.count(old)
    if n != 1:
        print(f"--- {label}\n    ANCHOR FOUND {n}x in {rel} - control invalid")
        ok = False
        continue
    f.write_text(original.replace(old, new, 1))
    try:
        if f.suffix == ".py":  # only Python can have a syntax break
            ast.parse(f.read_text())
        sabotaged = pytest(target)
    except SyntaxError as e:
        sabotaged = f"SYNTAX({e.lineno})"  # type: ignore[assignment]
    finally:
        f.write_text(original)
    restored = pytest(target)
    fired = sabotaged == 1 and restored == 0
    ok &= fired
    print(
        f"--- {label}\n    sabotaged exit={sabotaged} (want 1)   restored exit={restored} "
        f"(want 0)   {'FIRED' if fired else 'DID NOT FIRE'}"
    )
print("\nALL CONTROLS FIRED" if ok else "\nA CONTROL DID NOT FIRE")
sys.exit(0 if ok else 1)
