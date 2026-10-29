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
import os
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
        "tests/test_g30_capability_enforcement.py::test_a_cap_sets_max_tokens_when_the_step_asked_for_none",
    ),
    (
        "allowed_hosts: the step's policy never reaches the connector",
        "src/workflow_platform/tools/browser.py",
        "    return caps.host_allowed if caps is not None and caps.restricts_hosts else None\n",
        "    return None\n",
        "tests/test_g30_capability_enforcement.py::test_the_step_capabilities_reach_the_connector_through_the_tool",
    ),
    (
        "allowed_hosts: navigate stops checking the URL first",
        "src/workflow_platform/connectors/browser/playwright_connector.py",
        "        self.check_url(url)\n        await self._arm()\n",
        "        await self._arm()\n",
        "tests/test_g30_capability_enforcement.py::test_navigation_to_a_disallowed_host_is_refused_before_any_request",
    ),
    (
        "allowed_hosts: fetch_url lets the API context follow redirects again",
        "src/workflow_platform/connectors/browser/playwright_connector.py",
        "        response = await self._fetch_checked(url)\n",
        "        response = await self._page.context.request.get(url)\n",
        "tests/test_g30_capability_enforcement.py::test_fetch_url_follows_redirects_one_checked_hop_at_a_time",
    ),
    (
        "allowed_hosts: read_text stops checking where the page is",
        "src/workflow_platform/connectors/browser/playwright_connector.py",
        "        await self._guard()\n        # `_page` is Any",
        "        # `_page` is Any",
        "tests/test_g30_capability_enforcement.py::test_nothing_reads_or_acts_on_a_page_that_is_off_list",
    ),
    (
        "loader: a misspelt key is accepted (fails open)",
        "src/workflow_platform/security/capabilities.py",
        "    if missing or unknown:\n",
        "    if missing:\n",
        "tests/test_system_capabilities.py::test_the_loader_refuses_a_layer_that_could_fail_open",
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
        "tests/test_system_capabilities.py::test_health_reports_the_layer_as_counts_never_contents",
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
        "tests/test_g30_function_capabilities.py::test_a_denied_read_raises_and_is_never_a_skip",
    ),
    (
        "phase 2: a denial moves inside the skip-on-failure try (becomes a silent skip)",
        "src/workflow_platform/engine/functions.py",
        "        _require_path(context, path, write=False)  # outside the try: a denial is not a skip\n        try:\n            payload = await world.fs.read_bytes(path)\n",
        "        try:\n            _require_path(context, path, write=False)\n            payload = await world.fs.read_bytes(path)\n",
        "tests/test_g30_function_capabilities.py::test_a_denied_read_raises_and_is_never_a_skip",
    ),
    (
        "phase 2: a file function stops consulting capabilities",
        "src/workflow_platform/engine/functions.py",
        "    _require_path(context, path, write=True)\n    await world.fs.write_bytes(path, buf.getvalue().encode())\n",
        "    await world.fs.write_bytes(path, buf.getvalue().encode())\n",
        "tests/test_g30_function_capabilities.py::test_a_denied_write_raises_before_anything_is_written",
    ),
    (
        "matcher: back to PurePosixPath.match (admits ..)",
        "src/workflow_platform/security/capabilities.py",
        "    return candidate.full_match(pattern)\n",
        "    return PurePosixPath(path).match(pattern)\n",
        "tests/test_g30_function_capabilities.py::test_the_matcher",
    ),
    (
        "matcher: '..' segments no longer refused",
        "src/workflow_platform/security/capabilities.py",
        '    if ".." in candidate.parts:\n        return False\n',
        "",
        "tests/test_g30_function_capabilities.py::test_the_matcher",
    ),
    (
        "pattern rule: relative patterns accepted again",
        "src/workflow_platform/security/capabilities.py",
        '            if not pattern.startswith("/") or ".." in PurePosixPath(pattern).parts:\n',
        "            if False:\n",
        "tests/test_g30_function_capabilities.py::test_a_pattern_the_matcher_could_never_honour_is_refused",
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
    (
        "review: the id rule is switched off (traversal ids load again)",
        "src/workflow_platform/workflow/definition.py",
        '    if not _SAFE_ID.match(value) or ".." in value:\n',
        "    if False:\n",
        "tests/test_id_traversal.py::test_an_unsafe_workflow_id_is_refused",
    ),
    (
        "review: the memory manager stops containing its paths",
        "src/workflow_platform/memory/manager.py",
        "        if not path.resolve().is_relative_to(self.base_dir.resolve()):\n",
        "        if False:\n",
        "tests/test_id_traversal.py::test_memory_refuses_an_id_that_resolves_outside_its_directory",
    ),
    (
        "review: fetch_url joins the agent's file name onto the directory again",
        "src/workflow_platform/connectors/browser/playwright_connector.py",
        "        target = self._contained(dest_filename)\n",
        "        target = self.downloads_dir / dest_filename\n",
        "tests/test_g30_capability_enforcement.py::test_fetch_url_cannot_write_outside_the_downloads_directory",
    ),
    (
        "review: an operator script builds its engine without the layer",
        "tools/fire.py",
        "            system_capabilities=configured_system_capabilities(),\n",
        "",
        "tests/test_system_capabilities.py::"
        "test_every_engine_built_outside_tests_carries_a_system_layer",
    ),
    (
        "red-team 2: step capabilities written to the SHARED context again (the race)",
        "src/workflow_platform/engine/executor.py",
        '        step_context = context.model_copy(update={"capabilities": capabilities})\n        produced = await fn(step.config, step_context, self.world)\n',
        "        context.capabilities = capabilities\n        produced = await fn(step.config, context, self.world)\n",
        "tests/test_g30_function_capabilities.py::test_a_parallel_sibling_cannot_widen_a_steps_capabilities",
    ),
    (
        "red-team 3: the host check stops refusing parser-divergent URLs",
        "src/workflow_platform/connectors/browser/base.py",
        '    if "@" in parts.netloc or "%" in parts.netloc:\n        return False\n',
        "",
        "tests/test_g30_capability_enforcement.py::test_urls_the_two_parsers_could_read_differently_are_refused",
    ),
    (
        "red-team 1: the question-suggestion call escapes the cap",
        "src/workflow_platform/engine/executor.py",
        '                inference_config=cap_output_tokens(\n                    {"maxTokens": suggest.max_output_tokens}, self._system_token_cap()\n                ),\n',
        '                inference_config={"maxTokens": suggest.max_output_tokens},\n',
        "tests/test_g30_capability_enforcement.py::test_a_drafts_question_suggestion_cannot_exceed_the_system_cap",
    ),
    (
        "red-team 1: memory distillation sends no cap again",
        "src/workflow_platform/memory/learned.py",
        "                inference_config=cap_output_tokens(None, self.max_output_tokens),\n",
        "",
        "tests/test_g30_capability_enforcement.py::test_memory_distillation_carries_the_cap_and_is_unchanged_without_one",
    ),
    (
        "red-team 4: copy_files checks dest_dir instead of the file it writes (their mutant)",
        "src/workflow_platform/engine/functions.py",
        "        _require_path(context, out, write=True)\n        await world.fs.write_bytes(out, payload)\n",
        "        _require_path(context, dest_dir, write=True)\n        await world.fs.write_bytes(out, payload)\n",
        "tests/test_g30_function_capabilities.py::test_every_access_is_preceded_by_a_check_of_that_same_path",
    ),
    (
        "re-check: copy_files checks only the FIRST file it writes (their weaker mutant)",
        "src/workflow_platform/engine/functions.py",
        "        _require_path(context, out, write=True)\n        await world.fs.write_bytes(out, payload)\n",
        "        if not copied:\n            _require_path(context, out, write=True)\n        await world.fs.write_bytes(out, payload)\n",
        "tests/test_g30_function_capabilities.py::test_every_access_is_preceded_by_a_check_of_that_same_path",
    ),
    (
        "re-check: extract_archive checks only the FIRST member it writes",
        "src/workflow_platform/engine/functions.py",
        "        _require_path(context, out, write=True)\n        await world.fs.write_bytes(out, data)\n",
        "        if not extracted:\n            _require_path(context, out, write=True)\n        await world.fs.write_bytes(out, data)\n",
        "tests/test_g30_function_capabilities.py::test_every_access_is_preceded_by_a_check_of_that_same_path",
    ),
    (
        "the agent stops checking whether a tool is allowed (B1)",
        "src/workflow_platform/agent/agent.py",
        "            and not context.capabilities.tool_allowed(name)\n",
        "            and False\n",
        "tests/test_system_capabilities.py::test_the_shipped_layer_refuses_a_tool_it_does_not_list",
    ),
    (
        "E5 (live Chromium): the host check stops refusing parser-divergent URLs",
        "src/workflow_platform/connectors/browser/base.py",
        '    if not url.isascii() or any(\n        c == "\\\\" or c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in url\n    ):\n        return False\n    parts = urlsplit(url)\n    if parts.scheme not in ("http", "https") or not parts.hostname:\n        return False\n    if "@" in parts.netloc or "%" in parts.netloc:\n        return False\n',
        '    parts = urlsplit(url)\n    if parts.scheme not in ("http", "https") or not parts.hostname:\n        return False\n',
        "tests/test_browser_host_policy_live.py::"
        "test_a_backslash_cannot_smuggle_a_disallowed_host_past_fetch_url",
        "BROWSER_LIVE",
    ),
    (
        "D3 (real Postgres): a stored released vocabulary loads again",
        "src/workflow_platform/workflow/definition.py",
        "        if self.released_fields is not None:\n",
        "        if False:\n",
        "tests/test_g29_withdrawn_postgres.py::test_a_stored_declaration_is_refused_on_load",
        "TEST_DATABASE_URL",
    ),
    (
        "R1: the service passes no layer again",
        "src/workflow_platform/main.py",
        "        system_capabilities=configured_system_capabilities(),\n",
        "        system_capabilities=None,\n",
        "tests/test_system_capabilities.py::"
        "test_the_service_default_refuses_an_unlisted_tool_on_a_real_file",
    ),
    (
        "R1: unset means no layer again",
        "src/workflow_platform/security/capabilities.py",
        "        return load_system_capabilities(SHIPPED_SYSTEM_CAPABILITIES)\n",
        "        return None  # type: ignore[return-value]\n",
        "tests/test_system_capabilities.py::"
        "test_the_service_started_with_nothing_set_runs_the_shipped_layer",
    ),
    (
        "R1: the image stops carrying the layer",
        "Dockerfile",
        "COPY config/system_capabilities.yaml /app/config/system_capabilities.yaml\n",
        "",
        "tests/test_system_capabilities.py::"
        "test_the_image_carries_the_layer_where_the_service_looks_for_it",
    ),
    (
        "R2: the policy operation drops its lock",
        "src/workflow_platform/connectors/browser/base.py",
        "        async with self._operation_lock:\n            self._host_allowed = allowed\n            yield\n",
        "        self._host_allowed = allowed\n        yield\n",
        "tests/test_g30_capability_enforcement.py::"
        "test_a_parallel_sibling_cannot_widen_a_step_host_policy_mid_call",
    ),
    (
        "R2: a tool calls the connector outside a policy operation",
        "src/workflow_platform/tools/browser.py",
        "            async with connector.operation(_host_policy(context)):\n"
        "                text = await connector.read_text(sel)\n",
        "            text = await connector.read_text(sel)\n",
        "tests/test_g30_capability_enforcement.py::"
        "test_every_browser_tool_acts_only_inside_one_policy_operation",
    ),
    (
        "R3: every run shares one downloads directory again",
        "src/workflow_platform/engine/executor.py",
        "            run_downloads = self.browser_downloads_dir / instance_id\n",
        "            run_downloads = self.browser_downloads_dir\n",
        "tests/test_engine_browser_integration.py::"
        "test_two_runs_saving_the_same_name_keep_both_outputs",
    ),
    (
        "R4: an upload stops consulting file_read",
        "src/workflow_platform/tools/browser.py",
        "        if caps is not None and not caps.can_read(file_path):\n",
        "        if False:\n",
        "tests/test_browser_tools.py::test_upload_outside_file_read_never_reaches_the_page",
    ),
]


def pytest(target: str) -> int:
    r = subprocess.run(
        ["uv", "run", "pytest", target, "-q", "-p", "no:warnings"], capture_output=True, text=True
    )
    return r.returncode


ok = True
skipped = 0
for label, rel, old, new, target, *requires in CONTROLS:
    # An optional 6th field names the env var an opt-in suite needs. Without
    # it the control is reported NOT RUN — never counted as firing.
    if requires and not os.environ.get(requires[0]):
        print(f"--- {label}\n    NOT RUN (opt-in: set {requires[0]})")
        skipped += 1
        continue
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
print(
    ("\nALL CONTROLS FIRED" if ok else "\nA CONTROL DID NOT FIRE")
    + (f" ({skipped} opt-in not run)" if skipped else "")
)
sys.exit(0 if ok else 1)
