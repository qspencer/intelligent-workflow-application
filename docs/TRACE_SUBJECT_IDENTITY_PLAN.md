# Subject identity for `user_id` — lifecycle first, format second

Status: **design, 2026-09-19.** G-Trace-Subject-Identity, reviewer-specified
in the round-14/16 returns. Their closing constraint is why this document
exists before any code:

> Define rename, deletion and org-transfer behaviour **before** changing any
> stored identity format, and preserve historical attribution.

The format change *is* the build, so the lifecycle answers gate it. §1–§4
settle them. §6 is the build sequence. §7 is what is deliberately not done.

---

## 1. The measured exposure — and one correction to the spec

Counted over the live audit log (91,808 rows), because the backlog entry
asserted a shape rather than a count.

| | rows | note |
|---|---|---|
| `detail.user_id` present | 6,354 | |
| …email-shaped | **6,351** | memory-namespace keys, **2 distinct addresses** |
| …joining to `users` | 3 | `user_created` / `user_updated`, UUIDs |
| `actor_id` email-shaped | **0** | see below |

**The spec's largest claim is false on this deployment.** It says `actor_id`
*"holds an operator email on every audit row, which makes it the larger
exposure."* It holds no email on any row. The six distinct human values are
local-mode UUIDs (`sub` = `users.id`) and dev-mode handles (`qspencer`,
`carol`, `e2e`). The claim is **provider-dependent**, not wrong in
principle: an OIDC issuer may well put an email in `sub`, and D4 makes the
IdP the sole authority over it. So it is a real risk for a deployment we do
not have, and not an exposure we have.

That inverts the priorities the spec set: `user_id` is the whole of the
live exposure at 6,351 rows, and `actor_id` needs a **guard**, not a
migration (§5).

The second reviewer point is literally true here rather than hypothetical:
**6,351 of 6,354 `user_id` values do not join to `users`.** They are
`org:<org>:user:<mailbox>` memory-namespace keys. Any design that assumes
`user_id` is a platform user is wrong for 99.95% of the rows.

---

## 2. Subject kinds

A subject reference is `{kind, ref, org_id}`. Three kinds, closed:

- **`platform_user`** — `ref` is `users.id`. Already opaque, already stable.
- **`mailbox`** — a correspondent address inside a memory namespace. `ref`
  is a keyed pseudonym (§3).
- **`unknown`** — a namespace key that is neither. Classified rather than
  guessed; it carries no `ref`.

`org_id` is recorded **on the event**, never inferred from the ref — the
reviewer's first constraint, because `users.id` survives an org transfer
and so cannot itself be a tenant-scoped identity.

---

## 3. The mailbox pseudonym, and what it is keyed over

`ref = HMAC(key, normalize_entity(address) || org_id)`, truncated, with the
key from the environment (the pattern `codified_sender_check`'s sampling
key already uses — never the repo).

**Keyed over `(address, org)` and not the address alone.** Otherwise the
pseudonym is a cross-tenant join key: two orgs corresponding with the same
person would produce the same ref, and a reader entitled to one org's audit
could correlate into another's. Tenant isolation is an invariant here
(THREAT_MODEL §5), so the correlator has to respect it.

`normalize_entity` is reused rather than re-implemented: it already folds
case and strips plus-addressing, and a pseudonym that splits on
`User+Tag@x` when the memory store does not would make the two disagree
about who the subject is.

---

## 4. Lifecycle — the three answers the build was gated on

### Rename

- **platform_user:** no effect. `users.id` is stable across an email
  change, so historical attribution is preserved with no work.
- **mailbox:** the pseudonym is derived from the address, so a rename
  yields a **new ref and a split history**. Accepted, deliberately: the
  learned-memory store already partitions by address, so a renamed mailbox
  already starts a fresh partition today. The pseudonym **inherits the
  memory namespace's own identity semantics and does not invent a stronger
  one** — a mapping table that survived renames would be a second identity
  with its own lifecycle, and the two would drift.

### Deletion

- **platform_user:** refs are **not** deleted with the subject. This
  follows the rule already stated on `User`: *"the audit log's `actor_id`
  remains the raw sub string by design — audit entries must not dangle or
  mutate when users are reorganized."* A ref for a deleted user resolves to
  `unresolvable`, and the audit row keeps its shape. Rewriting history to
  erase a subject would destroy the attribution this whole item exists to
  preserve.
- **mailbox:** nothing to delete — the pseudonym is derived, not stored.
  **An erasure request therefore cannot be served by touching refs.** It
  acts on the two places the address actually lives: the raw-trace vault
  and the learned-memory store. The pseudonym then becomes an unresolvable
  token, which is the correct end state.

### Org transfer

- **platform_user:** `users.id` persists; the event's `org_id` does not.
  Because org is recorded per event, an audit row keeps the org that was
  true when it was written, and a transfer cannot retroactively move
  history between tenants.
- **mailbox:** the ref is keyed over the org (§3), so a mailbox that moves
  gets a **different ref**. That is not a defect: refs are within-tenant
  correlators by construction, and a ref that followed a subject across
  tenants would be the cross-tenant join key §3 refuses to create.

### Key rotation (the fourth question, which the spec did not ask)

Rotating the HMAC key invalidates every existing ref. **Refs are
correlators, not permanent identifiers**, and rotation is an announced
operational event, not a routine one. There is no automatic rotation and no
era field: a stored era would let a reader correlate across rotations,
which defeats the rotation.

---

## 5. `actor_id` — a guard, not a migration

Zero email-shaped values today (§1), so there is nothing to migrate and a
migration would be change without a defect. What is missing is that the
property is *assumed*. It becomes **checked**, in two places:

- a unit detector over the audit writers, and
- a `reality_check` claim over the live table,

so a deployment whose IdP puts an email in `sub` fails visibly instead of
quietly writing operator identities into every row. If that fires, the
question reopens as a real one with evidence behind it.

**Built 2026-09-19** — see §6 stage 0.

---

## 6. Build sequence

| stage | what | needs |
|---|---|---|
| **0** | `actor_id` shape guard (§5) | **done** |
| **1** | `subject_identity.py`: `SubjectKind`, `SubjectRef`, two constructors | **done** |
| **2** | `subject` emitted on the four actions that carry one; registry rules; projector **v17** | **done** |
| **3** | `POST /api/directory/resolve` | **done** |
| 4 | Backfill `subject` onto historical rows | **operator decision** — rewrites 6,351 production rows |

**Stages 1–3 built 2026-09-19.** Notes where the build settled something
the design left implicit:

- **Two constructors named by SOURCE** (`subject_from_namespace`,
  `subject_from_user_id`), not one that sniffs the value. A `users.id` and
  an opaque namespace key are both just tokens; shape bounds damage, it
  does not establish provenance. The caller knows what it holds.
- **Four actions carry a subject, not two.** `user_created` and
  `user_updated` carry platform-user subjects, which is what gives stage 3
  anything to resolve — a directory endpoint whose only subjects were
  mailbox pseudonyms would resolve nothing on this deployment.
- **No key configured ⇒ no ref**, rather than an unkeyed hash. An unkeyed
  digest over a small address space is reversible by anyone who can guess
  an address, which is the disclosure the pseudonym exists to prevent. An
  absent ref is omitted from the detail rather than emitted as null.
- **A mailbox ref resolves to `not_resolvable_by_directory`.** Not a gap:
  the pseudonym is not reversible and §4 refused the mapping table that
  would reverse it. The address lives in the vault — grant-gated, not
  directory-gated — so this is the boundary between the two permissions
  doing its job, and it is the concrete meaning of "independent".
- **`DIRECTORY_ROLES` is its own constant**, and a test asserts the module
  imports no grant machinery at all. Checking by IMPORT rather than by
  substring, because a source scan trips on the comments explaining the
  separation — proxy-instead-of-property in miniature.

Stages 1–3 add a field and a surface; they change no stored identity and
need no migration, so §4's answers are sufficient to start them. Stage 4 is
the only one that rewrites history, and it is the one to hold.

---

## 7. Deliberately not done

- **No change to `user_id` itself.** It stays withheld and vaulted. Both
  actions carrying it are instance-bound, so the raw is grant-recoverable —
  unlike the governance actions of G-Trace-Chokepoint-Rest, withholding
  here is not destruction.
- **No mailbox-identity table.** §4's rename answer is the reason.
- **No erasure API.** §4 names where erasure would have to act; building it
  needs a request model and a legal-basis conversation that do not exist.
- **No `actor_id` migration.** §5.
