# RPA Challenge OCR example

End-to-end browser-automation validation workload against the public
[RPA Challenge OCR](https://rpachallengeocr.azurewebsites.net/) site.
First workload that exercises every browser_* tool plus the new
`image_ocr` tool and the `filter_rows_by_date` / `write_csv` stock
functions. Distinct from the prior examples (PR triage, paper triage,
email triage) which were all LLM-classification over JSON payloads —
this one drives a real web UI.

## Workflow shape

Linear, 6 steps:

1. **`open_challenge`** (agentic) — navigate to the site, click Start,
   wait for the JS-rendered table.
2. **`read_table`** (agentic) — call `browser_read_table` on
   `#tableSandbox`, emit a JSON list of `{id, due_date, invoice_url}`.
3. **`filter_overdue`** (deterministic, `filter_rows_by_date`) — keep
   rows where `due_date <= today`.
4. **`extract_invoices`** (agentic) — for each kept row, download the
   invoice JPG via `browser_download`, OCR it via `image_ocr`, extract
   `invoice_number / invoice_date / company_name / total_due`. The
   rubric for parsing OCR text lives in `agent_memory.md`.
5. **`build_csv`** (deterministic, `write_csv`) — produce the output
   CSV at `/tmp/rpa-challenge-output.csv`.
6. **`submit`** (agentic) — upload the CSV, click Submit, screenshot
   the result page.

Per-workflow-run lifecycle: a `PlaywrightConnector` opens before step 1
and closes after step 6 — see `docs/BROWSER_CONNECTOR_PLAN.md` for the
rationale.

## What you get

After the run completes (success or otherwise), `step_executions.output`
for each step contains:

- `read_table`: agent's JSON list of `{id, due_date, invoice_url}`.
- `filter_overdue`: `{kept_rows, dropped_rows, kept_count,
  dropped_count, unparseable_count}`.
- `extract_invoices`: agent's JSON list of `{id, due_date,
  invoice_number, invoice_date, company_name, total_due}`.
- `build_csv`: `{path, row_count, column_count}`.
- `submit`: agent free-text containing the screenshot path.

## Scripts

```sh
# Single-shot (no payload — it's a manual trigger):
examples/rpa_challenge_ocr/scripts/run_one.sh

# Replay-mode end-to-end against the committed fixture:
BEDROCK_MODE=replay pytest backend/tests/test_rpa_challenge_workflow.py -q

# Real-browser live test (BROWSER_LIVE=1, lands in D7a):
BROWSER_LIVE=1 pytest backend/tests/test_browser_live.py -q
```

## Why this workload

`docs/BROWSER_CONNECTOR_PLAN.md` picked the RPA Challenge OCR site as
the first validation target because it's free, has no auth, exercises
every capability category (read JS-rendered table, click,
upload + submit, download files), and the site explicitly invites
automation. A passing run produces a printable "challenge completed"
result page — which is the OCR test's success criterion.
