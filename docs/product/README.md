# Product & go-to-market strategy layer

Seven documents written in an off-site working session (2026-07-15/16, against
repo state at commit 189 / the current HEAD of that day), folded into the repo
2026-07-16 with factual corrections (React frontend, veracium v0.2.1
capabilities, V1/V2 already shipped upstream, first-party-not-proprietary
memory framing).

**Status: proposals, not adopted policy.** `CLAUDE.md`'s operating principles
(validation-driven development, defer-until-a-workload-pulls) still govern
engineering sessions. The gap analysis's build plan — connector volume push,
cost analyst promoted to near-term, conversational layer — is a deliberate
posture change toward productization; adopting it requires amending
`CLAUDE.md` and `docs/BUILD_PLAN.md`'s deferral table explicitly, not
implicitly. Individual pieces (G10 recall injection, the scaffold eval
runner) already align with the existing backlog and need no posture change.

| File | Contains |
|---|---|
| `OPPORTUNITY.md` | The business case: market window (12–18 mo), positioning (trust wedge + cost wedge), GTM phases, pricing, risks, 12-month vision |
| `PRODUCT_SPECIFICATION.md` | Product spec: parity requirements P1–P8, differentiators D1–D7, pricing, NFRs, personas, MVP scope |
| `COMPETITIVE_LANDSCAPE.md` | July-2026 survey of 9 platforms (traditional + AI-native), feature matrix, the empty governance×intelligence quadrant |
| `GAP_ANALYSIS.md` | Implementation vs spec scorecard (accurate as of commit 189) + a 22–31-week plan to close all gaps |
| `LLM_EVAL_FRAMEWORK.md` | Eval methodology for scaffold/execution models: L1–L4 scoring ladder, pass criteria, leaderboard, cost-analyst pipeline |
| `LLM_EVAL_TEST_SUITE.md` | The 50 scaffold test cases (simple/medium/complex/trap) with structural expectations + judge rubrics |
| `VERACIUM_ENHANCEMENTS.md` | Intelligence-layer tiers vs veracium capabilities; verdict: recall injection needs zero veracium changes (verified against v0.2.1) |

Related in-repo material: `docs/CANVAS_ROADMAP.md` (GUI-angle competitive
analysis), `docs/NEXT_STEPS.md` §G10 (recall injection — the unanimous
top priority across these docs), `docs/SEMANTICS.md` (veracium adoption
record), `backend/tools/judge_email_triage.py` + `review_triage.py` (the
existing eval-loop tooling the eval framework generalizes).
