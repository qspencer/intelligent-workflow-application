# G29 review — reviewer's closure

**Received:** 2026-10-29, in reply to `docs/TRACE_G29_REVIEW_RESPONSE.md`.
**Recorded verbatim.** Dispositions of each qualification are in
`docs/REVIEW_FINDINGS_LEDGER.md` (G29) and `docs/NEXT_STEPS.md` (G29, G30).

---

This is a strong and appropriately candid response. I would treat G29 as **closed by enforced withdrawal, not approved for use**.

The disposition and reopening conditions accurately reflect the review. The corrected channel calculation is also right: allowing sequences of length 0–32 over three values yields about **51.3 bits**, not 53.

A few qualifications remain:

- The claimed fixes in `111542f`, `7a06183`, and `62353e8` are plausible but not independently verified by this response alone. The next source package should confirm that every persistence path—including clone, import, scaffold, update, and repository loading—actually revalidates the definition.
- Changing the fixed-point test to re-project without a vocabulary is correct for the withdrawn state. If G29 is reopened, the enduring property should instead be: projection is reproducible from immutable, row-bound, integrity-protected inputs. Simply passing the current definition to every consumer would not satisfy that.
- The scaffold finding is handled correctly for G29: the approval boundary can remain open because non-null `released_fields` is now rejected. It must be resolved before reopening.
- The G30 discoveries are substantial and justify a separate review. In particular, the redirect residual is not fully answered by saying redirected content never reaches the agent. A request to a disallowed destination can still disclose request metadata or cause an external side effect. The control’s contract should specify whether it governs requests or only returned content; if it governs destinations, enforcement must occur before dispatch.
- Phase 2 is a real completeness boundary. Overall capability enforcement should not be claimed while deterministic-function file operations remain outside the capability system.

The errata section improves the record, especially the admission that the prior evidence capture truncated audit output. I would keep the original archive immutable and cross-reference this response from the G29 ledger.

Overall: the response satisfactorily closes G29’s immediate review loop, while correctly leaving the proposed feature and the broader capability work unapproved pending their respective prerequisites.
