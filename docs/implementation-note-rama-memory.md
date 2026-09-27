# Implementation note: Rama memory and Hindsight layer (M1)

Short note recording what was ambiguous while implementing, what was chosen, and why. Smallest
reversible option first. Written 2026-09-28 after live testing against Hindsight Cloud.

## Ambiguities and the decision taken

### A1. `root_cause_key` is derived text, not a controlled taxonomy

The contract has no field for a root-cause category, so `case_metadata()` derives
`root_cause_key` from the root-cause string (lowercased, first 64 chars).

Consequence: two cases that phrase the *same* root cause differently get different keys. So
contradiction detection is exact-text based and will **not** catch a paraphrase-level
disagreement. This is deliberately conservative in the wrong direction only for paraphrases; it
never invents a conflict that the text does not contain.

If a curated taxonomy is ever agreed, this is a one-line change at the `case_metadata()` seam
plus a re-retain. No call site changes.

### A2. Contradiction means "same service, different derived root cause"

A recalled case conflicts only when it shares a service with a recalled case whose
`root_cause_key` differs. Cases that agree are corroboration. An `unconfirmed` root cause never
conflicts, because "unknown" is not a competing explanation.

This is a smaller rule than "detect semantic disagreement", and it is the only one that can be
verified with six synthetic cases. It is a real limitation, not a solved problem.

### A3. Abstention on contradiction requires no clear leader

When conflicts exist, the layer abstains unless the best candidate beats the best conflicting
candidate by `contradiction_margin` (default 0.2). The margin is computed on whichever signal
`effective_score()` selected, so it is always like-for-like.

Both numbers are provisional. They are configuration, not findings.

### A4. `final` is not an absolute relevance measure (measured)

See "Measured backend behaviour" below. This is the one place where the implementation had to
deviate from the simplest reading of the M0 bands, and it is the reason `semantic_floor` exists.

## Measured backend behaviour (live, 2026-09-27)

Three behaviours were observed against Hindsight Cloud and are not in the M0 documentation. Each
one changed the implementation. Each is pinned by a test.

1. **`final` is query-relative and collapses as a bank fills.** On a near-empty bank a good match
   scored ~1.0. With 26+ near-identical cases retained, the same class of match scored
   `final` 0.003 while `semantic` stayed 0.78. An absolute floor on `final` alone therefore
   silently drops real matches as seed volume grows. `effective_score()` falls back to
   `semantic` (a raw 0-1 vector cosine) when `final` is below floor, and `semantic` is used only
   as an acceptance signal, never to order results.

   Separated bands observed: relevant `semantic` ~0.78-0.81, irrelevant ~0.52. `semantic_floor`
   default 0.70 sits between them and is provisional.

2. **Recall returns several rows per stored memory.** A bank of 6 retained cases returned 35
   recall rows with differing chunk text and differing scores. `dedupe_by_case()` keeps the
   best-scoring row per `case_key` so provenance stays one entry per case.

3. **Contradiction grouping must be keyed on root cause.** Grouping by service alone flagged
   every same-service pair as a conflict, so 32 cases that all agreed on one root cause abstained
   on a real match. Found only because the live bank was dirty. Grouping is now by
   (service, root_cause_key).

4. **The installed Python SDK has no memory-curation method at all.** `hindsight-client 0.10.1`
   exposes no `update_memory`; the adapter's `update()` / `invalidate()` raise `AttributeError`
   against a real client and pass only against the test fake. Note this is *not* a contradiction of
   M0: `docs/hindsight-capability-verification.md` verified curation (edit / invalidate / restore)
   at the **product** level via REST and UI. The capability exists; the Python binding for it does
   not. Not fixed here — that is Phase 2 work and would need the REST API or an SDK upgrade.

## Two things the thresholds do not do (recorded, not fixed)

- **`semantic_floor = 0.70` does not separate the M0 vague class.** Observed M0 `semantic`: relevant
  0.867 / 0.884 / 0.802, vague 0.766, irrelevant 0.584 / 0.505 / 0.475. A floor of 0.70 sits between
  irrelevant and *everything else*, so the vague case clears it and would be classed `relevant`.
  The vague → `partial` behaviour currently depends on `score_final`. Whether `semantic` can carry
  that boundary at all is an open calibration question.
- **Thresholds live in two places.** `min_final_score`, `weak_reference_floor`,
  `stale_after_days` and `max_tokens` are env-driven through `MemoryConfig`; `semantic_floor` and
  `contradiction_margin` are constructor-only on `AbstentionPolicy`. A consumer tuning by
  environment alone cannot reach the fallback or the margin. Worth unifying before calibration.

## Consequences for the frozen decisions

- **C2 is no longer "freeze a number".** M0 itself recorded that "absolute scores are documented as
  non-calibrated, so a fixed numeric threshold is unsafe", and M1 live testing confirmed it. The
  decision doc was updated on 2026-09-28: the **policy shape** is frozen, all values stay
  configurable, and calibration against the final Phase 1 seed bank is a tracked task.
- `update()` / `invalidate()` are non-functional on the installed SDK (finding 4) and are outside
  the MVP path, consistent with the contract deferring them to Phase 2. No claim is made that they
  work.

## What is deliberately absent

No LLM, normalizer, evidence, verification, resolution, or CLI code. `retain()` accepts an
already-approved `MemoryCase`; the upstream boundary that decides to retain is Mukul's call site
and is not implemented here.
