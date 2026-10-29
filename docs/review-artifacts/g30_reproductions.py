"""G30 — every claim in the review guide, the tests that execute it, and the
controls that prove those tests can fail.

Run from `backend/`, in the locked environment:

    uv run python ../docs/review-artifacts/g30_reproductions.py
    # add the opt-in suites, as the guide's claims D3 and E5 need:
    BROWSER_LIVE=1 TEST_DATABASE_URL=postgresql+asyncpg://<user>:<pw>@localhost:5432/<a db whose
        tables you can lose> uv run python ../docs/review-artifacts/g30_reproductions.py

Why this runs the TESTS rather than re-implementing them: the G29 package's
reproduction script duplicated test logic and printed one figure as a typed
string, so re-running it "reproduced" a number nobody had computed. Here there
is one implementation. Each claim names the test node ids that execute it; the
script runs them and maps every outcome back to its claim, and counts the
controls (`g30_controls.py`) that sabotage each test's target.

A claim whose suite is opt-in and not enabled is reported NOT RUN, with the
command — never as passing.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1] / "backend"
CONTROLS = HERE / "g30_controls.py"

SC = "tests/test_system_capabilities.py"
EN = "tests/test_g30_capability_enforcement.py"
FN = "tests/test_g30_function_capabilities.py"
ID = "tests/test_id_traversal.py"
LIVE = "tests/test_browser_host_policy_live.py"
PG = "tests/test_g29_withdrawn_postgres.py"
BE = "tests/test_engine_browser_integration.py"
BT = "tests/test_browser_tools.py"
PC = "tests/test_playwright_connector.py"

#: (claim id, claim as the guide states it, test node ids, opt-in env var or None)
CLAIMS: list[tuple[str, str, list[str], str | None]] = [
    (
        "A1",
        "The shipped system layer loads strictly: every attribute stated; a missing, "
        "misspelt or extra key, an unreadable file, or a set-but-empty variable refuses "
        "rather than running open; unset means the shipped layer, never none",
        [
            f"{SC}::test_the_shipped_layer_loads_with_every_attribute_stated",
            f"{SC}::test_the_loader_refuses_a_layer_that_could_fail_open",
            f"{SC}::test_an_unreadable_configured_layer_refuses_rather_than_running_open",
            f"{SC}::test_unset_means_the_shipped_layer",
            f"{SC}::test_set_but_empty_is_refused",
        ],
        None,
    ),
    (
        "A2",
        "Every engine — the service on every launch path and every operator script — "
        "runs under a system layer: the real service, built with nothing set, refuses an "
        "unlisted tool on a real file; the image carries the layer, and every place that "
        "builds it supplies the root context (review R1; round-2 R2-F1)",
        [
            f"{SC}::test_every_engine_built_outside_tests_carries_a_system_layer",
            f"{SC}::test_the_production_launcher_names_the_shipped_layer",
            f"{SC}::test_the_service_started_with_nothing_set_runs_the_shipped_layer",
            f"{SC}::test_the_service_runs_a_named_layer",
            f"{SC}::test_the_service_refuses_to_start_on_a_layer_it_cannot_use",
            f"{SC}::test_the_service_default_refuses_an_unlisted_tool_on_a_real_file",
            f"{SC}::test_the_image_carries_the_layer_where_the_service_looks_for_it",
            f"{SC}::test_every_image_build_site_is_known",
            f"{SC}::test_the_ecr_script_builds_from_the_root_context",
        ],
        None,
    ),
    (
        "A3",
        "Every armed workflow is accounted for: runs under the layer, or is declared "
        "blocked with the reason — checked both ways, by the paths each step touches",
        [
            f"{SC}::test_every_armed_workflow_is_accounted_for",
            f"{SC}::test_the_layer_does_exactly_what_is_declared_to_each_armed_workflow",
            f"{SC}::test_the_file_function_set_matches_the_source",
            f"{SC}::test_the_live_triage_tool_is_the_only_tool_in_production",
        ],
        None,
    ),
    (
        "A4",
        "/api/health reports the layer as counts, never names, through the real wiring",
        [
            f"{SC}::test_health_reports_the_layer_as_counts_never_contents",
            f"{SC}::test_health_reports_an_absent_layer_honestly",
        ],
        None,
    ),
    (
        "B1",
        "Through the real engine, a tool the layer does not list is refused — on a file "
        "that exists, beside a twin that reads it without the layer",
        [
            f"{SC}::test_the_shipped_layer_refuses_a_tool_it_does_not_list",
            f"{SC}::test_without_a_layer_the_same_call_reads_the_file",
        ],
        None,
    ),
    (
        "B2",
        "max_tokens_per_call clamps agent steps (most restrictive layer), question "
        "suggestion and memory distillation (system layer); uncapped requests are unchanged",
        [
            f"{EN}::test_no_cap_leaves_the_request_exactly_as_it_was",
            f"{EN}::test_a_cap_sets_max_tokens_when_the_step_asked_for_none",
            f"{EN}::test_a_step_asking_for_less_keeps_its_own_figure",
            f"{EN}::test_a_step_asking_for_more_is_clamped_and_other_settings_survive",
            f"{EN}::test_the_most_restrictive_layer_wins",
            f"{EN}::test_a_drafts_question_suggestion_cannot_exceed_the_system_cap",
            f"{EN}::test_memory_distillation_carries_the_cap_and_is_unchanged_without_one",
        ],
        None,
    ),
    (
        "C1",
        "The path matcher: absolute only, `..` refused, whole-path match, `**` recursive; "
        "patterns it could never honour are refused at authoring",
        [
            f"{FN}::test_the_matcher",
            f"{FN}::test_a_pattern_the_matcher_could_never_honour_is_refused",
        ],
        None,
    ),
    (
        "C2",
        "Every deterministic file function checks before access; a denial raises and is "
        "never a skip; the paths it needs suffice; no capabilities means no restriction",
        [
            f"{FN}::test_a_denied_write_raises_before_anything_is_written",
            f"{FN}::test_a_denied_read_raises_and_is_never_a_skip",
            f"{FN}::test_the_paths_it_needs_are_enough",
            f"{FN}::test_no_capabilities_in_context_means_no_restriction",
            f"{FN}::test_a_traversal_out_of_an_allowed_tree_is_refused",
        ],
        None,
    ),
    (
        "C3",
        "Every filesystem access follows a check of that SAME path in that same mode "
        "(correctness), and every function that touches the filesystem checks (presence)",
        [
            f"{FN}::test_every_access_is_preceded_by_a_check_of_that_same_path",
            f"{FN}::test_every_function_that_touches_the_filesystem_consults_capabilities",
            f"{FN}::test_every_registered_file_function_is_covered_by_the_behaviour_tests",
        ],
        None,
    ),
    (
        "C4",
        "A parallel sibling step cannot widen a step's capabilities",
        [f"{FN}::test_a_parallel_sibling_cannot_widen_a_steps_capabilities"],
        None,
    ),
    (
        "C5",
        "The live deterministic workflows run under the SHIPPED layer at their real paths, "
        "and a path outside it fails the run loudly",
        [
            f"{FN}::test_dmarc_ingest_delivers_under_the_shipped_layer",
            f"{FN}::test_dmarc_ingest_fails_loudly_on_a_path_outside_the_layer",
            f"{FN}::test_scheduled_health_report_appends_under_the_shipped_layer",
        ],
        None,
    ),
    (
        "D1",
        "Workflow and step ids are plain tokens; memory refuses paths outside its "
        "directory; import refuses a traversal id",
        [
            f"{ID}::test_an_unsafe_workflow_id_is_refused",
            f"{ID}::test_an_unsafe_step_id_is_refused",
            f"{ID}::test_ordinary_ids_still_load",
            f"{ID}::test_memory_refuses_an_id_that_resolves_outside_its_directory",
            f"{ID}::test_an_ordinary_run_still_loads_its_memory",
            f"{ID}::test_import_refuses_a_traversal_id",
        ],
        None,
    ),
    (
        "D3",
        "A definition stored around the API with a released vocabulary is refused on "
        "repository load (real Postgres) — the G29 closure's request",
        [f"{PG}::test_a_stored_declaration_is_refused_on_load"],
        "TEST_DATABASE_URL",
    ),
    (
        "E1",
        "allowed_hosts blocks agent-initiated requests before dispatch, and the top-level "
        "page is checked before every read and action (fakes)",
        [
            f"{EN}::test_no_restricting_layer_means_no_host_policy",
            f"{EN}::test_navigation_to_a_disallowed_host_is_refused_before_any_request",
            f"{EN}::test_globs_match_hosts",
            f"{EN}::test_hostless_and_non_http_urls_are_refused_under_a_policy",
            f"{EN}::test_a_redirect_landing_off_list_is_cleared_and_fails",
            f"{EN}::test_a_click_that_leaves_the_site_fails_and_clears",
            f"{EN}::test_an_aborted_navigation_reports_it_was_blocked",
            f"{EN}::test_nothing_reads_or_acts_on_a_page_that_is_off_list",
            f"{EN}::test_fetch_url_to_a_disallowed_host_makes_no_request",
            f"{EN}::test_fetch_url_follows_redirects_one_checked_hop_at_a_time",
            f"{EN}::test_fetch_url_on_list_redirects_still_work",
            f"{EN}::test_a_download_from_off_list_is_refused_before_it_is_saved",
            f"{EN}::test_no_policy_keeps_unrestricted_behaviour",
            f"{EN}::test_the_step_capabilities_reach_the_connector_through_the_tool",
            f"{EN}::test_route_aborts_only_off_list_navigations",
        ],
        None,
    ),
    (
        "E2",
        "URLs the host check and the browser could parse differently are refused",
        [
            f"{EN}::test_urls_the_two_parsers_could_read_differently_are_refused",
            f"{EN}::test_an_ordinary_url_still_passes",
        ],
        None,
    ),
    (
        "E3",
        "Browser outputs stay in the run's OWN downloads directory — one per instance — "
        "whatever name is supplied; a second run cannot overwrite the first's (review R3)",
        [
            f"{BE}::test_two_runs_saving_the_same_name_keep_both_outputs",
            f"{EN}::test_fetch_url_cannot_write_outside_the_downloads_directory",
            f"{EN}::test_a_screenshot_path_is_confined_to_the_downloads_directory",
            f"{EN}::test_a_server_suggested_download_name_is_confined",
            f"{EN}::test_a_bare_dot_dot_name_is_refused",
        ],
        None,
    ),
    (
        "E6",
        "A step's host policy holds for its whole browser call: a parallel sibling sharing "
        "the page cannot widen it mid-redirect; every tool call runs in one policy "
        "operation (review R2)",
        [
            f"{EN}::test_a_parallel_sibling_cannot_widen_a_step_host_policy_mid_call",
            f"{EN}::test_the_serial_twin_refuses_the_same_redirect",
            f"{EN}::test_every_browser_tool_acts_only_inside_one_policy_operation",
        ],
        None,
    ),
    (
        "E7",
        "A browser upload reads a local file, so file_read applies: refused before "
        "Playwright sees anything; allowed twin uploads; the source is examined only once "
        "the call owns the browser, must be one regular file, and reaches the page as its "
        "bytes, so a queued path that becomes a directory is refused (review R4; R2-F2)",
        [
            f"{BT}::test_upload_outside_file_read_never_reaches_the_page",
            f"{BT}::test_upload_inside_file_read_reaches_the_page",
            f"{BT}::test_upload_refuses_a_directory_even_when_its_path_is_allowed",
            f"{BT}::test_a_queued_upload_cannot_become_a_directory_upload",
            f"{BT}::test_a_queued_upload_reads_its_source_when_its_turn_comes",
            f"{BT}::test_upload_refuses_anything_but_one_regular_file",
            f"{PC}::test_upload_file_hands_the_page_bytes_never_a_path",
        ],
        None,
    ),
    (
        "E8",
        "A read returns only what the checked document gave it — its result OR its error: "
        "a page that navigates during the call — off-list, or off-list and back — has either "
        "withheld, quoting nothing of the page; late content, child-frame navigation and an "
        "error about the checked page pass; an action is checked on every exit, its error "
        "withheld the same way; the model never sees the quote; every page method is "
        "routed through one of the two; all of it holds when CLEARING the page fails — the "
        "failure is reported, never its error, and a rejected screenshot never reaches its "
        "path; and no refusal names a URL a page or server chose — not at the next call's "
        "check, not for a redirect or a download — while the agent's own URL is still named "
        "(R2-F3; R3-F1, R3-F2; R4-F1; round-5 R5-F1)",
        [
            f"{EN}::test_a_read_whose_page_leaves_for_an_off_list_host_mid_read_is_withheld",
            f"{EN}::test_a_read_through_an_off_list_page_and_back_is_withheld",
            f"{EN}::test_the_reviewers_probe_a_url_change_with_no_event_is_withheld",
            f"{EN}::test_a_read_that_waits_on_a_page_that_stays_put_is_returned",
            f"{EN}::test_without_a_policy_a_read_is_not_fenced",
            f"{EN}::test_an_action_whose_page_leaves_mid_action_fails_and_clears",
            f"{EN}::test_every_page_method_is_a_fenced_read_or_a_checked_action",
            f"{EN}::test_an_error_after_the_page_left_for_an_off_list_host_is_withheld",
            f"{EN}::test_an_error_after_off_list_and_back_is_withheld",
            f"{EN}::test_an_error_about_the_checked_page_is_kept",
            f"{EN}::test_an_action_that_leaves_and_returns_is_not_detected",
            f"{EN}::test_the_model_never_sees_an_error_from_an_unchecked_page",
            f"{EN}::test_a_withheld_result_stays_withheld_when_clearing_fails",
            f"{EN}::test_a_withheld_error_stays_withheld_when_clearing_fails",
            f"{EN}::test_a_rejected_screenshot_never_reaches_its_path",
            f"{EN}::test_a_page_found_off_list_that_cannot_be_cleared_says_so",
            f"{EN}::test_the_withheld_status_reports_what_clearing_did",
            f"{EN}::test_the_model_never_sees_a_failed_clear",
            f"{EN}::test_a_call_starting_on_an_off_list_page_does_not_name_it",
            f"{EN}::test_a_retry_after_a_failed_clear_does_not_deliver_the_withheld_url",
            f"{EN}::test_a_redirect_off_list_is_refused_without_naming_it",
            f"{EN}::test_an_off_list_download_is_refused_without_naming_it",
            f"{EN}::test_a_url_the_agent_asked_for_is_still_named",
            f"{EN}::test_no_refusal_formats_a_url_a_page_or_server_chose",
        ],
        None,
    ),
    (
        "E4",
        "The browser stays out of the system layer until destinations are enforced before dispatch",
        [f"{SC}::test_the_browser_stays_off_until_destinations_are_enforced_before_dispatch"],
        None,
    ),
    (
        "E5",
        "Against real Chromium: off-list clicks and navigations send nothing; the redirect "
        "residual is MEASURED; fetch_url never follows a redirect off-list; the backslash "
        "smuggle is refused; a read waiting while the page redirects itself off-list is "
        "withheld, beside a late element on a page that stays put (R2-F3); so is the error "
        "two matches on the off-list landing raise, beside the same error kept on a page "
        "that stays put (R3-F1); a withheld read survives a clearing navigation that "
        "really fails, and the retry neither reads nor names the page; a fetch_url redirect "
        "refusal does not name where it led (R4-F1, R5-F1)",
        [
            f"{LIVE}::test_a_click_off_list_is_stopped_before_the_request_is_sent",
            f"{LIVE}::test_navigate_off_list_sends_nothing",
            f"{LIVE}::test_a_redirect_hop_is_the_residual_and_its_content_never_reaches_the_agent",
            f"{LIVE}::test_fetch_url_never_follows_a_redirect_off_list",
            f"{LIVE}::test_a_backslash_cannot_smuggle_a_disallowed_host_past_fetch_url",
            f"{LIVE}::test_a_read_waiting_while_the_page_leaves_for_an_off_list_host_is_withheld",
            f"{LIVE}::test_a_read_waiting_on_a_page_that_stays_put_returns_its_content",
            f"{LIVE}::test_an_error_quoting_an_off_list_page_is_withheld",
            f"{LIVE}::test_the_same_error_on_a_page_that_stays_put_is_kept",
            f"{LIVE}::test_a_withheld_read_stays_withheld_when_clearing_really_fails",
        ],
        "BROWSER_LIVE",
    ),
]


def _controls_by_target() -> list[str]:
    """The test target of every control in g30_controls.py (its 5th field)."""
    tree = ast.parse(CONTROLS.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "CONTROLS":
            return [ast.literal_eval(entry.elts[4]) for entry in node.value.elts]  # type: ignore[attr-defined]
    raise SystemExit("CONTROLS not found in g30_controls.py")


def _controls_for(node_ids: list[str], targets: list[str]) -> int:
    """Controls aimed at one of THIS claim's tests. A control aimed at a whole
    test file is not counted: it fires when any test in that file fails, so
    crediting it to every claim with a test there would overstate the evidence
    (the first version did exactly that — one control read as seven)."""
    return sum(1 for target in targets if "::" in target and target in node_ids)


def main() -> int:
    runnable = [
        n for _, _, nodes, env in CLAIMS if env is None or os.environ.get(env) for n in nodes
    ]
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rA", "--tb=no", "-p", "no:warnings", *runnable],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )
    outcome: dict[str, list[str]] = {}
    for line in r.stdout.splitlines():
        m = re.match(r"^(PASSED|FAILED|ERROR|SKIPPED) (\S+?)(\[.*\])?(?: |$)", line)
        if m:
            outcome.setdefault(m.group(2), []).append(m.group(1))
    targets = _controls_by_target()

    failed_claims = 0
    total_tests = 0
    print(
        f"{'claim':5} {'tests':>9} {'controls':>8}  status   (controls = aimed at this claim's own tests)"
    )
    for cid, text, nodes, env in CLAIMS:
        controls = _controls_for(nodes, targets)
        if env is not None and not os.environ.get(env):
            print(f"{cid:5} {'-':>9} {controls:>8}  NOT RUN (opt-in: set {env})  {text[:70]}")
            continue
        results = [res for n in nodes for res in outcome.get(n, ["MISSING"])]
        ok = bool(results) and all(res == "PASSED" for res in results)
        total_tests += len(results)
        failed_claims += not ok
        passed = sum(res == "PASSED" for res in results)
        status = "ok  " if ok else "FAIL"
        print(f"{cid:5} {passed:>4}/{len(results):<4} {controls:>8}  {status}  {text[:70]}")
    print(
        f"\n{len(CLAIMS)} claims; {total_tests} test results run; "
        f"{len(targets)} controls in g30_controls.py; pytest exit {r.returncode}"
    )
    if failed_claims or r.returncode not in (0,):
        print(f"{failed_claims} CLAIM(S) DID NOT REPRODUCE")
        return 1
    print("every runnable claim reproduced")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
