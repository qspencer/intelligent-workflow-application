# Reviewer's guide — F1/F5 round 5 (containment of the round-4 findings)

**Package:** `trace-f1-review-r5-<sha>.tar.gz` · branch `p1-reprimitive` ·
`git archive`, tracked files only. **Captured gate output ships inside, at
`docs/archives/GATE_OUTPUT_R5.txt`** — see §6.

---

## 0. Your round-4 verdict, and what we did with it

We accepted all of it. Nothing was disputed; every correction was re-executed
here before being folded.

| Your finding | What we did |
|---|---|
| §3's "all retained token paths are platform-computed" is **false** | Reproduced all three, corrected the claim in the sidecar, `P1_REPRIMITIVE_STATE.md` and `NEXT_STEPS.md` |
| The ceiling argument is right but its **conclusion was too broad** | Accepted as the round's most useful output — and acted on: all three are now closed WITHOUT §1.4a |
| The deferral **trigger** was wrong | Rewritten: *the first reader whose access depends on projection withholding content*, across UI, API, WebSocket, exports, logs and DB |
| Our **evidence claim about the key test was false** | Confirmed; the token-shaped-KEY property now exists and was RED first |

**This round asks: is the containment sound, and did it cost anything it
shouldn't?** Three of the four questions in §3 are about the containment's
*design*, not its correctness — we would rather hear "that global is wrong"
now than discover it in round 6.

---

## 1. What changed (all of it is new since your package)

Written **test-first**: each property was RED against the round-4 tree,
reproducing your executions, before any fix landed.

**(a) Token-shaped dict keys.** `Obj` now declares where its dynamic keys come
from. `wildcard_keys="platform"` (step ids, which we generate) survives; the
default is fail-closed and the entry is **dropped whole**. `_USAGE` stopped
being a shape-gated wildcard and became a closed set of counter names.

**(b) External routing ids** (`id`, `message_id`, `thread_id`) are withheld
below grant and recoverable from the vault. Checked first that nothing in
backend or frontend reads a routing id out of a *projected* payload.

**(c) Model-chosen tool names** resolve against a catalog; an unresolved name
is never echoed. See §3.2 — the plumbing here is the part we most want judged.

**(d) A dropped key increments `_withheld_key_count`** — a count, never names.
Dropping keys outright destroyed the signal that *something was there*, which
the old `key: [redacted]` form carried and which explain and the completeness
checks depend on.

**(e) `_withheld_key_count` is a projection fixed point.** Leaving it
undeclared made re-projection strip-and-recount it, so an already-backfilled
row looked raw forever. Our own bug; the migration tests caught it, we did not.

---

## 2. What this cost, stated plainly

**Operators below grant now see how many fields were withheld, not which.**
`output_text`, `category`, `copied_to` and friends are counted, not named.
That is a real loss of operational signal and we are not pretending otherwise.

**Seven existing assertions encoded the superseded contract** and were updated
in place *with the reason*, not deleted — including the routing-id property,
which previously asserted the **opposite** (that an id-shaped value is retained
by design, §1.3). Given round 4 turned partly on a test that claimed coverage
it did not have, please treat our test edits as suspect and check that each new
assertion is genuinely **stricter** than the one it replaced.

---

## 3. The four questions for this round

### 3.1 Is `wildcard_keys="platform"` a sound trust declaration?

It asserts step ids are platform-generated. **We are not certain it holds.**
Step ids come from the workflow definition — operator-authored YAML, *but* the
C7.1 NL scaffold has an LLM draft definitions, so a scaffolded workflow's step
ids are **model-chosen strings** that an operator may never have read closely.
If that breaks the declaration, the fix is ours (validate ids at definition
load, or treat scaffolded definitions as untrusted-keyed) — but we would rather
you rule on it.

### 3.2 Is a process-level tool catalog acceptable, or a design smell?

`set_resolvable_tools()` is **module-global mutable state consulted by a
security control**, kept in step by `ToolCatalog.register()`.

We did this because the alternative failed: projection must be idempotent, so a
name is re-validated on every re-projection *including on the read path*, which
sits in the API layer with no engine handle. Threading a catalog to every read
site left the common path fail-closed in practice — names never shown anywhere,
safe but useless.

The costs we already know: **any** `ToolCatalog` construction in the process
widens it, and it is **order-dependent** — a test registering tools changed an
unrelated assertion's result, which is how we found it. That test now passes an
explicit empty catalog, and the order-dependence is flagged in its comment
rather than papered over. Is this acceptable for a control of this kind?

### 3.3 Does the withheld-count reopen anything?

It publishes a small integer per object. We believe a count of dropped keys
carries no content. Does it leak structure worth having (fingerprinting a
workflow, confirming a field's presence), and is a count the right trade
against the operator signal it preserves?

### 3.4 Is the containment actually complete?

We closed the three paths you named. **Are there others of the same class** —
a retained value whose source is external or model-derived and which we are
still vetting by shape? That question, not the three fixes, is the one whose
answer we would act on fastest.

---

## 4. Reproduce (and the exact executions from your round-4 report)

```sh
cd backend && uv sync

uv run pytest tests/test_trace_boundary_properties.py -v     # 39 properties
uv run pytest -q
uv run ruff check . ; uv run ruff format --check . ; uv run mypy src tests
```

Your three reproductions, against this tree:

```python
from workflow_platform.trace_projection import (
    redact_tool_data, safe_trigger_payload, safe_tool_call)

redact_tool_data({"usage": {"AKIAIOSFODNN7EXAMPLE": 1}}, admin=False, kind="step_output")
# -> {"usage": {"_withheld_key_count": 1}}

safe_trigger_payload({"type": "webhook", "id": "sk_live_51H8xQ2"})
# -> id is "[redacted — raw-trace grant required]"

safe_tool_call({"name": "exfiltrate_sk_live", "input": {}}, known_tools=frozenset({"file_read"}))
# -> name withheld;  a RESOLVED name ("file_read") is still shown
```

The four properties that pin the round-4 class specifically:
`test_token_shaped_dict_key_is_not_a_leak_channel`,
`test_externally_supplied_routing_ids_are_grant_gated`,
`test_unresolved_tool_name_is_not_emitted`,
`test_p1_trigger_routing_fields_are_withheld` (the superseded one).

---

## 5. Unchanged, and still out of scope

**F3** (mutable stamp), **F4** (plaintext commitment oracle), **F6** (per-kind
release audit) remain knowingly open. **B1** remains deferred — under the
corrected trigger from §0. **§1.4a provenance** is still unbuilt; this round's
containment does not substitute for it, and we are not claiming Contract A.

The posture is unchanged from your verdict: flip ON, single operator across
every trace surface, no acceptance claimed.

---

## 6. Packaging — the two failures from round 4, fixed

1. **The index lacked its own entry.** We built the tarball from the commit
   *before* updating `docs/archives/README.md`. This time the record lands
   first and the archive is built after it, so the index inside the package
   carries this package's own row and per-file hashes. (A package still cannot
   contain its own tarball hash — that is recorded in the repo README after the
   build, and the per-file hashes inside let you verify independently.)
2. **You could not verify any gate claim.** Captured standalone output now
   ships at `docs/archives/GATE_OUTPUT_R5.txt`: ruff, ruff-format, mypy and
   pytest, each run standalone with its exit code printed and never piped
   (piping through `tail` is how we once reported a red tree as clean), plus
   the three reproductions re-run. It is evidence of what the tree did on our
   host, not a substitute for your own run if deps resolve for you.

Secret-cleanliness: `git archive` emits tracked files only; `.secrets/`,
`.env`, refresh tokens, `.venv` and `.memory/` (real mail content) are
gitignored and cannot be present. Verified at build, both for secrets and for
personal mail data.
