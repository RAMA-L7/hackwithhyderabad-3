# Phase 1 Demo Scenario (single domain)

**Status:** Frozen demo script for the 29 Sept MVP. One domain only.
**Date:** 2026-09-27. **Revised:** 2026-09-28 — the Act 1 and Act 2 queries below were **measured
live** against Hindsight Cloud (bank seeded with the six synthetic cases) and replaced because the
original Act 1 did not abstain. Every classification shown is what the code actually returned, not
what the script hoped for.
**Domain choice:** service/API runtime failures (connection resets, oversized payloads, auth-handling
errors) — the same family as the M0 probe cases, so the seeds are already validated as retrievable
and distinguishable. This is a single domain for the MVP, not a second domain; nothing here implies
multi-domain support.

## Cast

- **Engineer** — the persona using the CLI.
- **Bank** — pre-seeded historical cases (the six synthetic cases in
  `src/debugagent/seeds/cases.json`): `orders-api` ×2 (same symptoms, **different** root causes),
  `media-uploader`, `batch-runner`, `checkout-api`, `payments-api`.

## Act 1 — Without useful memory (the "before")

*Measured 2026-09-28: abstained=True, relevance_class=irrelevant, all six cases excluded.*

1. Engineer starts a debug session for a **new** issue with no matching history:
   *"checkout-web returns 500 only for requests with a missing auth cookie; other requests are fine."*
   (`checkout-web` is deliberately neither `checkout-api` nor any seeded service, and the failure is
   not a request-size problem, so nothing in the bank resembles it.)
2. Agent recalls six cases; every one is classified `irrelevant` and kept only in the trace.
3. **MEMORY** says *"No usable memory: no recalled case reached the usable floor"*.
4. **PROPOSAL** shows hypotheses with `relevance_state: generic` and **empty**
   `supporting_case_ids` — visibly not memory-informed.
5. Engineer verifies against **EVIDENCE** (facts supplied now), decides, and resolves.
6. The case — *including the failed approach tried* — is retained, and the fact id is shown.

> **Why the query changed.** The earlier Act 1 text ("payments-service returns 502 only for payloads
> above 2 MB") was measured on 2026-09-28 and did **not** abstain: it recalled four candidates
> (`payments-api` and `checkout-api` as `relevant`, the two `orders-api` cases as `contradictory`).
> Mukul flagged this conflict before the merge. The replacement query was measured and abstains.

## Act 2 — With useful memory (the "after")

*Measured 2026-09-28: abstained=False, class=relevant, top score 1.030.*

7. Engineer starts a second issue:
   *"media-uploader resets connections on uploads over 2 MB behind nginx."*
8. **MEMORY** shows the recalled `media-uploader` cases with their **original environment**
   (`service=media-uploader`, `runtime=node20`) and the `orders-api` proxy case as a weaker
   reference, with the difference stated explicitly:
   *"differs from now: runtime (then python3.10, now node20), service (then orders-api, now
   media-uploader)"*. The engineer's current service is never overwritten by a past one.
9. **PROPOSAL** cites only ids that appear in MEMORY, warns against approaches a past case records
   as failed, and lists a refutation condition for each hypothesis.
10. **EVIDENCE** shows only current-case facts. Fields the engineer did not state stay **unknown**
    and are listed, rather than being filled in from memory.
11. **DECISION** records accept/modify/reject + engineer note, and whether the past case was relevant.
12. Outcome retained; the engineer sees the retention acknowledgement (fact id) and the failed
    approach preserved.

> **Re-running Act 2 is safe.** Resolving an issue that matches a seed stores a *paraphrase* of that
> seed's root cause. The first version of the classifier treated the paraphrase as a second,
> disagreeing root cause and abstained on a correct match. That was measured on 2026-09-28 and
> fixed; `contradiction_text_overlap` now treats near-identical root causes as the same cause.

## Act 3 — Disagreeing history (only if time allows)

*Measured 2026-09-28: abstained=True, relevance_class=contradictory, margin 0.0015 < 0.2.*

13. Engineer asks about the genuinely ambiguous case: *"orders-api returns 502 for payloads above 2 MB."*
14. The bank holds two `orders-api` cases with the **same symptoms and different root causes**:
    an upstream proxy request-size limit, and an upstream dependency returning 502.
15. **MEMORY** shows **both**, each labelled `contradictory` with `conflicts with` naming the other.
    The layer abstains rather than silently picking one, and says why.
16. **PROPOSAL** stays `generic` — a disagreement in history is not a reason to lean on either side.

## What the judge should see in 60–90 seconds

1. First issue: **abstention** with a visible reason, generic proposal, **no citations**.
2. Retention is visible (a real Hindsight write, fact id shown).
3. Second issue: relevant historical case recalled **with its original conditions**.
4. Similarities *and* differences are stated; a foreign-service case is shown as a weak reference,
   never merged into the current environment.
5. Proposal is reprioritised and cites memory; a previously failed approach is flagged.
6. Engineer verification is an explicit, logged step; the CLI never presents a proposal as verified.
7. If time allows: disagreeing history is surfaced as a conflict rather than resolved by the agent.

## Demo data

Six synthetic cases in `src/debugagent/seeds/cases.json`, each with at least one failed approach (or
an unconfirmed root cause), an outcome class, and verification notes. `orders-api` deliberately
carries two cases with the same symptoms and different root causes, so the contradiction behaviour
has something real to show. Load them with `load_seed_cases(store)`; it is idempotent.

## Hard rules for the demo

- No invented performance numbers, timings, or improvement claims — the claim is behavioural:
  *the second investigation is informed by the first*.
- **Do not narrate the thresholds as validated.** `min_final_score`, `semantic_floor`,
  `weak_reference_floor` and `contradiction_margin` are provisional defaults from a six-case bank and
  are still uncalibrated. Say "relevance is classified app-side and the thresholds are configurable",
  not "the threshold is 0.05".
- No proprietary data, no secrets on screen.
- If the primary LLM fails during the recording, let the fallback serve it and keep the log line
  showing `fallback_used=True` — a visible resilience story, not a hidden one. (Observed on
  2026-09-28: the primary served the run; an earlier run fell back to the fallback provider. Both are
  correct behaviour and both are shown.)
- The recording must be reproducible from the README; keep a pre-recorded backup.
- Run the rehearsal against the **seeded** bank, not an empty one. All three behavioural defects
  found during M1 appeared only once the bank held more than one case.
