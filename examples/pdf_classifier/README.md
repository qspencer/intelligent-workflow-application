# PDF classifier example

Watches `sample_inbox/` for new PDFs. For each one: extracts the text,
asks Claude to classify it, and copies the file into `output/<category>/`.

This is the platform's first real workload — the use case the prototype
at `/home/ubuntu/Dev/pdf-tool` was built for, expressed as a workflow
definition.

## Files

- `workflow.yaml` — the definition. Trigger + 5 steps + 4 edges (a diamond
  after `classify`).
- `agent_memory.md` — seed memory for the classifier agent. Loaded by
  the engine into the agentic step's system prompt at runtime.
- `sample_inbox/` — drop folder. Empty by default.
- `output/` — destination root. Per-category subfolders are created on
  demand (`output/invoice/`, `output/receipt/`, etc.).

## Shape

```
extract → classify → route
                  → evaluate → record_eval
```

The `evaluate` step is an LLM-as-judge: it scores the classifier's output for
faithfulness (does the summary / key_fields reflect the text?) and category
plausibility (was the chosen document_type reasonable?). `record_eval` is a
deterministic step that parses the evaluator's JSON into structured fields so
they're queryable directly off `step_executions.output`.

Routing is the user-facing outcome and runs in parallel with evaluation —
eval failures don't block routing.

## Run it

### Replay mode (no AWS needed)

The platform's test suite covers this flow with a fake Bedrock —
`backend/tests/test_pdf_classifier_workflow.py` runs the whole pipeline
end to end against `MockWorld` in under a second. That's the canonical
"does this work?" check.

### Live mode (real Bedrock + real PDFs)

Once the Bedrock gates from `docs/BEDROCK_SETUP.md` are clear:

```bash
cd backend
# Drop a PDF into examples/pdf_classifier/sample_inbox/ first.
uv run python tools/replay.py \
  --definition ../examples/pdf_classifier/workflow.yaml \
  --trigger '{"file_path": "../examples/pdf_classifier/sample_inbox/<your-file>.pdf"}' \
  --bedrock-mode live
```

The replay CLI runs the workflow against in-memory repositories. The
output PDF lands under `examples/pdf_classifier/output/<category>/`.

To wire it up as a long-running watcher (the trigger fires on every new
file under `sample_inbox/`), use the platform's API: register the
workflow via `POST /api/workflows/import` and start the trigger from
the dashboard.

## Notes

- The classifier's category set mirrors the prototype's
  (invoice / receipt / contract / report / letter / form / other).
  Extend by editing both the agent's prompt and the `categories:` list
  in `workflow.yaml`.
- The route step is deterministic on purpose: the LLM decides the
  category, but file movement is plain code. "Deterministic where
  possible" — see `docs/VISION.md`.
- For real production, swap the `filesystem` trigger for `s3` (objects
  arrive in a bucket) and replace `route_by_classification`'s file-copy
  with a downstream connector send (webhook to your DMS, etc.).
