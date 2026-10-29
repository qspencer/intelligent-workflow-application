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
    (
        "a browser tool is enabled before destinations are enforced",
        "../config/system_capabilities.yaml",
        "  - email_label_apply__qspencer_gmail_com\n",
        "  - email_label_apply__qspencer_gmail_com\n  - browser_navigate\n",
        "tests/test_system_capabilities.py::"
        "test_the_browser_stays_off_until_destinations_are_enforced_before_dispatch",
    ),
    (
        "phase 2: copy_files stops checking what it reads",
        "src/workflow_platform/engine/functions.py",
        "        _require_path(context, path, write=False)  # outside the try: a denial is not a skip\n        try:\n            payload",
        "        try:\n            payload",
        "tests/test_g30_function_capabilities.py",
    ),
    (
        "phase 2: a denial moves inside the skip-on-failure try (becomes a silent skip)",
        "src/workflow_platform/engine/functions.py",
        "        _require_path(context, path, write=False)  # outside the try: a denial is not a skip\n        try:\n            payload = await world.fs.read_bytes(path)\n",
        "        try:\n            _require_path(context, path, write=False)\n            payload = await world.fs.read_bytes(path)\n",
        "tests/test_g30_function_capabilities.py",
    ),
    (
        "phase 2: a file function stops consulting capabilities",
        "src/workflow_platform/engine/functions.py",
        "    _require_path(context, path, write=True)\n    await world.fs.write_bytes(path, buf.getvalue().encode())\n",
        "    await world.fs.write_bytes(path, buf.getvalue().encode())\n",
        "tests/test_g30_function_capabilities.py",
    ),
    (
        "matcher: back to PurePosixPath.match (admits ..)",
        "src/workflow_platform/security/capabilities.py",
        "    return candidate.full_match(pattern)\n",
        "    return PurePosixPath(path).match(pattern)\n",
        "tests/test_g30_function_capabilities.py",
    ),
    (
        "matcher: '..' segments no longer refused",
        "src/workflow_platform/security/capabilities.py",
        '    if ".." in candidate.parts:\n        return False\n',
        "",
        "tests/test_g30_function_capabilities.py",
    ),
    (
        "pattern rule: relative patterns accepted again",
        "src/workflow_platform/security/capabilities.py",
        '            if not pattern.startswith("/") or ".." in PurePosixPath(pattern).parts:\n',
        "            if False:\n",
        "tests/test_g30_function_capabilities.py",
    ),
    (
        "layer: DMARC spool pattern one level too shallow",
        "../config/system_capabilities.yaml",
        "  - /home/ubuntu/.local/share/workflow-platform/dmarc/spool/*/*\n",
        "  - /home/ubuntu/.local/share/workflow-platform/dmarc/spool/*\n",
        "tests/test_g30_function_capabilities.py::test_dmarc_ingest_delivers_under_the_shipped_layer",
    ),
    (
        "layer: widened enough to run invoice-extraction, declaration not updated",
        "../config/system_capabilities.yaml",
        "tools:\n  - email_label_apply__qspencer_gmail_com\n\n# Exactly the paths the live deterministic steps touch, and nothing else.\nfile_read:\n  # dmarc-ingest / extract_reports — the trigger spools one folder per message\n  - /home/ubuntu/.local/share/workflow-platform/dmarc/spool/*/*\n  # dmarc-ingest / deliver_reports — reads what extract_reports wrote\n  - /home/ubuntu/.local/share/workflow-platform/dmarc/extracted/*\n  # scheduled-health-report / append — appending reads the log it appends to\n  - /home/ubuntu/.local/share/workflow-platform/health/scheduled-health-report.log\nfile_write:\n  # dmarc-ingest / extract_reports\n  - /home/ubuntu/.local/share/workflow-platform/dmarc/extracted/*\n  # dmarc-ingest / deliver_reports — the viewer watches this directory\n  - /home/ubuntu/Dev/dmarc-viewer/xml-files/*\n  # scheduled-health-report / append\n  - /home/ubuntu/.local/share/workflow-platform/health/scheduled-health-report.log\n\n",
        "tools:\n  - email_label_apply__qspencer_gmail_com\n  - email_send\n\nfile_read:\n  - /**\nfile_write:\n  - /**\n\n",
        "tests/test_system_capabilities.py::"
        "test_the_layer_does_exactly_what_is_declared_to_each_armed_workflow",
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
