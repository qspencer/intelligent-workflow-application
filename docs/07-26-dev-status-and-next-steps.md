# Development status & sequencing — 2026-07-26

A dated snapshot + recommendation, written at the end of an unusually dense
ten days. Complements (does not replace) the living docs: `CLAUDE.md` for
shipped state, `docs/NEXT_STEPS.md` for the backlog, plan docs for designs.
When this document and those disagree, those win — this one doesn't get
maintained.

## 1. Where the platform stands

Every planned phase and epic with a committed scope is **done**: Phase 0–2,
the canvas cuts C1–C8, the auth + tenant-roles arc (AUTH_PLAN built;
ROLES_PLAN S1–S3 fully executed), startup user seeding, and the veracium
integration through V4 outcome tracking on `0.3.0b1` (org-namespaced store,
7,981 rows migrated). Test surface: **854 backend + 186 frontend tests +
3-spec Playwright/axe e2e**, ruff/mypy-strict clean, CI green including
schema-fuzz. What remains everywhere is trigger-gated deferrals, each with
its trigger named in its home doc.

The qualitative shift of the last ten days: the platform stopped being
read-only. The acting email-triage variant (`EMAIL_TRIAGE_ACT_PLAN`) is the
first production **mutating** capability — privilege-split, input-minimized,
enum-gated, allowlisted — and it is live on the personal mailbox now.

## 2. In flight: the §8 supervised window

19 messages processed as of this morning; **all criteria green**: 100%
category parity, zero out-of-namespace writes, zero failures, apply cost
steady at the structural two-turn floor (~$0.0025/msg; the §8 target of
~$0.001 was a design underestimate — recommend formally revising to
≤$0.003). One human spot-check confirmed (the live `awaiting-reply`).

The window has already paid for itself twice over:

- **Three defects found by the first real message** (Bedrock tool-name
  charset; engine stranding unexpected exceptions in RUNNING; rubric+recall
  leaking into the minimized apply step) — all fixed same-day, all
  regression-pinned. The design review missed all three; live traffic
  found them in one run. Validation windows work.
- **Two taxonomy insights from casual operator observations**: awaiting-reply
  is a *state* needing lifecycle, not a category (G11 evidence #4); and the
  codification criterion is *sender-determined vs content-dependent* — the
  operator's own Gmail filters being a hand-built version of the layer G13
  automates (G13 sharpened, evidence threshold already met: 8 qualifying
  senders, zero corrections store-wide).

Volume note: the window sees INBOX residue only (~9/day — sender-based
filters pre-drain the rest, *correctly*). Recommend closing on **evidence
quality** rather than insisting on the raw ≥100 count: at 100% parity with
zero anomalies across ~50+ messages and one more human spot-check pass, the
count adds little. Projected close: **~Aug 2–3** by count, or ~Jul 29–30 by
quality if the streak holds.

## 3. Near-term queue (next ~2 weeks), in recommended order

1. **veracium `0.3.0b1` → `0.3.0` pin bump** — when dev releases (~Jul
   29–30, post-PR-#9-merge). Mechanical: bump, re-run gated suites, done.
   Watch COORDINATION.md for the release event. (Also upcoming there:
   arXiv paper announcement ~Sun Jul 26 20:00 ET; 0.4.0 proactive recall
   later — a future consumer feature for a "mailbox briefing" workflow,
   no action now.)
2. **Close the §8 window** (per §2 above) and write the closing verdict
   into `EMAIL_TRIAGE_ACT_PLAN`. This *arms G11 phase 2 and G13* — both
   triggers then satisfied.
3. **G11 phase 2 — two-axis triage (rubric-only, as designed).** The
   highest-value intelligence work available: four collision examples
   pinned, the attention-axis lifecycle requirement (check-then-label,
   retirement path, removal stays operator-side) specified by live
   evidence. Rubric + label-vocabulary change; the ACT_PLAN §7 shape.
4. **G13 slice 1 — sender pre-filter codification.** After G11 lands (the
   split defines what's codifiable: category yes, attention never).
   Evidence is ready; the filter-export seed idea is recorded in the
   entry. This is also the platform's best demo of its own thesis.
5. **Frontend arc, when frontend is the priority** (holds indefinitely;
   see §4): login e2e spec → react-router 7 → Automations/Workflows IA
   rework — smallest first, each making the next cheaper.

## 4. The frontend question, answered honestly

Asked directly (2026-07-26): is anything higher priority than the
react-router 7 migration? Yes, two things — but the ordering insight is
that they compose:

- **The Automations-vs-Workflows IA rework** is the highest-value frontend
  item (open since 2026-06-06): two overlapping definition lists, examples
  filtered from one and not the other, owner/org now invisible on cards,
  and G11's future attention UI needing a coherent home. It is
  route-and-navigation surgery.
- **The local-auth Playwright spec** is small and security-relevant: the
  product's front door (login, 401 gate, sign-out, role gating) has never
  been driven in a real browser. Seeded test users make it a half-day.
- The **router migration** should be step two of that arc, not a
  standalone chore: do it before the IA rework so the route surgery is
  written once against the v7 API. (Current v6 carries two *moderate*
  advisories with no v6 fix; CI gates on high, SSR advisory inapplicable,
  open-redirect behind auth — no urgency, just direction.)

Recommendation: run the intelligence arc (§3.2–3.4) first — it's where the
product's differentiation lives and the evidence is hot. Take the frontend
arc as the next block after, or interleave the login spec anytime.

## 5. The fork that isn't scheduled: product posture

`docs/product/` (opportunity memo, spec, competitive landscape incl. the
2026-07-24 Pega profile, positioning note) remains **proposals, not
policy**. Adopting the GTM build plan — connector volume, cost analyst,
conversational layer, MCP exposure (G14) — is a deliberate posture change
requiring explicit CLAUDE.md/BUILD_PLAN amendments. Nothing in §3 depends
on this decision, and nothing forces it. It is Quentin's call, on Quentin's
timeline; the docs are ready when the moment is.

## 6. Standing housekeeping (unblocked, unscheduled)

- **Credential rotations (operator)**: the local-auth admin password
  (2026-07-18 session), `qrsconsulting` admin password, OpenAI + HF keys
  (2026-07-18, moved to `~/.config/secrets.env` but old values exposed in
  session logs). Still outstanding.
- **EC2 uptime awareness**: G9 backfill makes downtime loss-free for
  processed-once mailboxes, but the window accumulates only while up.
- **Advisory-wave pattern**: four CI-blocking dependency advisories in ten
  days (pillow, httplib2/json-repair, pyasn1, postcss). Standing offer: a
  scheduled audit job (waves surface on a timer) or warn-on-PR /
  strict-on-schedule. Adopt if the interruptions start to grate.
- **P2.3 Terraform apply** stays deferred under solo-dev posture; G1
  (portable PDF recordings) minor; per-user memory keys (`ROLES_PLAN` §8)
  waits on G12/first user-authored memory feature.

## 7. One-line summary

The platform is feature-complete against every committed plan, its first
mutating capability is live and clean under supervision, and the next two
weeks have a natural order: close the window, ship the two-axis split it
specified, codify what the evidence already proves — then give the
frontend its IA overhaul on a v7 foundation.
