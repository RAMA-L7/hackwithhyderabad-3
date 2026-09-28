# Phase 0 → M1 Freeze Checklist (Decisions C1–C5)

**Status:** Proposals only. **None of these is decided.** Each requires explicit joint agreement by
Rama and Mukul in the 30-minute freeze session.
**Date:** 2026-09-27. **Revised:** 2026-09-28 (C2 rewritten after M1 live evidence — see below).
**Reference:** `docs/m1-contract.md` §5, `docs/phase1-team-interface.md` §5.

**Evidence labelling used throughout this document.** Three tiers are distinguished and must not
be blurred:

| Tier | Meaning | May be cited as |
|---|---|---|
| **Observed** | Reproducible measurement, recorded with its result file | fact |
| **Provisional default** | A value chosen by the implementer to make the system run | starting point, explicitly unvalidated |
| **Final calibrated** | Determined by a calibration pass against the final seed bank | not yet exists |

No **provisional default** in this repository is a validated production threshold.

---

## C1 — Ownership of `llm/` (LLM adapter + router)

| | |
|---|---|
| **Decision** | Who owns `debugagent/llm/` (adapter ABC, router, provider adapters, local validation) |
| **Current proposal** | **Mukul** — the split lists "LLM adapter/router, primary → fallback behavior" under Mukul; he is also the only consumer (hypothesis generation) |
| **Why it matters** | It is shared infrastructure. If Rama also touches it, the failover/auth rules can be changed from two places and silently diverge from the M0-verified behaviour |
| **Recommended default** | Mukul owns `llm/` exclusively; Rama consumes it only through the `LLMAdapter` Protocol and never imports provider modules |
| **If the alternative is chosen** | If Rama owns `llm/`, Mukul must code hypothesis generation against the `LLMAdapter` Protocol stub **before** Rama implements it, and the M0 behaviours (failover on 429/5xx/timeout only; auth never fails over; local validation fail-closed) become a contract test owned by Rama. Slower start, extra interface-contract cost |

## C2 — Abstention policy shape (REVISED 2026-09-28: do NOT freeze a number)

**Status change:** C2 was originally "the initial `min_final_score` value". M1 implementation and
live testing showed a single numeric threshold cannot be frozen, because the score it would apply
to is not a calibrated quantity. **C2 is now a decision about the policy *shape*.** The numbers are
explicitly deferred.

| | |
|---|---|
| **Decision** | The *shape* of the abstention policy: which signals participate, in what order, and what each one is allowed to decide |
| **Current proposal** | Freeze the shape below. Do **not** freeze any threshold value in this session |
| **Why it matters** | The demo hinges on abstaining on irrelevance and recalling on relevance. A shape that is wrong cannot be tuned later; a number that is wrong can. Freezing a number now would freeze a value derived from a bank that does not yet exist |
| **Recommended default** | Adopt §C2.1 as written; record the numbers in the "provisional" column of §C2.2 and revisit after the seed bank is final |
| **If the alternative is chosen** | Freezing a number now is not harmful *if* it is labelled provisional and re-calibration is a checklist item — but it invites later readers to treat 0.05 or 0.70 as a validated constant, which the evidence does not support |

### C2.1 What is frozen (the policy shape)

These are structural and are not expected to change:

1. **Abstention is computed application-side**, in `classify_candidates`, from returned scores.
   Never delegated to the provider's `min_scores` (documented but never exercised in M0).
2. **Two signals participate, with strictly different permissions:**
   - `score_final` — decides *ordering* and is the primary acceptance signal.
   - `score_semantic` — **acceptance only, never ordering.** It is consulted *only* when
     `score_final` is below the floor, and only to avoid discarding a real match.
3. **The semantic fallback is mandatory, not optional.** Without it, a matching case is silently
   dropped whenever the bank grows (measured: `final` 0.003 while `semantic` 0.78).
4. **A floor is not a relevance claim.** Clearing the floor yields a *candidate*; it never yields
   a verified fact. Recall output is reference material only.
5. **Contradictions are surfaced, never silently resolved.** When recalled cases disagree and no
   candidate leads by the configured margin, the layer abstains and says why.
6. **Agreement is not conflict.** Cases that share a root cause are corroboration.
7. **Every abstention is explained.** `AbstentionDecision` always carries `top_score`,
   `threshold_used`, and a human-readable `reason`.
8. **All thresholds are configuration.** No threshold is a code constant, and every threshold is
   re-calibratable without touching classification logic.

### C2.2 What is NOT frozen (values pending calibration)

| Threshold | Provisional default | Where it lives | Status |
|---|---|---|---|
| `min_final_score` | `0.05` | `DEBUGAGENT_MIN_FINAL_SCORE` (env) | **Provisional.** Derived from M0 bands on a 5-case bank |
| `semantic_floor` | `0.75` | `DEBUGAGENT_SEMANTIC_FLOOR` (env) | **Provisional.** Raised from `0.70` on 2026-09-28 from measured rehearsal scores (unrelated 0.7016/0.7084 rejected, genuine 0.78 kept). A calibration parameter, not a validated constant |
| `weak_reference_floor` | `0.6` | `DEBUGAGENT_WEAK_REFERENCE_FLOOR` (env) | **Provisional** |
| `contradiction_margin` | `0.2` | `AbstentionPolicy(...)` only — *not* env-driven | **Provisional.** No margin experiment has been run |
| `stale_after_days` | `365` | `DEBUGAGENT_STALE_AFTER_DAYS` (env) | **Provisional policy choice, not a measurement** |
| `max_tokens` | `1024` | `HINDSIGHT_RECALL_MAX_TOKENS` (env) | **Provisional** |

**These values must not be presented as universal truths, in code comments, in the CLI, or in the
demo narration.** They are starting points for a six-case bank.

### C2.3 Calibration must happen against the final Phase 1 seed bank

Calibration is a scheduled task, not a decision. It should record, for each labelled query
(relevant / vague / irrelevant), the observed `final` and `semantic` scores, and set thresholds to
separate the classes on the final seed bank.

**Known gap, still open:** the current `semantic_floor = 0.75` separates *irrelevant* from
*not-irrelevant*, but it still does **not** reproduce the M0 vague/relevant split.
Observed M0 `semantic` scores:

| Class | M0 observed `semantic` |
|---|---|
| relevant (H-4A / H-4B / H-4D) | 0.867 / 0.884 / 0.802 |
| vague (H-4E) | 0.766 |
| irrelevant (H-4C and others) | 0.584 / 0.505 / 0.475 |

A floor of 0.75 still admits the M0 vague case (0.766) as `relevant`. The vague → `partial`
behaviour therefore currently depends on `score_final`, not on the semantic fallback. Whether
`semantic` can carry the vague/relevant boundary at all is an open calibration question; if it
cannot, the vague class needs a different signal, and that is a Phase 1 risk to record rather than
paper over.

### C2.4 Evidence behind the revision

| Source | Observation | Consequence |
|---|---|---|
| M0 H-4E-ASSERT | vague top `final` 0.352 < min relevant 0.971 | A relative floor is achievable |
| M0 H-4E-ASSERT (own note) | "absolute scores are documented as non-calibrated, so a fixed numeric threshold is unsafe" | M0 already warned against a fixed number |
| M1 live, 26+ near-identical cases | matching case: `final` 0.003, `semantic` 0.78 | One absolute `final` threshold drops real matches as the bank grows |
| M1 live, seed bank | 6 stored cases → 35 recall rows | Recall volume and duplication are app-side concerns |
| M1 live, 32 agreeing cases | grouping by service alone abstained on a real match | Contradiction detection must key on root cause |


## C3 — Initial seed domain family

| | |
|---|---|
| **Decision** | Which single engineering domain the seed cases and the demo cover |
| **Current proposal** | Service/API runtime failures (connection resets, oversized payloads, upstream errors) — the M0 probe family |
| **Why it matters** | Rama authors seeds; Mukul's normalizer and CLI must speak the same vocabulary. Two vocabularies = silent recall misses on demo day |
| **Recommended default** | Reuse the M0 probe family — it is already proven retrievable, distinguishable, and abstention-testable against a real bank |
| **If the alternative is chosen** | Another domain is permissible (e.g. hardware timing, batch/data pipelines) but costs: new taxonomy, new seed authoring, a fresh abstention calibration, and loss of the M0 evidence. It also risks becoming "a second domain", which is out of Phase 1 scope |

## C4 — CLI command names and section labels

| | |
|---|---|
| **Decision** | Command verbs and the exact rendering of the four trust sections |
| **Current proposal** | Commands `debug`, `verify`, `resolve`, `inspect`; sections labelled **MEMORY**, **EVIDENCE**, **PROPOSAL**, **DECISION** |
| **Why it matters** | The four-section separation *is* the demo's core trust surface. Labels are the contract between the architecture and what the judge sees |
| **Recommended default** | Keep the four labels exactly as above (already referenced by the demo script and DoD); command names are free to change since no integration depends on them |
| **If the alternative is chosen** | Any relabelling must be applied to `docs/phase1-demo-scenario.md` and the DoD in the same commit, or the docs and the demo drift apart |

## C5 — `observed_evidence` representation

| | |
|---|---|
| **Decision** | Whether the memory field stores URI/reference only, or short text plus URI |
| **Current proposal** | References/URIs only (no raw report or log blobs) |
| **Why it matters** | Affects seed size, render width in the CLI, and the "no secrets on screen" rule. Blobs would also bloat the bank and slow recall |
| **Recommended default** | URI/reference only; a short human label is acceptable **if** it carries no sensitive content (e.g. `prime-time-report://2026-09-27/run-114`) |
| **If the alternative is chosen** | Storing text snippets is convenient for the demo but introduces a secrets/PII surface and larger `max_tokens` consumption; it must then be explicitly sanitised and length-capped |

---

## Additional item raised by this review (not previously numbered)

### C6 — `min_scores` dependency (technical, low-controversy)

`min_scores` is documented by both providers but was **not exercised in M0**. The contract now
requires abstention to be computed in `classify_candidates` from returned scores, with `min_scores`
treated as unverified. **Recommendation: no decision needed — adopt the contract as written.** Raise it
only if someone wants provider-side filtering as well.

### C7 — Memory Defense verification (technical, low-controversy)

M0 confirmed the configuration call returns without error but did **not** verify that redaction is
active. **Recommendation:** do not claim it protects secrets; rely on the "no secrets in seed data"
rule, and verify redaction only if time allows after the MVP.
