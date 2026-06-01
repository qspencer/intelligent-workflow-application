---
name: regex-first-extraction
description: >-
  Build cost-aware structured-text extractors (invoices, forms, tables, documents)
  the platform way — deterministic regex + confidence scoring first, an LLM repair
  step ONLY for the low-confidence remainder. Use when parsing repeating-pattern text,
  adding a `record_*`/extraction stock function, or deciding regex vs LLM for a field.
  Reserve Bedrock for the ~2-5% of cases regex can't handle.
---

# Regex-first extraction (LLM only for the edge cases)

Adapted from `affaan-m/ECC` (MIT). Reworked for this platform: async, the Bedrock
wrapper, our deterministic-function idiom, conditional edges, and replay-mode tests.

## The thesis

For structured text with repeating patterns, **regex handles 95-98% deterministically
and for free.** Send only the low-confidence remainder to the cheapest LLM. This is
[VISION](../../docs/VISION.md) anti-goal #3 ("don't use an LLM where deterministic logic
works") made concrete, and it's why our extraction examples (`examples/invoice_extraction`,
`pdf_classifier`) parse a real template before they reach for Claude.

```
Is the format consistent and repeating?
├── Yes (>90% follows a pattern) → regex parser → confidence score
│       ├── score ≥ threshold → emit directly (NO LLM call)
│       └── score <  threshold → flag for the LLM repair step
└── No (free-form, highly variable) → skip regex, go agentic from the start
```

If you find yourself writing regex for genuinely free-form prose, stop — that's the
case the LLM is *for*. The pattern below is for the inverse: text that mostly obeys a
shape, where paying per-token for every item is waste.

## How to wire it on THIS platform (preferred)

Do **not** bury a Bedrock call inside a deterministic function unless you have to (it
hides cost attribution and breaks the clean unit/replay split). Instead, model the two
phases as two steps joined by a **conditional edge** — the same mechanism the engine
already uses for routing:

```
[extract_regex]            deterministic  → emits items + `needs_llm` (+ flagged ids)
      │
      ├─ condition: needs_llm == false → [done]          (most runs end here, $0)
      │
      └─ condition: needs_llm == true  → [repair_llm]    agentic, Haiku, capability-gated
                                              │
                                              ▼
                                          [merge]        deterministic
```

Why this shape and not an inline call:
- **Cost is attributed.** The agentic step gets its own `cost_usd` / token usage on
  `step_executions.output` — invisible if you call `converse` from inside a function.
- **It's replayable.** The regex + confidence steps are pure and unit-test without AWS;
  only `repair_llm` needs a recorded fixture (`BEDROCK_MODE=replay`).
- **It's auditable + gated.** The LLM step goes through normal agent dispatch, so the
  capability allowlist and per-tool-call audit log apply.
- **The common path never touches Bedrock.** On a clean batch, `needs_llm` is false and
  the workflow ends after one deterministic step.

## Phase 1 — regex + confidence (pure, deterministic, unit-tested)

Keep these as pure module functions. Frozen dataclasses; never mutate a parsed item —
return a new one. mypy strict + ruff clean.

```python
import re
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ParsedItem:
    id: str
    fields: dict[str, str]
    confidence: float = 1.0
    flags: tuple[str, ...] = ()


_ROW_RE = re.compile(
    r"^(?P<id>\d+)\s+(?P<name>.+?)\s+\$(?P<total>[\d,]+\.\d{2})$",
    re.MULTILINE,
)


def parse_items(content: str) -> list[ParsedItem]:
    items: list[ParsedItem] = []
    for m in _ROW_RE.finditer(content):
        items.append(
            ParsedItem(id=m.group("id"), fields={"name": m.group("name").strip(),
                                                  "total": m.group("total")})
        )
    return items


def score_confidence(item: ParsedItem) -> ParsedItem:
    """Return a new item with confidence + flags set. Pure; no mutation."""
    score, flags = 1.0, []
    if not item.fields.get("total"):
        score -= 0.5
        flags.append("missing_total")
    if len(item.fields.get("name", "")) < 3:
        score -= 0.3
        flags.append("short_name")
    return replace(item, confidence=max(0.0, score), flags=tuple(flags))
```

The deterministic stock function (`async def fn(config, context, world) -> dict`, the
same signature as every `record_*` in `engine/functions.py`) runs both and emits the
flag the conditional edge reads:

```python
THRESHOLD = 0.95

async def extract_regex(config, context, world) -> dict[str, Any]:
    raw = _resolve_path(context, config.get("text_from", "steps.read.output_text"))
    if not raw:
        raise StepFailure("extract_regex could not resolve input text")
    items = [score_confidence(i) for i in parse_items(raw)]
    flagged = [i.id for i in items if i.confidence < THRESHOLD]
    return {
        "items": [i.fields | {"id": i.id} for i in items],
        "flagged_ids": flagged,
        "needs_llm": bool(flagged),          # ← the conditional-edge predicate
        "regex_success_rate": 1 - len(flagged) / max(1, len(items)),
    }
```

## Phase 2 — the LLM repair step (Haiku, edge cases only)

Make it an **agentic step** in the workflow YAML, gated by the conditional edge so it
only runs when `needs_llm` is true. Cheapest model first — the region-prefixed Haiku
inference profile:

```yaml
- id: repair_llm
  type: agentic
  model: us.anthropic.claude-haiku-4-5-20251001-v1:0
  goal: |
    Some rows below failed deterministic parsing (their ids are in
    prior_steps.extract_regex.flagged_ids). For ONLY those rows, re-read the
    source text and return corrected JSON objects. Respond with a JSON array,
    no prose, no markdown fences.
  policy:
    max_iterations: 4
    max_total_tokens: 4000

edges:
  # Conditions are simpleeval expressions; `steps` is the step-id → output-dict
  # map, so use subscript form (bare-truthy works, like the engine's own tests).
  - {from: extract_regex, to: repair_llm, condition: "steps['extract_regex']['needs_llm']"}
  - {from: repair_llm, to: merge}
```

Then a deterministic `merge` step parses the agent's output and splices the repaired
rows back over the flagged ids. **Reuse the existing tolerant parsers** — don't write a
new one: `_find_json_object` / `_extract_json_list` (and `_JSON_FENCE_RE`) in
`engine/functions.py` already handle bare JSON, ```json fences, and surrounding prose,
which is exactly how Haiku replies in practice.

### If you genuinely must call Bedrock inline

Sometimes the repair isn't worth a whole step. Then call the wrapper directly — note
the converse shape (`response["output"]["message"]`, usage under `response["usage"]`):

```python
resp = await bedrock.converse(
    model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    messages=[{"role": "user", "content": [{"text": prompt}]}],
    inference_config={"maxTokens": 500, "temperature": 0},
)
text = resp["output"]["message"]["content"][0]["text"]
fixed = _find_json_object(text)            # tolerant; returns None on garbage
```
Accept the trade-off knowingly: cost won't show as a separate step, and the function now
needs a recorded fixture to test. Prefer the two-step shape above.

## Testing

- **Phase 1 is pure** → unit-test `parse_items` / `score_confidence` directly, including
  malformed input, missing fields, encoding edge cases. No Bedrock, no MockWorld needed.
- **Phase 2** → record one fixture (`BEDROCK_MODE=record`) of a flagged batch, commit it,
  and the workflow test runs in CI under the `replay` default. Mirror
  `test_invoice_extraction_workflow.py`.
- **Assert the metric.** Put `regex_success_rate` (and flagged count) on the step output
  and assert it stays high — a regression there means the regex drifted and you're
  silently paying for more LLM calls.

## Don't

- Send the whole document to an LLM when regex handles the bulk — that's the anti-goal.
- Mutate parsed items during scoring/repair — return new instances (`dataclasses.replace`).
- Reinvent JSON extraction — reuse `engine/functions.py` helpers.
- Hide the Bedrock call inside a deterministic function without a deliberate reason.
- Reach for a model bigger than Haiku for repair without evidence it's needed.

## Reference

Real production numbers from the source (a 410-item quiz parser): regex handled 98%,
~5 LLM calls total, ≈95% cost saving vs all-LLM. Our analogues:
`examples/invoice_extraction`, `examples/pdf_classifier`, and the `record_*` functions
in `backend/src/workflow_platform/engine/functions.py`.
