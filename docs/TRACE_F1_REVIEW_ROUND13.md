# F1/F5 trace review — round 13 sidecar

**Scope: the five round-12 findings, fixed — plus two more of the same class
that we found ourselves before packaging.**

| | |
|---|---|
| Commit | `73cd12a` (`73cd12a67e75b1d4e6fbeeeecd0af4046a69860b`) |
| Tree | `608a23db4789fa09e73de47497a6120c4d13a6b2` |
| Aggregate source hash | `f3bec68af1258958850082337b84353365fb87d92006ca1bd86f1fac3c43f359` |
| Archive | `docs/archives/trace-f1-review-r13-73cd12a.tar.gz` |
| Manifest / gates | `docs/archives/CODE_MANIFEST_R13.txt` · `GATE_OUTPUT_R13.txt` |
| Design record | `docs/TRACE_AUDIT_VAULT_DESIGN.md` (Part 1; Part 2 still unbuilt) |

Every claim here was executed before it was written. Every gate cited as
coverage was observed FAILING — eight controls, in the gate capture.

---

## 1. The five findings

**F1 — recovery (P1).** The audit endpoints now fetch from the vault and
record the outcome they achieved. Audit had become a vault-fetch surface
while still using the atomic `decide_raw_release`, which documents itself as
covering only surfaces whose raw is inline; it uses `begin_raw_release` /
`commit_raw_release` now.

*The first fix did not work, and the reason is the round's main lesson.* It
gated recovery on `audit_detail_has_raw(stored_detail)` — but stored is
already projected, so that predicate always answers no. **Third instance in
this epic of asking a projection predicate about an already-projected
value.** The signal has to be persisted: `AuditEntry.projector_version`
(Alembic `0013`), set only when the raw was vaulted. That is the P3a
argument already settled for step rows, and a COLUMN specifically so
deleting a payload marker cannot make a reader skip the vault.

**F2 — key initialisation (P1).** `trace_bootstrap.init_tracing()` is shared
by every entry point that builds a `WorkflowEngine`; a configured key that
cannot be resolved now raises `TraceKeyUnavailable` and stops execution.
Verified in production both ways: with no key configured the CLI writes
plaintext (correct); with the secret configured, 5/5 rows come back sealed.

**F3 — AEAD binding (P1).** `audit_entry_id` joins the associated data,
omitted when None so every non-audit row's AAD bytes are unchanged and the
existing vault still opens. **No legacy fallback**: one would keep accepting
exactly the substitution the binding closes, for precisely the rows an
attacker would target.

*Compatibility, as requested.* Rows sealed before the change do not open, so
they are migrated rather than tolerated: `tools/reseal_audit_vault.py`
(dry-run by default) opens with the legacy AAD and re-seals with the bound
one. Applied to production — 79 legacy-AAD + 8 plaintext re-sealed, 3 already
bound, **0 unopenable**; a second run reports 90 already bound, so it is
idempotent. Verified on real rows that a payload opens under its own entry id
and is rejected under another entry's id and under the legacy identity.

**F4 — writer inventory (P2).** Discovery keys on the TYPE (who constructs an
`AuditEntry`), not on what the receiving variable is called. The control
writes a real module containing `self.audit_repo.append(...)` and requires
the scanner to parse and find it; a second test pins why the receiver-name
scanner was abandoned. The escalation tool now vaults before projecting — it
had been destroying `reason`/`context` outright.

**F5 — retry identity (P2).** `_audit` accepts an explicit `entry_id`, so a
retry of the same logical write re-addresses one vault row. Tested through
`_audit` rather than the helper beneath it, with a companion test that two
calls WITHOUT an id remain two events — collapsing them would break the
multiplicity criterion.

---

## 2. Two more of the same class, found before packaging

The round-12 return was really about **half-built paths**, so we went looking
for the rest of them instead of only fixing what was named.

**2.1 The global `/audit` branch still had F1.** Three endpoints return audit
entries; we had fixed two. A platform-wide grant holder reading `/api/audit`
got the stored projection while the log recorded a release. Now all three go
through one responder, and a detector enumerates handlers returning
`list[AuditEntry]` and requires each to use it, so a fourth cannot be added
without recovery.

**2.2 The WS stream had it in a subtler form.** `_redact_ws_event(event) ==
event` meant "carries no raw" — but since the at-rest tightening the
published event IS already the projection, so that was true for every audit
event. The stream silently stopped offering raw to grant holders and skipped
the access audit with it. The persisted stamp is the honest signal. Tested
through an actual websocket.

Also: the owning org is resolved **per entry**, not once per request. The
global list spans orgs, so one org would decrypt against the wrong AEAD
identity.

---

## 3. What the pre-package protocol caught this round

**The F3 fix was unverified.** Removing `audit_entry_id` from the AAD broke
no test. The code was right and the evidence was absent — so the binding is
now pinned by two tests (substitution rejected; non-audit rows keep their
exact legacy identity, which matters because if that broke, the entire
existing vault would become unreadable).

**We pushed a commit with mypy red.** `592b1bc` ran ruff and pytest and
skipped mypy; `**ident` on a heterogeneous dict does not satisfy strict
keyword types. Fixed in `73cd12a`. Recorded because the five gates are a set
and running four of them is how a red CI job went unnoticed for six runs
earlier in this project.

**Two stale control lines were removed from the capture.** An early sabotage
pattern missed its anchor and left `sabotaged exit=0` in the evidence, which
reads as a broken control. Regenerated. That class has now bitten three
times, which is why the protocol says to read your own capture as a stranger
would.

---

## 4. Verification

- **1,211** backend tests pass, 16 skipped. Five gates green with exit codes
  read before any pipe.
- **Postgres-gated suite** run and included (4 passed), plus an **alembic
  up/down/up rehearsal of `0013`** — round 12 noted migration execution was
  not independently verifiable from the archive.
- **Eight controls**, each sabotaged with the defect it claims to catch and
  observed failing.
- **Production round-trip** under projector v8 with encryption on: all vault
  rows sealed, audit rows entry-bound and non-audit rows not, and every audit
  entry stamped **iff** its raw was vaulted.
- `PROJECTOR_VERSION` is unchanged at 8. This round changed an AEAD identity,
  a persisted stamp and three read paths — none of which is projector output.
  The golden guard agreeing is the evidence for that, not the assumption.

---

## 5. Still open, and still the operator's call

1. **The widening.** At rest equals the read path, and 39% of audit entries
   withhold every field. Most of what operators lost is the same ownership
   class as fields `_AUDIT_DETAIL` already declares. Unchanged since round 12
   and deliberately not decided by us.
2. **The backfill.** `verify_zero_raw` reports 10,110 findings in a capped
   2,000-instance sample. One-way; not started.
3. **`monitoring/service.py`** remains the one audit writer outside a
   vaulting path, pinned by a test so it cannot go quiet.
4. **One orphaned vault row** with a NULL `audit_entry_id` from the F1
   mapping outage window.

---

## 6. The question we would most like answered

Round 12 found five defects in work that had passed our own protocol. The
common shape was a path built from one end. We have since added R-e (*a claim
about a read path must be executed through the read path*) and gone looking
for the rest — finding two more ourselves.

**Is that enough of a change in method, or is there a class of half-built
path that neither the protocol nor the detectors would reach?** The detectors
we added are all "every X must do Y" enumerations over one file or one type.
We suspect that shape has a blind spot we cannot see from inside it.
