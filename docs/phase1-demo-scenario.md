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

### A fresh bank also needs a fresh ledger directory

`data/memory_ledger.json` records which cases have already been retained, under an idempotency key of
`sha256(problem_signature | session_id)` — **that key does not include the bank id.** So a new
`HINDSIGHT_BANK_ID` combined with the default data directory is a trap: all six seeds get reported as
*"skipped: case already retained"* and the new bank is left **empty**, which breaks all three acts.
Always pair the two. Validated pattern (PowerShell):

```powershell
$env:HINDSIGHT_BANK_ID = "demo-" + (Get-Date -Format "yyyyMMdd-HHmmss")
$env:DEBUGAGENT_DATA_DIR = "$env:TEMP\demo-ledger-$($env:HINDSIGHT_BANK_ID)"
```

bash equivalent: `export HINDSIGHT_BANK_ID=demo-$(date +%s)` together with
`export DEBUGAGENT_DATA_DIR="$TMPDIR/demo-ledger-$HINDSIGHT_BANK_ID"`

### How to confirm the bank really is fresh

1. **Before seeding**, the new bank should have no memories — `list_memories` returns a 404.
2. **Seeding must report `inserted: 6, skipped: 0, rejected: 0`.** If it reports `skipped: 6`, a
   previous ledger directory is being reused; fix that before going further.
3. **Do not use the memory-unit count as the freshness criterion.** It is asynchronous: one freshly
   seeded bank was observed reporting 17 units and then 25 minutes later, with no further writes. The
   seed report is the reliable signal.

### Then seed and run the acts

```bash
PYTHONPATH=src python -c "from debugagent.config import load_memory_config; \
from debugagent.memory.hindsight_store import HindsightMemoryStore; \
from debugagent.seeds.loader import load_seed_cases; \
store = HindsightMemoryStore(load_memory_config()); \
print(load_seed_cases(store).to_dict()); store.close()"
PYTHONPATH=src python -m debugagent.cli debug --env .env     # Act 1, then Act 2, then Act 3
```

The `store.close()` matters: the Hindsight client holds an aiohttp session, and without an explicit
close the process prints *"Unclosed client session"* and *"Unclosed connector"* on exit. The CLI
closes its own port automatically; this one-liner has to do it by hand.

Seeding the six cases took **24–38 s**. Running it twice against the *same* ledger inserts 0 and
skips 6, so it is safe to repeat.

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
   nginx"* (`service=media-uploader`, `runtime=node20`, `proxy=nginx-1.25`). At the facts prompt, add
   `region=us-east-1`: seeds now carry `region`, so leaving it unstated makes it an evidence gap and the
   memory-backed hypothesis becomes `insufficient_evidence` (correct behaviour, but not this beat).
8. **MEMORY** shows the seeded `media-uploader` case as `relevant` with *"differs from now: nothing
   recorded differs"*, and the `orders-api` proxy case as a foreign-service reference with the
   mismatch stated explicitly: *"differs from now: runtime (then python3.10, now node20), service
   (then orders-api, now media-uploader)"*. The current environment is never overwritten by a past one.
9. **MEMORY** shows the seed's full original environment (`proxy=nginx-1.25, region=us-east-1`) and ends
   the past case with *"Failed before: raised the uploader client timeout (why it failed: …)"*. **PROPOSAL**
   cites only ids that appear in MEMORY (dry run on `mukul/phase1-fixes`, fresh bank: H1 cited
   `2bf03cc80118b00a` and proposed the recorded fix, raising `client_max_body_size`, first), does not
   recommend the approach MEMORY marks as failed, and lists a refutation condition for each.
   *Before the fixes on `mukul/phase1-fixes`, recall kept only the symptom fact, so neither the fix nor
   the failed approach reached the prompt; the model once recommended raising timeouts.*
10. **EVIDENCE** shows only current-case facts. Fields the engineer did not state stay **unknown** and
    are listed; they are never filled in from memory.
11. **DECISION** records accept/modify/reject, the engineer's note, and whether the past case was
    relevant.
12. Outcome retained; fact id shown, failed approach preserved.

## Act 3 — Disagreeing history

*Rehearsed 2026-09-28 (Rama): abstained=True, class=contradictory, 2 candidates, margin 0.1449 < 0.2.*
*Dry runs on fresh banks (Mukul, `mukul/phase1-fixes`, twice): class=contradictory, the same 2 candidates,
**abstained=False** (one side led by the margin), both hypotheses citing them labelled `conditional`.*

13. *"orders-api large payloads are rejected by the proxy with 502"* (`service=orders-api`).
14. The bank holds two `orders-api` cases with the **same symptoms and different root causes**: an
    upstream proxy request-size limit, and an upstream dependency returning 502.
15. **MEMORY** shows **both**, each `contradictory` with `conflicts with` naming the other. Whether the
    layer also abstains depends on the score margin, which varies between fresh banks (Hindsight's fact
    extraction is LLM-based). **Narrate the stable behaviour, not the abstention:** *"conflicting history
    is surfaced side by side, never resolved by the agent."*
16. **PROPOSAL** either stays `generic` (abstained) or labels every hypothesis that cites one side
    `memory-backed · past cases disagree` (`conditional`). Answer **n** to "Did you resolve the issue?"
    so **nothing is retained**; the CLI does ask.

> **This query was also changed.** "orders-api returns 502 for payloads above 2 MB" surfaced a third,
> unrelated `checkout-api` case as `relevant`, which outranked the pair. The wording above keeps the
> conflict at the top, so the stable behaviour is that **conflicting history is surfaced side by side;
> abstention depends on the score margin** and is not guaranteed.

## What the judge should see in 60–90 seconds

1. First issue: **abstention** with a visible reason, generic proposal, **no citations**.
2. Retention is visible (a real Hindsight write, fact id shown).
3. Second issue: relevant historical case recalled **with its original conditions**.
4. Similarities *and* differences are stated; a foreign-service case is shown with its environment
   mismatch, never merged into the current environment.
5. Proposal is reprioritised and cites memory; MEMORY shows the approach that failed before, and the
   proposal does not repeat it.
6. Engineer verification is explicit and logged; the CLI never presents a proposal as verified.
7. Disagreeing history is surfaced side by side as a conflict, never resolved by the agent.

## Rehearsal record (2026-09-28, live Hindsight Cloud + real LLM)

> **Historical / pre-fix evidence — retained for comparison, not authoritative.** These numbers were
> measured at `integration/phase1` @ `1f29c30`, **before** the `mukul/phase1-fixes` changes merged at
> `a36bad3` (fact-joining `dedupe_by_case`, failed-approaches and `proxy`/`region` metadata, the
> `"unknown"`-dropping seam, and the fallback retry) and before `semantic_floor` moved to 0.75. The fixes
> changed the prompt, the router and the recall seam, so these figures no longer describe current
> behaviour. The **authoritative** post-fix rehearsal is the two-fresh-bank table further below.

| Step | Wall time | Result |
|---|---|---|
| Seed 6 cases into a fresh bank | 24–38 s | 6 inserted, 0 skipped, 0 rejected |
| Act 1 | 27.6 s | abstained; 3 generic hypotheses, 0 citations; retained `d86dff414ff0b105` |
| Act 2 | 29.7 s | 3 candidates; 1 valid citation; retained `e45829384ccf83fe` |
| Act 3 | 11.7 s | abstained (contradictory); 0 citations; nothing retained — **pre-fix figure, see the authoritative table below** |
| **All three acts** | **~71 s** | every act exited 0 |

A single act is **~12–30 s**, dominated by one structured LLM call (3–7 s, up to ~14 s through
failover). All four trust sections rendered on every act on a Windows console.

**Final rehearsals on `mukul/phase1-fixes` @ `5373b3e`** (Mukul, macOS, Baseten primary, `semantic_floor` 0.75,
two separate fresh banks, Acts 1 → 2 → 3 → 4, each checked against this script):

| Bank | Seed | Act 1 | Act 2 | Act 3 | Act 4 (Act 1 recurs) |
|---|---|---|---|---|---|
| `demo-rehearsal-1790617985` | 6 / 0 / 0 | abstained, 3 generic, retained | seed relevant; full env + *Failed before*; H1 cites it and leads with the fix; retained | conflicting `orders-api` history surfaced; nothing retained — see the note below on how many sides appear as candidates | Act 1's live case recalled and cited |
| `demo-rehearsal-1790618070` | 6 / 0 / 0 | same | same | same | same, served by the **fallback** (`fallback_used=True`) |

> **How many conflicting sides appear as candidates was not recorded separately, and it varies.**
> These two rehearsals are logged as "only the two orders-api cases, contradictory". A later
> fresh-bank measurement on this branch (`semantic_floor` 0.75) observed **one** `orders-api` case
> accepted as a `contradictory` candidate while the other was **excluded as `irrelevant`**, because its
> `final` score had collapsed and its `semantic` score fell below the 0.75 fallback floor. Whether both
> sides surface therefore depends on the scores, not only on the bank contents. The stable behaviour to
> narrate is unchanged: **conflicting history is surfaced; abstention depends on the score margin.**

The earlier rehearsal on `a36bad3` (floor 0.70) failed Act 1 and Act 3 on semantic-fallback false positives;
see `docs/decision-log.md`.

> **Act 3's `abstained` value is not recorded for these two post-fix runs.** The table above records that
> Act 3 surfaced conflicting `orders-api` history and retained nothing — but it does not state whether
> the layer also abstained. No repository evidence establishes that value, so it is
> left unstated rather than guessed. Act 3's narrative above is written accordingly: **the guaranteed
> behaviour is that conflicting history is surfaced side by side; abstention depends on the score margin.**
> If you need the exact value for the recording, read it from the session trace
> (`.debugagent/last-session.json` → `memory.abstention.abstained`) during a fresh-bank run.

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
- `semantic_floor` is **0.75** on `mukul/phase1-fixes` (was 0.70; `DEBUGAGENT_SEMANTIC_FLOOR` overrides it).
  The final fresh-bank rehearsal on 2026-09-28 admitted `batch-runner` into Act 1 (semantic 0.7084) and
  `media-uploader` into Act 3 (0.7016) at 0.70. Evidence and reasoning: `docs/decision-log.md`.
- No proprietary data, no secrets on screen.
- If the primary LLM fails during the recording, let the fallback serve it and keep the log line
  showing `fallback_used=True`.
- Seed a **fresh** bank for the recording (see the reproducibility rule).
- The recording must be reproducible from the README; keep a pre-recorded backup.
