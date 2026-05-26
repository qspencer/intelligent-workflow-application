# Invoice Extraction

Drop a single-page invoice PDF (Tableau Superstore template) into an
inbox folder. Workflow extracts structured fields, runs invariant
checks, routes the PDF into a per-country folder, and emails the
operator after each substantive stage.

## Files

- `workflow.yaml` — the workflow (7 steps: `pdf_extract` → `notify_extracted`
  → `extract` → `record` → `notify_validated` → `route` → `notify_filed`)
- `agent_memory.md` — extraction rubric for the `extract` step (field
  catalog, date format, line-item parsing)
- `fixtures/` — 5 committed PDFs from the Tableau Superstore sample set
  (synthetic data, safe to commit)
- `scripts/run_one.sh`, `run_batch.sh` — fire helpers via `tools/fire.py`

## Notification pattern

After each substantive stage, a tiny agentic `notify_*` step sends an
email to `qrsconsulting@quentinspencer.com` via the `email_send` tool:

| Step | When it fires | Email content |
|---|---|---|
| `notify_extracted` | After `pdf_extract` | "PDF parsed: N chars, M pages" |
| `notify_validated` | After `record` | Full structured fields + invariant check results |
| `notify_filed` | After `route` | Source path, destination path, country bucket |

Each notify agent has only `email_send` in its tool surface and uses a
tight prompt to compose the body from the prior step's output. No new
engine plumbing — just leverages the email connector wiring from Phase 1.

Cost: ~$0.001 per notification on Haiku 4.5 (~$0.003 per invoice across
all three notify steps), plus ~$0.002 for the main `extract` step.

## Quick start

```bash
# Need WORKFLOW_PLATFORM_GMAIL_ACCOUNT set so the email_send tool wires
# into the engine catalog. Without it, the notify steps fail with
# "Unknown tool: email_send".
export WORKFLOW_PLATFORM_GMAIL_ACCOUNT=intelligent.workflow.engine@quentinspencer.com

# Fire one fixture:
examples/invoice_extraction/scripts/run_one.sh \
  "examples/invoice_extraction/fixtures/invoice_Liz Thompson_19562.pdf"

# Fire all five committed fixtures:
examples/invoice_extraction/scripts/run_batch.sh

# Fire the full external 1000-PDF set:
FIXTURES_DIR=/home/ubuntu/Documents/intelligent-workflow-engine/sample-invoices/1000-pdf-invoice-samples \
  examples/invoice_extraction/scripts/run_batch.sh
```

Replay-mode tests at `backend/tests/test_invoice_extraction_workflow.py`
exercise the pipeline against `FakeBedrock` + a fake Gmail connector,
including the three notify-step branches and the invariant-check logic.

## Output shape

`record_invoice_extraction` parses the agent's JSON into:

| Field | Type | Source |
|---|---|---|
| `parse_ok` | bool | always present |
| `invoice_number`, `customer_name`, `order_id` | str | agent JSON |
| `invoice_date` | str (ISO) | agent JSON |
| `ship_mode`, `ship_to_city`, `ship_to_country` | str | agent JSON |
| `subtotal`, `shipping`, `total` | float | agent JSON |
| `line_items` | list[dict] | agent JSON |
| `line_item_count` | int | computed |
| `subtotal_plus_shipping` | float | computed |
| `subtotal_plus_shipping_matches_total` | bool/null | invariant check (±$0.01) |
| `line_items_sum` | float | computed |
| `line_items_sum_matches_subtotal` | bool/null | invariant check (±$0.01) |
| `invoice_date_iso` | bool/null | ISO parse check |

Postgres query for batch-run analysis:

```sql
SELECT
  output->>'ship_to_country' AS country,
  COUNT(*) AS n,
  AVG((output->>'total')::float) AS avg_total,
  SUM(CASE WHEN output->>'subtotal_plus_shipping_matches_total' = 'true' THEN 1 ELSE 0 END) AS math_ok,
  SUM(CASE WHEN output->>'invoice_date_iso' = 'true' THEN 1 ELSE 0 END) AS date_ok
FROM step_executions
WHERE step_id = 'record'
  AND output->>'parse_ok' = 'true'
GROUP BY 1
ORDER BY n DESC;
```

The two `*_matches_*` columns are the eval signal: when they drift below
~98%, the rubric needs tightening.

## Iterating on the rubric

Same loop as PR / paper / email triage:

1. Edit `agent_memory.md` (the field catalog, date format, line-item
   parsing rules).
2. Re-run a batch — start with the 5 committed fixtures, then 50, then
   the full 1007.
3. Query Postgres grouped by `memory_hash` to A/B compare rubric edits.
4. The `notify_validated` email surfaces the per-invoice invariant
   results in real-time — useful for catching regressions without
   waiting for a batch to finish.

## What's not wired yet

- **Send-as alias** — outgoing notification emails currently get
  rewritten by Gmail from `intelligent.workflow.engine@quentinspencer.com`
  to `qrsconsulting@quentinspencer.com` (the Workspace primary).
  Recipients see "qrsconsulting" as the sender. To make notifications
  show the bot identity, register `intelligent.workflow.engine@` as a
  verified Send-As entry in Gmail settings.
- **Cross-reference against the Tableau Superstore source CSV** for
  ground-truth accuracy scoring. The dataset has known-correct values
  for every order_id; we could programmatically check the extracted
  total against the source. Deferred until the rubric-only invariants
  plateau as a useful signal.
