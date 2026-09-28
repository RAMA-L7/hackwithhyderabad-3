# Phase 1 Demo Scenario (single domain)

**Status:** Frozen demo script for the 29 Sept MVP. One domain only.
**Date:** 2026-09-27. **Revised twice on 2026-09-28** — once after the integration measurement, and
again after a **full rehearsal on a freshly seeded bank**, which showed the Act 1 and Act 3 queries
were not reproducible. Every number below was measured live against Hindsight Cloud. Nothing here is
what the script hoped for; it is what the code returned.
**Domain choice:** service/API runtime failures (connection resets, oversized payloads, idempotency
and retry bugs) — the same family as the M0 probe cases, so the seeds are already validated as
retrievable and distinguishable. Single domain for the MVP, not a second domain.

## Reproducibility rule — read before recording

**Seed a fresh bank and run the acts in order, once.** Retrieval is deterministic *within* a bank
(three identical calls returned identical scores), but the scores are query-relative, so **adding a
case changes them**. Rehearsing on the same bank you record on changes what the judge sees.

```bash
export HINDSIGHT_BANK_ID=debugagent-demo-$(date +%s)   # a NEW name per recording
PYTHONPATH=src python -c "from debugagent.config import load_memory_config; \
from debugagent.memory.hindsight_store import HindsightMemoryStore; \
from debugagent.seeds.loader import load_seed_cases; \
print(load_seed_cases(HindsightMemoryStore(load_memory_config())).to_dict())"
PYTHONPATH=src python -m debugagent.cli debug --env .env     # Act 1, then Act 2, then Act 3
```

Seeding the six cases took **24–38 s**. Running it twice inserts 0 and skips 6, so it is safe to repeat.

## Cast

- **Engineer** — the persona using the CLI.
- **Bank** — six seeded cases (`src/debugagent/seeds/cases.json`): `orders-api` ×2 (same symptoms,
  **different** root causes), `media-uploader`, `batch-runner`, `checkout-api`, `payments-api`.

## Act 1 — Without useful memory (the "before")

*Rehearsed 2026-09-28 on a fresh 6-case bank: abstained=True, class=irrelevant, 0 candidates,
top `semantic` 0.641 against a floor of 0.70 — a margin of 0.059.*

1. Engineer starts a session for a **new** issue with no matching history:
   *"billing-service writes duplicate invoices when the job is retried"* (`service=billing-service`).
   No seeded case is about billing, idempotency, or duplicate writes.
2. All six recalled cases are classified `irrelevant` and kept only in the trace.
3. **MEMORY** says *"No usable memory: no recalled case reached the usable floor"*.
4. **PROPOSAL** shows hypotheses labelled `generic · no memory used` with **empty**
   `supporting_case_ids` — visibly not memory-informed.
5. Engineer verifies against **EVIDENCE**, decides, resolves.
6. The case — *including the failed approach* — is retained; the fact id is shown.

> **This query was changed twice, and the reason matters.** The first text ("payments-service returns
> 502 above 2 MB") did not abstain at all: it recalled four candidates. The replacement ("checkout-web
> … missing auth cookie") abstained on one bank but **failed on the rehearsal bank** — its top
> `semantic` was 0.678 against the 0.70 floor, a margin of **0.022**, so small score variation flipped
> it. Six candidate queries were measured; this one has the widest margin (0.059).

## Act 2 — With useful memory (the "after")

*Rehearsed 2026-09-28: abstained=False, class=relevant, top 1.0872, 3 candidates.*

7. Engineer starts a second issue: *"media-uploader resets connections on uploads over 2 MB behind
   nginx"* (`service=media-uploader`, `runtime=node20`, `proxy=nginx-1.25`).
8. **MEMORY** shows the seeded `media-uploader` case as `relevant` with *"differs from now: nothing
   recorded differs"*, and the `orders-api` proxy case as a foreign-service reference with the
   mismatch stated explicitly: *"differs from now: runtime (then python3.10, now node20), service
   (then orders-api, now media-uploader)"*. The current environment is never overwritten by a past one.
9. **PROPOSAL** cites only ids that appear in MEMORY (rehearsal: H1 cited `2bf03cc80118b00a`; the
   other two cited nothing and were labelled `generic`), warns against approaches a past case records
   as failed, and lists a refutation condition for each.
10. **EVIDENCE** shows only current-case facts. Fields the engineer did not state stay **unknown** and
    are listed; they are never filled in from memory.
11. **DECISION** records accept/modify/reject, the engineer's note, and whether the past case was
    relevant.
12. Outcome retained; fact id shown, failed approach preserved.

## Act 3 — Disagreeing history

*Rehearsed 2026-09-28: abstained=True, class=contradictory, 2 candidates, margin 0.1449 < 0.2.*

13. *"orders-api large payloads are rejected by the proxy with 502"* (`service=orders-api`).
14. The bank holds two `orders-api` cases with the **same symptoms and different root causes**: an
    upstream proxy request-size limit, and an upstream dependency returning 502.
15. **MEMORY** shows **both**, each `contradictory` with `conflicts with` naming the other, and says
    *"recalled cases disagree on root cause with no clear leader (margin 0.1449 < 0.2)"*. The layer
    abstains rather than silently picking one.
16. **PROPOSAL** stays `generic` with no citations, and **nothing is retained** — the engineer is not
    asked to resolve an issue the history cannot inform.

> **This query was also changed.** "orders-api returns 502 for payloads above 2 MB" surfaced a third,
> unrelated `checkout-api` case as `relevant`, which outranked the pair and suppressed the
> abstention. The wording above keeps the conflict at the top and abstains deterministically.

## What the judge should see in 60–90 seconds

1. First issue: **abstention** with a visible reason, generic proposal, **no citations**.
2. Retention is visible (a real Hindsight write, fact id shown).
3. Second issue: relevant historical case recalled **with its original conditions**.
4. Similarities *and* differences are stated; a foreign-service case is shown with its environment
   mismatch, never merged into the current environment.
5. Proposal is reprioritised and cites memory; a previously failed approach is flagged.
6. Engineer verification is explicit and logged; the CLI never presents a proposal as verified.
7. Disagreeing history is surfaced as a conflict rather than resolved by the agent.

## Rehearsal record (2026-09-28, live Hindsight Cloud + real LLM)

| Step | Wall time | Result |
|---|---|---|
| Seed 6 cases into a fresh bank | 24–38 s | 6 inserted, 0 skipped, 0 rejected |
| Act 1 | 27.6 s | abstained; 3 generic hypotheses, 0 citations; retained `d86dff414ff0b105` |
| Act 2 | 29.7 s | 3 candidates; 1 valid citation; retained `e45829384ccf83fe` |
| Act 3 | 11.7 s | abstained (contradictory); 0 citations; nothing retained |
| **All three acts** | **~71 s** | every act exited 0 |

A single act is **~12–30 s**, dominated by one structured LLM call (3–7 s, up to ~14 s through
failover). All four trust sections rendered on every act on a Windows console.

**Provider behaviour:** Act 1 was served by the **fallback** (`baseten`) after two primary attempts
returned `INVALID_OUTPUT` — the CLI showed `fallback_used=True` and the log lines, which is the
designed resilience story. Acts 2 and 3 were served by the **primary** (`openrouter`), showing
`fallback_used=False`. Both paths were exercised in one rehearsal.

**Two extra checks, both passed:**

- *Insufficient evidence is not upgraded.* Re-running the Act 2 issue **without** stating the runtime
  produced `status=insufficient_evidence` with `mismatched_environment_fields=['runtime']` — even
  though the engineer pressed accept. The gap is visible and nothing is retained.
- *A repeated resolved case does not break recall.* Running Act 2 again in a new session wrote a
  third `media-uploader` case (idempotency is keyed on signature **and** session id, so a new session
  is legitimately a new case). All three share near-identical root causes and were shown together as
  `relevant` with **no false contradiction** — the `contradiction_text_overlap` fix holds.

## Hard rules for the demo

- No invented performance numbers, timings, or improvement claims — the claim is behavioural:
  *the second investigation is informed by the first*.
- **Do not narrate the thresholds as validated.** `min_final_score`, `semantic_floor`,
  `weak_reference_floor`, `contradiction_margin` and `contradiction_text_overlap` are **calibration
  parameters** — provisional defaults from a six-case bank, not measured truths. Say "relevance is
  classified app-side and the thresholds are configurable", never "the threshold is 0.05". The
  rehearsal above is exactly why: a 0.022 margin decided Act 1 on one bank and not another.
- No proprietary data, no secrets on screen.
- If the primary LLM fails during the recording, let the fallback serve it and keep the log line
  showing `fallback_used=True`.
- Seed a **fresh** bank for the recording (see the reproducibility rule).
- The recording must be reproducible from the README; keep a pre-recorded backup.
