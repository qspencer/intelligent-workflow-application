# Decision records — convention

How we capture architectural decisions so future readers understand *why* the codebase is
shaped the way it is. The template + detection signals are adapted from `affaan-m/ECC`'s
`architecture-decision-records` (MIT, Nygard format); the **home is our existing docs**, not a
new parallel tree.

## Where decisions live (don't fragment this)

We already record decisions in two places — keep using them:

1. **`docs/ARCHITECTURE.md` — `D1`–`D13`.** The numbered, in-context decisions about the
   spine (agent hierarchy, capabilities, audit, mock-world, …). A new spine-level decision
   gets the **next `D#`** there, in context with the architecture it affects.
2. **`docs/SEMANTICS.md` — the decision log.** For "why we *haven't* done X yet" decisions
   (no formal ontology / KG / semantic layer) with the trigger that would reopen each.

Most decisions belong in one of those. **Do not create a `docs/adr/` directory** — a parallel
ADR tree would compete with the `D#` / SEMANTICS conventions and fragment the corpus. A
decision earns its **own** doc only when it's weighty and standalone enough that inlining it
would bloat ARCHITECTURE (the connector / canvas / email plans are the precedent); index any
such doc in the CLAUDE.md table.

## When to record (detection signals)

Record when you see:
- Choosing between significant alternatives (framework, library, pattern, datastore, API shape).
- "We're doing X instead of Y because…" — the rationale is the valuable part.
- A trade-off accepted with eyes open, or a thing *deliberately deferred* (→ SEMANTICS style).

Don't record trivia (naming, formatting) — that's what ruff/mypy and `CLAUDE.md` are for.

## Template

Keep it readable in ~2 minutes. Present tense ("we use X", not "we will").

```markdown
### D<NN>: <decision title>            ← or `## <title>` for a standalone doc
**Date:** YYYY-MM-DD · **Status:** proposed | accepted | deprecated | superseded by D<NN>

**Context.** The issue/forces motivating this (2–5 sentences; if >10 lines, it's too long).

**Decision.** The change, stated plainly (1–3 sentences).

**Alternatives considered.** For each: pros · cons · **why not**. "We just picked it" is not
a rationale — name what you rejected and why.

**Consequences.** What gets easier / harder; the trade-offs and risks, stated honestly.
```

## Lifecycle

```
proposed → accepted → [deprecated | superseded by D<NN>]
```
When a decision is replaced, mark the old one **superseded** and **link the replacement** —
never silently overwrite. If you're backfilling a past decision, note the original date.

## Good / bad

- **Do:** be specific ("use Alembic expand-contract for NOT-NULL adds", not "be careful with
  migrations"); record the why; list rejected alternatives; state trade-offs; keep it short.
- **Don't:** write essays; omit alternatives; let superseded decisions go unlinked; record
  formatting choices.

## See also

`docs/ARCHITECTURE.md` (D1–D13) · `docs/SEMANTICS.md` (deferral log) · `docs/BUILD_PLAN.md`
(sequencing decisions).
