# Code-review archives

Secret-clean, reproducible snapshots of the repo cut for external reviewers.

The tarballs themselves are **gitignored** (see `.gitignore`) — they're binary,
large, and exactly reproducible from their commit SHA, so committing them would
bloat the repo for no benefit. This README is the durable record; the archive
is a build artifact you regenerate on demand.

## Why `git archive` (not a folder copy)

`git archive <sha>` emits **only tracked files at that commit**. Everything
sensitive — `.secrets/`, `.env`, `~/.config/workflow-be.env`, refresh tokens,
`.venv` — is gitignored and therefore **cannot** be in the archive. A plain
`tar`/`cp` of the working tree can leak those; `git archive` cannot. Always use
`git archive` for a handoff.

## Regenerate an archive

```sh
# reproducible (gzip -n drops the timestamp → identical bytes for a given SHA)
SHA=$(git rev-parse --short HEAD)
git archive --format=tar "$SHA" | gzip -n > docs/archives/<topic>-review-$SHA.tar.gz
```

Naming: `<topic>-review-<short-sha>.tar.gz`. Verify secret-cleanliness with
`tar tzf <file> | grep -iE '\.secrets|\.env$|refresh_token|\.venv'` (expect
nothing but the `secrets/` **source module**, which is code, not credentials).

## Two kinds of review

**Code** reviews (`trace-governance-review-*`) ask whether the implementation
holds. **Design** reviews (`trace-1.4a-design-review-*`) ask whether an unbuilt
spec is sound. Both ship the whole repo — design reviewers have repeatedly found
blocking issues by reading code the spec described inaccurately.

## Manifest

| Archive | SHA | Date | For | Cover note |
|---|---|---|---|---|
| `trace-f1-review-r3-b7cc1d2.tar.gz` | `b7cc1d2` (branch `p1-reprimitive`) | 2026-08-09 | **F1/F5 review ROUND 3** — the six round-2 findings (R2-1..6) fixed at the CLASS level. Start at `docs/TRACE_F1_REVIEW_GUIDE.md` §R3 | `docs/TRACE_F1_REVIEW_GUIDE.md` |
| `trace-f1-review-r2-b2f913a.tar.gz` | `b2f913a` (branch `p1-reprimitive`) | 2026-08-09 | **F1/F5 review ROUND 2** — the four G-Trace-Review-4 findings (GR4-1..4) remediated. Start at `docs/TRACE_F1_REVIEW_GUIDE.md` §R | `docs/TRACE_F1_REVIEW_GUIDE.md` |
| `trace-f1-review-r11-dc79abe.tar.gz` | `dc79abe` (branch `main`) | 2026-09-18 | **Round-10 return + two detectors built before sending.** Template rewriting no longer touches DATA (the engine renders placeholders in exactly ONE field; our round-9 reasoning was inverted); context paths parse the way the resolver parses them, so a non-ASCII id no longer dangles; the schema gate ENUMERATES the models and ships a control that fires. Plus the two gaps our own ledger admitted: duplicate-definition sweep extended to FUNCTIONS, and grammar agreement — the scaffold now IMPORTS the engine's placeholder pattern instead of copying it. Sidecar: `docs/TRACE_F1_REVIEW_ROUND11.md`. |
| `trace-f1-review-r10-a1cb20e.tar.gz` | `a1cb20e` (branch `main`) | 2026-09-18 | **Round-9 return + a schema-derived SELF-AUDIT.** The reference model was wrong, not just incomplete: agent `inputs` hold CONTEXT PATHS not ids, `pin_params` (fail-closed) was unhandled, learned-memory fields live outside step config, and defaults only materialised inside an existing config dict. Fields and defaults are declared PER FUNCTION now (a helper's default no longer switches on behaviour in its caller). The audit enumerates every string-bearing schema position and asserts the 5 deliberate exclusions; its adversarial step caught an over-reach WE had just introduced (the delimited rewrite hitting config data and a less-than operator). Sidecar: `docs/TRACE_F1_REVIEW_ROUND10.md`. |
| `trace-f1-review-r9-d9d147c.tar.gz` | `d9d147c` (branch `main`) | 2026-09-18 | **Round-8 return fixed + the first self-audited package.** Minting now rewrites DOTTED config references and materialises step-naming defaults (a renamed workflow parsed then FAILED); the golden corpus is ENTRY-POINT aware and a test instruments the run to prove `safe_tool_call` is reached; the historical fixture was regenerated from `b67391d`, the archive the reviewer holds, after being built from the wrong commit. Ships `docs/REVIEW_FINDINGS_LEDGER.md` — all ~45 findings classified into 7 mechanisms with detectors, whose first run found a defect the reviewer had not (version stamps inside the projected output → projector v6). Sidecar: `docs/TRACE_F1_REVIEW_ROUND9.md`. |
| `trace-f1-review-r8-7cfed97.tar.gz` | `7cfed97` (branch `main`) | 2026-09-18 | **Round-7 remediation + the GOLDEN VERSION GUARD.** All five round-7 findings fixed (ownership now composes at the schema NODE; no function may write projection metadata or an unearned `parse_ok`; projector v4; minting by PATH with PARSED conditions; invalid legacy marker signals). The guard makes the version bump a build failure instead of a thing to remember — with two proofs it fires, and a v3 fixture REGENERATED from the archived round-6 projector so historical degradation is tested rather than skipped. Adds the end-to-end coverage the reviewer asked for. Sidecar: `docs/TRACE_F1_REVIEW_ROUND8.md`. |
| `trace-f1-review-r7-38ec7a1.tar.gz` | `38ec7a1` (branch `p1-reprimitive`) | 2026-09-18 | **OWNERSHIP TYPING — the class behind six rounds.** Four owner classes (ENGINE/CONFIG/BUSINESS/PROJECTION) declared for all 57 root fields of the four typed schemas, no default (unclassified = build failure). BUSINESS withheld until a release rule exists (`parse_ok` released, reason recorded). ENGINE defended at the BOUNDARY — a function's output cannot carry engine keys — because StepExecution does not persist the step type, so a verifier could not re-derive the producer. Round-6's three defects also fixed. Sidecar: `docs/TRACE_F1_REVIEW_ROUND7.md`. **Ships the test-time CODE MANIFEST** round 6 asked for. |
| `trace-f1-review-r6-b67391d.tar.gz` | `b67391d` (branch `p1-reprimitive`) | 2026-09-18 | **F1/F5 round 6 — remediation of the round-5 RETURN.** All six findings fixed, each reproduced first: projector version bumped 2→3 (old rows now degrade, not read as tampering), the process-wide tool catalog REMOVED (projection is a pure function again), the withheld count → BOOLEAN (the count was a raw channel), scaffold mints platform step ids, pinned/pin_overrides → booleans, usage counters restored, tool-name resolution made total. **Two of the six were defects we introduced while fixing round 4.** Sidecar: `docs/TRACE_F1_REVIEW_ROUND6.md`. Gate capture rebuilt after round 5's was shown untrustworthy (masked exit code + wrong commit). |
| `trace-f1-review-r5-d67d427.tar.gz` | `d67d427` (branch `p1-reprimitive`) | 2026-09-18 | **F1/F5 round 5 — CONTAINMENT of the round-4 findings.** All three round-4 reproductions closed WITHOUT §1.4a, test-first (each RED against the r4 tree): token-shaped dict keys dropped + counted (`_withheld_key_count`), external routing ids grant-gated, tool names resolved against the live catalog. Round-4's verdict accepted in full, nothing disputed. Sidecar: `docs/TRACE_F1_REVIEW_ROUND5.md`. **Ships captured standalone gate output** at `docs/archives/GATE_OUTPUT_R5.txt` (round 4's reviewer could resolve no deps offline). F3/F4/F6 + B1 still out of scope. |
| `trace-f1-review-r4-1fa67c2.tar.gz` | `1fa67c2` (branch `p1-reprimitive`) | 2026-09-18 | **F1/F5 CONFIRMATION round 4** — ships the two remediation commits no reviewer has seen (`8e9d0d1` class-level fixes: keys-are-content, list decomposition, routing ids, marker totality, audit denylist; `29e2c4e` the masked lint + the disposition). Narrowed on purpose: asks whether the foundation can carry Contract A **now that the flip is ON in production**, and asks the reviewer to challenge the shape-vs-provenance ceiling rather than produce a seventh finding list. Sidecar: `docs/TRACE_F1_REVIEW_ROUND4.md`. F3/F4/F6 + B1 out of scope. |
| `trace-f1-review-ebceb6a.tar.gz` | `ebceb6a` (branch `p1-reprimitive`) | 2026-08-09 | **F1/F5 FOUNDATION code review** — the path-scoped, kind-dispatched projection re-primitive answering the third review's F1 + F5. F3/F4/F6 out of scope. Start at `docs/TRACE_F1_REVIEW_GUIDE.md` | `docs/TRACE_F1_REVIEW_GUIDE.md` |
| `trace-governance-review-r3-8657f88.tar.gz` | `8657f88` | 2026-08-08 | **CODE review, round 3** — the four remediation primitives (P1 validator registry · P2 surface routing · P3a projection stamp + §4.3 predicate · P4 grant/vault CAS) built in response to rounds 1–2. **Decides whether Contract A / B1 hold** | `docs/TRACE_CODE_REVIEW_GUIDE.md` |
| `trace-1.4a-design-review-r7-4b871b7.tar.gz` | `4b871b7` | 2026-08-08 | **§1.4a DESIGN review, round 7** — after folding round 6's two HIGH (executable effect model spanning deterministic functions · supersession no longer terminal) + four stale-text cleanups | `docs/TRACE_1_4A_REVIEW_ROUND7.md` |
| `trace-1.4a-design-review-r6-75c72bb.tar.gz` | `75c72bb` | 2026-08-08 | **§1.4a DESIGN review, round 6** — after folding round 5's three HIGH (influence-graph closure · `expired` lifecycle · audit sidecar withdrawn) + grammar exactness | `docs/TRACE_1_4A_REVIEW_ROUND6.md` |
| `trace-1.4a-design-review-r5-0b6e748.tar.gz` | `0b6e748` | 2026-08-08 | **§1.4a DESIGN review, round 5** — after folding round 4's four blockers (upstream semantic closure · completion-time revocation fence · audit-vs-scrub · approval expiry) + both contract corrections | `docs/TRACE_1_4A_REVIEW_ROUND5.md` |
| `trace-1.4a-design-review-r4-90dee87.tar.gz` | `90dee87` | 2026-08-08 | **§1.4a DESIGN review, round 4** — the per-workflow declassification approval. Whole repo, not docs-only: round 3's blocking finding came from reading `engine/functions.py`. Nothing in §1.4a is built | `docs/TRACE_1_4A_REVIEW_ROUND4.md` |
| `trace-governance-review-e397b8b.tar.gz` | `e397b8b` | 2026-08-02 | External **code** review of the trace-governance build (TG1–TG3d-1 + gate-wiring; Contract A + B1) | `docs/TRACE_CODE_REVIEW_GUIDE.md` |
| `trace-governance-review-0c847fa.tar.gz` | `0c847fa` | 2026-08-03 | **Round 3** — after the four build-conformance primitives (P1 typed projector, P2 surface inventory, P4 grant+vault CAS, P3a rehydration predicate); code at `29a42f5`, guide refreshed at `0c847fa` | `docs/TRACE_CODE_REVIEW_GUIDE.md` |
| `trace-governance-review-2cfacfc.tar.gz` | `2cfacfc` | 2026-08-02 | **Re-review** after remediating all 10 findings from the `e397b8b` review (code fixes at `5e0d84b`; see `docs/NEXT_STEPS.md` G-Trace-Review + `backend/tests/test_trace_review_fixes.py`) | `docs/TRACE_CODE_REVIEW_GUIDE.md` |

## Integrity hashes (F1/F5 review ROUND 11)

```
  7400696130750a4e6a2de087b9d27ced11c632204d222fbf46d81fd93f2fd475  docs/TRACE_F1_REVIEW_ROUND11.md
  08af1a1236f2e27d7b2ec4693330f97231ce0920d75087e521427a71db0c8199  docs/archives/GATE_OUTPUT_R11.txt
  d409df367a5e2ec3d1309c51894c4790d30691737bd689a0cc1746b711bd8a88  docs/archives/CODE_MANIFEST_R11.txt
  79157fcc14637969eefea15cac54a7fa0ec90b04e630d33367d4e02eb72b05bf  docs/REVIEW_FINDINGS_LEDGER.md
  0a933adc4ae68af817c59c39a4078cbc26fadbf9aa3cfc3db57420b2d1457eb8  backend/src/workflow_platform/scaffold.py
```

**Aggregate source hash** (recomputable from the extracted package, no git):

```
d8d8859441a332cc78eef9998ebeb2c8c79900d20ea56f00286b985a7b3be8e2
cd backend && find src tests -name '*.py' -type f | sort | xargs sha256sum | sha256sum
```

Tarball: `e780be097ab42545d191318368cb140ed5ff8fdef9e309863fdf563bf0c5821e`
(642 files). Verified by EXTRACTING and recomputing with no git: aggregate
matches, the index carries its own row, all five gates show `exit: 0`, the
shipped evidence contains no SyntaxError (the first capture attempt did — a
mangled inline quote — and was regenerated), and all four fixtures ship.
Secret and mail sweeps returned nothing.

```sh
git archive --format=tar dc79abe | gzip -n > docs/archives/trace-f1-review-r11-dc79abe.tar.gz
```

## Integrity hashes (F1/F5 review ROUND 10)

```
  46a1a4059405d5a109beb16c610ba0b246ad4da965ef400047ca5d9eb33622be  docs/TRACE_F1_REVIEW_ROUND10.md
  30ec336dbaa6dff65a9094575ed1508869e20cb7a41c84c5376e78cfa75b98f3  docs/archives/GATE_OUTPUT_R10.txt
  59cdbede627d40d08c610f4c884314b8549b043ce1d89b801a8de2f7c80da3cc  docs/archives/CODE_MANIFEST_R10.txt
  37f9fa8221ffcb6cde5c22a483db6f4dab74dc5e293b4f31f4c6f9cae8412d8a  backend/src/workflow_platform/scaffold.py
  8401de8d2dc64b2b263d941779ca648c5580e737254973121c67259f0efb34de  docs/REVIEW_FINDINGS_LEDGER.md
```

**Aggregate source hash** (recomputable from the extracted package, no git):

```
641d4ebfcec65c8367af310c78525d93c38ead8805901f0b8176794fa5fc3447
cd backend && find src tests -name '*.py' -type f | sort | xargs sha256sum | sha256sum
```

Tarball: `32248892f6fff01fb6560e5aca0171bb0335ab6f49cda18472a53f2afa7d644a`
(638 files). Verified by EXTRACTING and recomputing with no git: aggregate
matches, the index carries its own row, all four fixtures and the findings
ledger ship. Secret and mail sweeps returned nothing.

```sh
git archive --format=tar a1cb20e | gzip -n > docs/archives/trace-f1-review-r10-a1cb20e.tar.gz
```

## Integrity hashes (F1/F5 review ROUND 9)

```
  38290aafccfa6c279a0cd8f971f4d765e35c037f3e82809b9ac0861bfeedce1e  docs/TRACE_F1_REVIEW_ROUND9.md
  e868278e8ed8b6d7a27c91e68de51652b052226709e9fac51cc7edcb94d7313d  docs/archives/GATE_OUTPUT_R9.txt
  768cdd3f038938afc1416095c2979519864092c19b8e06167762083cf5b831db  docs/archives/CODE_MANIFEST_R9.txt
  4cfde544736904888ad26b08d544b5aa5694c0febd762d879ac7b9be4bd57be4  docs/REVIEW_FINDINGS_LEDGER.md
  c1a9aa533577c20f0ed3d3ae29283855cff280d5cbbcb4b94d4d3f0672602b21  backend/src/workflow_platform/scaffold.py
```

**Aggregate source hash** (recomputable from the extracted package, no git):

```
7a784c9032afab1a73eafb504852c3d8651873e430f278c46452e2254b7ce0bf
cd backend && find src tests -name '*.py' -type f | sort | xargs sha256sum | sha256sum
```

Tarball: `0d88d9ec8925b5c5256b530a759fe1f0ceb89bfa0df2157b3af979a418ff8865`
(635 files). Verified by EXTRACTING and recomputing with no git: aggregate
matches, the index carries its own row, all four fixtures and the findings
ledger ship. Secret and mail sweeps returned nothing.

```sh
git archive --format=tar d9d147c | gzip -n > docs/archives/trace-f1-review-r9-d9d147c.tar.gz
```

Four golden fixtures ship (v3 historical + v4/v5/v6), each naming the commit
it was frozen from, so a superseded fixture can be RE-DERIVED and shown
authentic rather than trusted — the round-8 correction.

## Integrity hashes (F1/F5 review ROUND 8)

```
  f428e0beee1ca115cba3956fb4243f6dee4be18eff421b8ed55fb1649c39adb7  docs/TRACE_F1_REVIEW_ROUND8.md
  b5337a0378fa7b0ee3833bcbd943757778deb52746406b2a3f9a77afe2bcd2de  docs/archives/GATE_OUTPUT_R8.txt
  cdc04a22931575b843295b01ff6bd903c64afebba52300033baab1c24ddd9265  docs/archives/CODE_MANIFEST_R8.txt
  1237cb04a25f7bd0b5bcda7e257e714392500de16e378c942d0da1104e41cde6  backend/src/workflow_platform/trace_projection.py
  e71dc669b32b83f388f4d42c591f943842190c92500e5b473ac287189a0fc70f  backend/tests/test_projection_golden.py
```

**Aggregate source hash** (every `.py` under `backend/src` + `backend/tests`,
recomputable from the extracted package with no git):

```
0f0b823eca1ca56ae56c4d1d2fddcbf66333801a66b4604a7114d7577be84212
cd backend && find src tests -name '*.py' -type f | sort | xargs sha256sum | sha256sum
```

Tarball: `f44e71a98f580feb89699f0831811f102f36403e3ca4150a0f16888cb22a1983`
(629 files). Verified by EXTRACTING the package and recomputing with no git:
the aggregate matches, the index inside carries its own round-8 row, and both
golden fixtures (v3 historical, v4 current) ship. Secret and mail sweeps
returned nothing.

```sh
git archive --format=tar 7cfed97 | gzip -n > docs/archives/trace-f1-review-r8-7cfed97.tar.gz
```

Five gates captured, not four — `pip-audit` joined the set after a red CI went
unnoticed locally for several pushes.

## Integrity hashes (F1/F5 review ROUND 7)

```
  529b7f12b8666e6c5b23f472ab07f7bcec2ae3c60d0b3e8b28c2ab77e00df5ba  docs/TRACE_F1_REVIEW_ROUND7.md
  9ad5a03351de3fc68119178f879166b3cc939b967a0619fd9546665afc2f6373  docs/archives/GATE_OUTPUT_R7.txt
  c34de02c58ffef9992a20b15d975fda5fc5343ad9ef5fa16a7a1562ecd39b7bd  docs/archives/CODE_MANIFEST_R7.txt
  d9793a147318646969f47118020acdcb88a2f4ac6f51cced2082114f82d59116  backend/src/workflow_platform/trace_projection.py
  d3ce1b67dbd9a878c07062d3ac1e477169da06a027707564d988ae2e47bd4be7  backend/tests/test_trace_boundary_properties.py
```

Tarball: `9195ff897fc48b05b786ccc345d28714cfe990ba4af0fcad416145d53a56495a`
(619 files). Secret + mail sweeps returned nothing. Rebuild byte-identically:
`git archive --format=tar 38ec7a1 | gzip -n > docs/archives/trace-f1-review-r7-38ec7a1.tar.gz`

**Verified by extracting the package and recomputing with no git:** the
aggregate below matches, and the index inside carries its own round-7 row —
both of round 6's packaging asks, checked rather than asserted.

**Aggregate source hash** (every `.py` under `backend/src` + `backend/tests`,
recomputable from the extracted package with no git — this is round 6's ask):

```
75b1c3af3505cb89f657fde81f2b97ab358ac3b9bd36ec96b7251dca8d7d30c5   225 files
cd backend && find src tests -name '*.py' -type f | sort | xargs sha256sum | sha256sum
```

## Integrity hashes (F1/F5 review ROUND 6)

Per-file hashes of the archived content, computed from the committed objects
BEFORE the build so they can ship inside the package:

```
  626c70ff8f6080b84f599f193ab503691c420b77c4e7afad01205e2ec01898e1  docs/TRACE_F1_REVIEW_ROUND6.md  (sidecar)
  e9066736e0fcf98ceb985ef24921a8cd656d0618b9f2199798dfbed7e4c927d4  docs/archives/GATE_OUTPUT_R6.txt  (gates; see its header)
  dd8c7c812b462f7b4e190ccdd85ae9c29f7df91d1fc6083144601a0bc282b556  backend/.../trace_projection.py
  175c1f48fa96730d659db826efe11cd8efa8ae35ff79312f4b115db1fcea61b5  backend/tests/test_trace_boundary_properties.py
```

Tarball hash (post-build; a package cannot contain its own hash):

```
4859d0a463e40138c877edd1b7ea890e57cbbe7d3fb839d5784c23771ead862a
         trace-f1-review-r6-b67391d.tar.gz
```

Gates ran at `f5a2819`; the archive is `b67391d`. `git diff --stat
f5a2819..b67391d` is **docs only** (the sidecar, the capture, this row) —
exactly the claim the capture's header makes and invites you to check.

Rebuild byte-identically:
```sh
git archive --format=tar b67391d | gzip -n > docs/archives/trace-f1-review-r6-b67391d.tar.gz
```

Round-5's capture was NOT trustworthy — it named a commit the archive did not
contain, and printed `exit: 0` beneath a reformat warning because `$?` read
`tail` through a pipe. Round 6 reads `$?` before any pipe and states the tree
the gates ran at, with the command to verify the archive differs only in docs.

## Integrity hashes (F1/F5 review ROUND 5)

Per-file hashes of the archived content (computed from the committed objects
BEFORE the build, which is why they can appear inside the package — a package
cannot contain its own tarball hash; that is appended below after the build):

```
  1f2626daf6f8b5165b9405c9b0274a7d68ee7621de0b72447f24f2666ae94fb1  docs/TRACE_F1_REVIEW_ROUND5.md  (the sidecar)
  315908fe5067c5336b44ce17b212dc0ab3766c85a0a1b9fee89b84c64b6e7eb1  docs/archives/GATE_OUTPUT_R5.txt  (captured gates)
  77b0d7393b0a364575159298da7ce1a5f12e5a8b2aff508e2f0b00e346b8e46e  backend/.../trace_projection.py  (the projector)
  41947b246f9c4a2d1cbccda7cb8b2817ea20671730c10c3ee91b2dd34f443275  backend/tests/test_trace_boundary_properties.py  (39 properties)
```

Tarball hash (appended post-build, necessarily — a package cannot contain its
own hash; the per-file hashes above are inside it and verify the same content):

```
66caa92f09a7acf299eda80d9bf89756738cae6c5de86db6ee3ca9834a9a7880
         trace-f1-review-r5-d67d427.tar.gz   (614 files)
```

Verified at build: no `.secrets`/`.env`/refresh-token/`.venv`/`.memory` paths,
and no personal mail data (`data/email_triage|qspencer|backups/`) — both sweeps
returned nothing.

Rebuild byte-identically:
```sh
git archive --format=tar d67d427 | gzip -n > docs/archives/trace-f1-review-r5-d67d427.tar.gz
```

Round-4 packaging failures fixed here: the record lands BEFORE the build, so
the index inside the package carries this row; and captured standalone gate
output ships inside, because round 4's reviewer could resolve no dependencies
offline and could therefore verify none of our suite/lint/type claims.

## Integrity hashes (F1/F5 review ROUND 4)

```
b08e0574310bc01853dfcdaf604c2f4ffef44a5189fe3d7cf6c54437a0490039
         trace-f1-review-r4-1fa67c2.tar.gz

docs + code, as archived:
  06adbed0676ba2ccacdec8431595777e24dce5ea4d5f53c259a972308f4a23bd  docs/TRACE_F1_REVIEW_ROUND4.md  (the sidecar)
  3960c6b92f7a952f0982d5e14736912d8de68a4da1790f939e6737746b08b0a9  backend/.../trace_projection.py  (the projector)
  a47008c1a0abe689f1b92ad73036e8bbd92bc2cb97b77bd84de0fbafe1ddd196  backend/tests/test_trace_boundary_properties.py  (the contract)
  8d121e5574cb7e8a85bf38b7dc48d4e0b69e09d1364d4ae3e464a92fb448b114  backend/.../trace_migration.py  (at-rest backfill)
```

Verified secret-clean at build: `tar tzf … | grep -iE '\.secrets|\.env$|refresh_token|\.venv|\.memory/'`
returned **nothing**, and a second sweep for real mail data
(`data/email_triage|qspencer|backups/`) also returned nothing. 612 files.

Rebuild byte-identically with:
```sh
git archive --format=tar 1fa67c2 | gzip -n > docs/archives/trace-f1-review-r4-1fa67c2.tar.gz
```

## Integrity hashes (F1/F5 review ROUND 3)

```
archive  bef0a98586105c3d55b7b4a65a3da3f94ee7fd257b02c1d78290d1276bfebd3b
         trace-f1-review-r3-b7cc1d2.tar.gz

  3d215849c4d09dcbd515e8f1a4a6f625a1d1b5b8413b5f0d881fe62f823e1f33  TRACE_F1_REVIEW_GUIDE.md  (§R3 = R2-1..6)
  60fb319ede52318b72531b7209762deeaccf6322981392f2ec0d8f9c5fdf70a7  backend/.../trace_projection.py
  8d121e5574cb7e8a85bf38b7dc48d4e0b69e09d1364d4ae3e464a92fb448b114   backend/.../trace_migration.py
```

Round 3 of F1/F5: the six round-2 findings fixed at the class (keys-are-content
key validation, routing-id token validator, one total+exact marker predicate,
audit `query`, backfill vaulted-but-unstamped repair) with generative class
tests. Full suite 1040 passed under both environments. The flip is ON in the
deployment (B1 encryption working) — earlier "flip is off" claims withdrawn.
NOT self-certified (0-for-5 on that).

## Integrity hashes (F1/F5 review ROUND 2)

```
archive  4d1b466313aac3930b3e3f7bb3fabb7fcaf022eff02d3339ec5edb363d97229e
         trace-f1-review-r2-b2f913a.tar.gz

  7967d2fe3653323a59fab1c10d07cd5c31fa07a7751be7aac474aed3e1d5e434  TRACE_F1_REVIEW_GUIDE.md  (§R = GR4-1..4 remediation)
  07de7180073f3f0d40b3efd6e27bac1caebb6af3e6c0eaa431ceb72611682e04  backend/.../trace_projection.py
```

Round 2 of F1/F5: the four findings the first foundation review returned
(safe_tool_call structural leaves, escalation context kind, fork/backfill
stamping, raw audit writes + verifier) are all remediated with surface-exercising
tests. Full suite 1029 passed under both environments. NOT self-certified — a
re-review must confirm. F6 and the third review's F3/F4 remain out of scope.

## Integrity hashes (F1/F5 foundation package)

```
archive  8d916b492fe467d0dfa6836b4787547bb21cb904d6f3ba9bc5cefb7cd26c797c
         trace-f1-review-ebceb6a.tar.gz

docs + code, as archived:
  cf7522e234d452aaa30a82cc5f8340c1ccc1b9a238e35f66dd4f6e54e93d14cc  TRACE_F1_REVIEW_GUIDE.md
  ce68b2416e1e330f0301d88a24ba7249fc85238d11886db8d82d7e3aacef7910  backend/.../trace_projection.py  (the re-primitive)
  d28cf4e50b848ff07d1ae2e524b38c76c713630be0c31baff5b90188c8f16120  backend/tests/test_trace_boundary_properties.py  (the contract)
```

This archive is the **branch `p1-reprimitive`** tip, not `main` — it carries the
schema-driven projector under review. Every §3 reproduction in the guide was
re-verified against these exact bytes before packaging.

## Integrity hashes (round-3 CODE package)

```
archive  555079c6b180ddbfe2ba97ff024d1a96896b6e2f0945f8e35a1acd22aa977b67
         trace-governance-review-r3-8657f88.tar.gz
```

The trace surface is **byte-identical to `29a42f5`**, when the four primitives
landed — verified by `git diff` over `trace_*.py`, `raw_trace_grants`,
`raw_trace_audit`, `redaction`, `ws` and `executor`. The intervening commits are
dependency upgrades and an unrelated schema-drift check, so this is the
primitives as built.

## Integrity hashes (round-7 design package)

```
archive  0db0b3708387ff8b450e79fee004a324f9e45bdc1f09a3a3cd691eda888c7660
         trace-1.4a-design-review-r7-4b871b7.tar.gz

docs, as archived:
  b646f7b769bb22fbb981a688a4b9fc1c2f7c7b14e05d13dfa64eb175726ecb41  TRACE_1_4A_REVIEW_ROUND7.md
  e97a8439dba92b963b485e63d3a2c8c08cf03e7d5da85c55d8d82d41a04f7cbd  TRACE_GOVERNANCE_PLAN.md
  ff37a8834d9831a4c19c6e366043904f642e759fc1eff09a4720caaa1d4fc7a6  THREAT_MODEL.md
  e658c01907cd7fdb6985d6cad1a761213abc0832656a25706d3404ba9bae75c4  EXECUTION_SEMANTICS.md
```

**`THREAT_MODEL.md` and `EXECUTION_SEMANTICS.md` are byte-identical across rounds
4, 5, 6 AND 7** — reviewers independently reported these digests in rounds 4, 5
and 6. Both companions need no re-reading; only the handoff and the plan move.

## Integrity hashes (round-6 design package)

```
archive  3c5c3def736581256c1fa6bacb78677ae86a039db17f8459ba216908cd2224a3
         trace-1.4a-design-review-r6-75c72bb.tar.gz

docs, as archived:
  550527c5f3da3dc992d15678cc0310acbb9965853863b554da7ec450852d24ca  TRACE_1_4A_REVIEW_ROUND6.md
  3707c746007d36f3dff01da6ac76116019c8ab74c23c698a3f41ecd76eba5a0e  TRACE_GOVERNANCE_PLAN.md
  ff37a8834d9831a4c19c6e366043904f642e759fc1eff09a4720caaa1d4fc7a6  THREAT_MODEL.md
  e658c01907cd7fdb6985d6cad1a761213abc0832656a25706d3404ba9bae75c4  EXECUTION_SEMANTICS.md
```

**`THREAT_MODEL.md` and `EXECUTION_SEMANTICS.md` are byte-identical across rounds
4, 5 AND 6** — the reviewer confirmed these same digests in round 4, so both
companions need no re-reading. Only the handoff and the governance plan moved.

## Integrity hashes (round-5 design package)

Published so the reviewer can confirm they received what was sent — round 4's
reviewer verified by SHA-256 unprompted, so it is worth pre-supplying.

```
archive  f20322dd34753c6c2e7e357dcd3378b52b12819162547709007a47ed8d587a3a
         trace-1.4a-design-review-r5-0b6e748.tar.gz

docs, as archived:
  ade602b282c2e0e369c7da5506245689d80bf5105ecf244eb73c84315c1f803f  TRACE_1_4A_REVIEW_ROUND5.md
  29f6612cb53036f712edbf99f7220ff46183b52dd418c37f8d79f3bbab046da8  TRACE_GOVERNANCE_PLAN.md
  ff37a8834d9831a4c19c6e366043904f642e759fc1eff09a4720caaa1d4fc7a6  THREAT_MODEL.md
  e658c01907cd7fdb6985d6cad1a761213abc0832656a25706d3404ba9bae75c4  EXECUTION_SEMANTICS.md
```

**The two companion hashes are byte-identical to the ones round 4 reported**
(`ff37a883…`, `e658c019…`), so `THREAT_MODEL.md` and `EXECUTION_SEMANTICS.md` are
provably unchanged since that review and need no re-reading. Only the handoff and
the governance plan moved.

---

## Round 12 — audit-detail vaulting + the at-rest audit tightening (2026-09-18)

First round whose subject is **not** the F1/F5 projection primitive. Rounds
8–11 found zero defects there, so the line closed and the scope moved to the
work the reviewer named next.

```
commit   b60e9da
tree     01f0fda12b4f2fe5c4ae0b36b8f96d162dbb2377
archive  cb2d4ac86daa1f38c3873d0189a563ba9747d7cbff75a36739b82383ac70e1c7
         trace-f1-review-r12-b60e9da.tar.gz
```

**Manifest is extraction-verified**: unpacking the archive and recomputing
`find src tests -name '*.py' | sort | xargs sha256sum | sha256sum` yields
`72f372e3…`, byte-identical to `CODE_MANIFEST_R12.txt` (232 files).

Companions, delivered alongside (not inside the archive, as in prior rounds):

```
1c2dcc7ec3675398b1039263ce8b721e0994cc2a9d5f5a14ca06b5fc41a73db6  TRACE_F1_REVIEW_ROUND12.md   (sidecar)
d7ebe59274e29e5eae2825cad74372319c41f72131bf0db806e04592704a217e  TRACE_AUDIT_VAULT_DESIGN.md  (design record)
4d97e95d0189abdc44f06667dafb9e2bb712de194631441eefb75c305c653427  CODE_MANIFEST_R12.txt
932a1bdc8613b34294d25330ed96c2a21f0d62e7fb91d3ff003d2a2564db14d5  GATE_OUTPUT_R12.txt
```

The gate capture carries the six **controls**: each cited detector sabotaged
with the defect it claims to catch, observed failing, restored, re-run. The
pre-package protocol found three defects of its own this round — a validator
admitting an email on routing ids, a golden corpus that could not see the
fields the change released, and fixture provenance naming the wrong commit —
all fixed before the package was built.

---

## Round 13 — the round-12 fixes, plus two of the same class we found ourselves (2026-09-18)

```
commit   73cd12a
tree     608a23db4789fa09e73de47497a6120c4d13a6b2
archive  88ea9396e6720e5712f0499bcfff2bc4afd2a936c8da6ec3a0a6537c023b618a
         trace-f1-review-r13-73cd12a.tar.gz
```

**Manifest is extraction-verified**: unpacking and recomputing yields
`f3bec68a…`, byte-identical to `CODE_MANIFEST_R13.txt` (234 files).

Companions, delivered alongside:

```
7fea30505c245a205feb770bcfcf01739cd0bd9305ad87ae29707b1956cbe617  TRACE_F1_REVIEW_ROUND13.md   (sidecar)
d7ebe59274e29e5eae2825cad74372319c41f72131bf0db806e04592704a217e  TRACE_AUDIT_VAULT_DESIGN.md  (design record)
104da0af5543f9aed96405113fa3d579f88b6f40d7068fc600286d5866177ba0  CODE_MANIFEST_R13.txt
b12aa69f39ff37682249e6ecc0d8d688156068e5ef80949d63552850b744df8c  GATE_OUTPUT_R13.txt
```

The gate capture adds two things round 12 asked for: the Postgres-gated
suite and an **alembic up/down/up rehearsal of `0013`**, since migration
execution was not independently verifiable from the last archive. It carries
eight controls, each sabotaged and observed failing.

The protocol found three problems before this left: the round-12 AEAD fix
was **unverified** (removing the binding broke no test), a commit went out
with mypy red, and two stale `sabotaged exit=0` lines were sitting in the
evidence. All fixed; the first is now pinned by two tests.

---

## Round 14 — the round-13 fixes, three self-found, and a question (2026-09-19)

```
commit   da2d179
tree     a45f22b0f07376ce5ece05ca0d5ab9a2e1e534e6
archive  a26a4383fce77fc428081e68e5c3e19be362eb129dda5dfebfc0761592e80452
         trace-f1-review-r14-da2d179.tar.gz
```

**Manifest extraction-verified**: unpacking and recomputing yields
`9532e335…`, byte-identical to `CODE_MANIFEST_R14.txt` (234 files).

Companions:

```
bf46e7918554051e3efd98e27ed11b865d1df8ced9199b7d7daf86c808de445e  TRACE_F1_REVIEW_ROUND14.md   (sidecar)
bc5822244e861aa4e770da76315ab1f2221a1e5b1a9dfe09db39854e60db20e0  TRACE_AUDIT_VAULT_DESIGN.md  (design record)
99db0e1d67d1d3154cb0f4b1f811568ce505a7f21a150c703951f550eaed99a7  CODE_MANIFEST_R14.txt
e0afc76e41019d68b87b13781fe691bd3a02a4fd3ee27945283de99560002458  GATE_OUTPUT_R14.txt
```

**§1 of the sidecar asks a question rather than reporting work**: the
at-rest widening (v9) classifies by SHAPE, and round 13 closed by saying
source must be established and shape is insufficient. That is mechanism M1
in our own ledger. The section sets out why we did it, concedes where the
critique lands, and asks four questions — including whether v9 should stand
or revert meanwhile. It also records the sequence: the widening was decided
and built BEFORE the round-13 verdict arrived.

---

## Round 15 — the round-14 fixes, and two answers put back as questions (2026-09-19)

```
commit   80db6ef
tree     8fe879eca881e4c8081a3f2426e0bd708d858305
archive  4d26e76fbcbae6fff6564dab0269830dd2dfbd728ebda29fc26580ece064a2e9
         trace-f1-review-r15-80db6ef.tar.gz
```

**Manifest extraction-verified**: recomputing from the unpacked archive
yields `8581e322…`, byte-identical to `CODE_MANIFEST_R15.txt` (235 files).

Companions:

```
b0c274dad84f484c377f1dcc46a06d9b745355051d6dfc92eabfbbcb033c61f6  TRACE_F1_REVIEW_ROUND15.md   (sidecar)
b569e28291a0612cf0a3aa5b4218915e5f37c43f97f840deb403b1e651fbe181  TRACE_AUDIT_VAULT_DESIGN.md  (design record)
bce5c4532bf506683c6d9394d1239e2cb63237e9bd61cc05b0af1467eb12bff1  CODE_MANIFEST_R15.txt
25d956a57e99383781db3af9e0dd909eec70536111f64b3844cfe670386a03ee  GATE_OUTPUT_R15.txt
```

**§1 asks rather than builds.** Two of the reviewer's own round-14 answers —
the per-(action, field) registry with typed constructors, and an opaque
subject identity for `user_id` — are deliberately unbuilt, because each has
a design question inside it that we would rather get wrong on paper than in
a migration.

Projector **v10**: the widening reclassified by SOURCE after the reviewer
demonstrated `evidence_ref` carrying input-derived content past a `_TOKEN`
validator to a grant-less reader. Six controls; the F4 race now has an
executable proof (three `UniqueViolationError`s against the old code, none
against the new).

---

## Round 16 — the round-15 fixes, and a third instance we found ourselves (2026-09-19)

```
commit   0f89fe0
tree     3ff50eff60606b72424d2fd69e1039ca87ba6097
archive  be8b13658c69b7ca81e161bbf15262b5b5a20cfbd73aaaae1f2a6e182919eb25
         trace-f1-review-r16-0f89fe0.tar.gz
```

**Manifest extraction-verified**: `9ad74f17…`, byte-identical to
`CODE_MANIFEST_R16.txt` (235 files).

Companions:

```
84e0f2a8ab6e3c3e13b21fb590b297cb57ef91cea1fce96e080d6061959d53df  TRACE_F1_REVIEW_ROUND16.md   (sidecar)
3b960f1144260cd6d86f815567501116a2ad132abdfe1f41dd2cfc7352906ccf  TRACE_AUDIT_VAULT_DESIGN.md  (design record)
48c6a8dbfef5c027abb725e4d451f7437eb0fe48afa1c95368c8f2f7e35415af  CODE_MANIFEST_R16.txt
55317f33fdffa3f6643db19acf72e9e927c90960c38c5f4f5f8d77578b69c5e7  GATE_OUTPUT_R16.txt
```

The pre-package pass is the substance of this round: it found the SAME
defect on `explain_step`, a surface the reviewer had not tested, by
enumerating the CALLERS of the recovery helpers instead of answering the
counterpart question from memory. It also found a control that did not
fire, two follow-ups missing from the backlog, and a stale field count in
the design doc.

---

## Round 17 — the round-16 fix, the same defect on a third field, and the OCR test (2026-09-19)

```
commit   b4dbab5
tree     4721cb814cdd44cee2ea6a28a5c3319de996f71e
archive  55a70d3707b5a43ba5776b3a5a9b1b785eb7738338cc084469c7462dc67b2505
         trace-f1-review-r17-b4dbab5.tar.gz
```

**Manifest extraction-verified**: `836b8ba8…`, byte-identical to
`CODE_MANIFEST_R17.txt` (235 files).

Companions:

```
8bb22c07392842305759184c0dd883e9355b678461f22d70e4cb32344b7dc757  TRACE_F1_REVIEW_ROUND17.md   (sidecar)
3b960f1144260cd6d86f815567501116a2ad132abdfe1f41dd2cfc7352906ccf  TRACE_AUDIT_VAULT_DESIGN.md  (design record)
21366bcfcfe30fcaa548dcc73c9c635a6231ec5ac17483509ac08dbffb142f60  CODE_MANIFEST_R17.txt
5caf206a5510581a7ca562428630b57428bf22e9773b43ad1a835fa969190cc3  GATE_OUTPUT_R17.txt
```

Two things this round that are not findings but matter:

- The failing test the reviewer reported in **every round since 12** was
  ours, not their environment: a 12-character PDF fixture fell below the
  30-char native threshold, so extraction silently used OCR and the
  assertion depended on the local tesseract build.
- **One control in the capture did not fire, and says so.** Reverting that
  fixture cannot fail here, because our tesseract reads it correctly —
  which is the defect. The evidence is an environment-independent property
  instead (`is_native` becomes True, so tesseract is never invoked).
