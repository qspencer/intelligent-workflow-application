# F1/F5 trace review — round 14 sidecar

> **Status: in assembly.** The code sections fill in when the package is
> built. §1 is written first because it asks a question we would like
> answered whatever else is in the round.

---

## 1. The at-rest widening — why we did it, and where it disagrees with you

**We would like your read on this specifically.** It is a posture change we
made deliberately, and your round-13 closing comment points at a weakness in
how we made it. We would rather argue it out than quietly keep it.

### Sequence, so the disagreement is not mistaken for disregard

The widening was recommended, decided by the operator, and built **before**
your round-13 verdict arrived. Your position — *"I favor restoring narrowly
defined operational metadata through action-specific schemas. Establish its
source before allowing it through; identifier shape or numeric type alone is
insufficient"* — reached us after v9 had shipped. So this is not work done
against your guidance; it is work your guidance now calls into question.

### What the problem was

The v8 tightening made at rest equal the read path. Measured on a 4,000-row
production sample, **39% of audit entries then withheld every field**.
`_AUDIT_DETAIL` had been written for governance entries (grants, release
decisions) and had never classified the engine-EXECUTION fields at all, so
their absence was an oversight rather than a decision.

### The argument that actually decided it

Not "operators want the trail back". This:

```
instance surface, no grant : {'workflow_id': 'dmarc-ingest', ...}
audit surface,    no grant : {'_withheld_keys': True}
```

The **same reader**, denied a value on one surface that they are handed on
another. Withholding `workflow_id` on the audit surface protects nothing —
they fetch it next door — and costs the trail. `instance_id` likewise.

The secondary argument is about the grant itself: at 39% fully-withheld,
operators reach for raw-trace grants as routine. A grant is the break-glass
control, and making it the daily path erodes it while burying genuine access
in noise. We read that as an argument *for* widening, on security grounds
rather than convenience.

### What we built

19 fields declared as `_TOKEN` / `_COUNT` / `_AMOUNT` — **never `_ID`**,
because `_ID` admits `@` for operator-identity paths and that was the v8
defect you found. Measured effect: fully-withheld **39% → 1%**, and the
vault rate **70.3% → 61%** because fewer details lose anything.

Deliberately still withheld, each for a stated reason: `user_id` (an email
in 416/416 production cases, and unlike `actor_id` it names the person
*acted upon*), `trigger` and `output` (nested schemas a flat declaration
would bypass), and `emitter` (absent from the sample, so no evidence it is
safe — undeclared means withheld).

### Where you are right, in our own vocabulary

**We classified by SHAPE. You are saying classify by SOURCE.** That is
mechanism **M1** in our findings ledger — *"shape used where SOURCE was the
question"* — the first class we ever recorded, and we walked back into it.

The concrete evidence that you are pointing at something real, rather than a
style preference:

- `audit_detail` is **the only one of five asset kinds with no ownership
  typing.** `step_output`, `step_row`, `instance` and `context` each have a
  total `Owner` table (ENGINE / CONFIG / BUSINESS / PROJECTION) with a
  totality test that fails the build on an unclassified field. `audit_detail`
  has none, so nothing forced us to state a producer per field.
- Our declarations are **flat across actions**, while projection already
  dispatches per action for `tool_call`. So `model` is declared once and
  trusted identically whatever wrote it. Today every `model` we can see is
  engine-chosen — but that is an observation about current data, not a
  constraint, and a validator that accepts a 120-char token cannot tell an
  engine-chosen model id from an attacker-chosen one of the same shape.

A shape bound is a real bound — a `_TOKEN` cannot carry a mail body or an
email address — but it bounds **damage**, not **provenance**. You are asking
for provenance, and we do not currently have the machinery to state it for
this asset kind.

### What we think the fix looks like, if you agree

Extend ownership typing to `audit_detail` and declare per **(action, field)**
rather than per field, so the existing totality test covers it and an
unclassified field fails the build. That is the same shape as the four kinds
that already have it, so it is a known quantity rather than a new mechanism.

### The questions

1. **Is the cross-surface argument sufficient on its own** for the subset
   already released to the same grant-less reader elsewhere — `workflow_id`,
   `instance_id` — independent of provenance? Our reasoning is that
   withholding a value the reader can fetch from another endpoint is
   theatre, and theatre in a security boundary is worse than nothing because
   it reads as protection.
2. **For the remainder, is per-(action, field) ownership typing the right
   instrument**, or is there a lighter one that still establishes source?
3. **In the meantime, should v9 stand or be reverted to v8?** Standing means
   ~1% fully-withheld on shape-bounded fields with source unestablished.
   Reverting means 39% fully-withheld and the cross-surface inconsistency
   returns. We lean toward standing, because every declared field is bounded
   such that content cannot survive it and the destructive direction is
   already closed — but that is exactly the judgement we are unsure of, so
   we would rather ask than assume.
4. **Is `user_id` the right call?** We withheld it because it names the
   person acted upon. That costs user-management audit its subject at a
   glance, recoverable only with a grant.

We are not looking for agreement. If the answer is that shape-bounded
declaration is not acceptable at any width without source, we would rather
learn that now than ship two more rounds on top of it.
