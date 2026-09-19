# F1/F5 trace review — round 17 sidecar

**Scope: the round-16 finding, fixed — plus the same defect on a third
field, and the cause of the failing test you have reported in every round
since 12.**

| | |
|---|---|
| Commit | `b4dbab5` (`b4dbab578d977061341e691ce38114e998d3a1d5`) |
| Tree | `4721cb814cdd44cee2ea6a28a5c3319de996f71e` |
| Aggregate source hash | `836b8ba8be33b9f76b38f640b1a8a21798432cf0728db597d1ef3ba3e01ad848` |
| Archive | `docs/archives/trace-f1-review-r17-b4dbab5.tar.gz` |
| Manifest / gates | `docs/archives/CODE_MANIFEST_R17.txt` · `GATE_OUTPUT_R17.txt` |
| Design record | `docs/TRACE_AUDIT_VAULT_DESIGN.md` |

Every claim was executed before it was written. Four controls; **one did
not fire and the capture says so**, with the reason.

---

## 1. The finding

`explain_step` now builds its response from the recovered output. Both
branches: the deterministic `output` and the agentic `output_text`. The
projection is kept when retrieval or the release-decision audit fails.

Your point about the tests was the sharper half. Our explain test checked
HTTP 200 and the absence of a secret on the FAILURE path, which establishes
nothing about success. Both branches now assert the content is present, and
the controls reproduce your exact symptoms.

---

## 2. The same defect on a third field

**`error` was rendered from `exe.error`** — the stored value, which under
the flip is the redaction marker, with the raw vaulted under
`RawTraceKind.ERROR`. `merge_error` exists for exactly this and
instance-detail calls it; explain never did. So a grant holder received the
marker beside `raw_included: true`.

You named `output` and `output_text`. We found this by enumerating every
stored-value reference in the handler instead of fixing the two fields
named — the rule we added after round 15, when answering a counterpart
question from memory cost two returns.

We then checked the whole class by enumeration: which asset kinds each
surface exposes, and which it recovers. `get_instance` recovers output,
error and trigger; `explain_step` recovers output and error and exposes no
trigger; the audit, escalation and websocket surfaces recover audit
details. Coverage is complete for what each surface shows.

---

## 3. The failing test you have reported every round

**It is our test, not your environment.** We had been treating
`Invoice`/`Thvoice` as a known quirk at your end. It is a real defect in
the fixture.

The phase-2 test built its PDF from the string `"Invoice text"` — **12
characters, below `PdfExtractTool.NATIVE_THRESHOLD` of 30** — so extraction
silently fell through to the OCR path, and the assertion depended on the
local tesseract build. Ours (5.3.4) reads `Invoice`; yours reads
`Thvoice`. The test is about the PDF flow reading the actual file, not
about OCR accuracy, so the fixture is now long enough to take the
deterministic native path. Verified: `is_native` flips to `True`, so
extraction never reaches tesseract.

Every other PDF fixture was checked against the threshold. Three others sit
below it; only one asserts on extracted text, and its fixture is multi-line
and already clears the threshold. This was the only affected test.

**Note the control for this one did not fire**, and the capture says so.
Reverting the fixture restores the OCR path, but our tesseract still reads
it correctly, so the test passes here — which is the defect. The evidence
is instead an environment-independent property: with the longer fixture
`is_native` is `True` and tesseract is never invoked.

A test that fails in your environment every round trains both sides to read
a red result as noise. That is how a real failure gets missed, so we would
rather it were green for a reason than red for a known one.

---

## 4. Verification

- **1,244** backend tests, 19 skipped. Five gates green under one exit code.
- **7 Postgres integration tests** and an **alembic up/down/up rehearsal**.
- **Four controls**; three fired, one is documented as unable to fire
  locally and why.
- **Forgery pass** against v10: input-derived reference, out-of-enum
  classification, `workflow_id` holding an email, author-set
  `event_type` — all rejected.
- **Three read surfaces agree** on four action shapes.
- `PROJECTOR_VERSION` **10**, unchanged: this round touched response
  construction and a test fixture, neither of which is projector output.

---

## 5. Design follow-ups — your six answers are recorded and both are unblocked

Recorded in `docs/NEXT_STEPS.md` with the shapes you specified. Two of your
answers changed our understanding rather than just our plan:

- **`users.id` survives an org transfer**, so the org must be recorded on
  the event rather than inferred from the id.
- **Not every `user_id` is a platform user.** Our memory namespace is
  `org:<org>:user:<key>` where the key is a mailbox address — so "give
  those subjects their own typed identity" describes something already live
  in production, not a hypothetical.

We have also carried forward your point that `actor_id` holds an operator
email on every audit row and is therefore the larger exposure, and that
rename / deletion / org-transfer behaviour must be defined before any
stored identity format changes. That moves subject identity from a
rendering change to an identity-lifecycle decision with a migration behind
it, which is why it is sequenced after the registry.

---

## 6. Unchanged, and still the operator's

1. The **backfill** — 10,110 findings in a capped 2,000-instance sample.
2. **`monitoring/service.py`** — blocked on where instance-less raw lives.
3. **One orphaned vault row** with a NULL `audit_entry_id`.
