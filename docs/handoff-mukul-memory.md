# Handoff: Rama's Memory Layer → Mukul

**From:** Rama · **To:** Mukul · **Date:** 2026-09-28
**Code:** `integration/phase1` (Rama's memory layer + Mukul's pipeline, merged; MK9 complete)
**Read alongside:** `docs/m1-contract.md` (§4 rules, §5 limitations), `docs/m1-freeze-decisions.md` (§C2),
`docs/handoff-mukul-memory.md` is normative for *your* consumption of memory.

> **Status note (2026-09-28, MK9).** This handoff has been actioned. `pipeline/memory_adapter.py`
> implements `HindsightMemoryPort` against your `MemoryPort` seam, `--memory hindsight` is the CLI
> default, and the full loop runs end to end against Hindsight Cloud. Your Q1, Q3, Q4, Q5, Q6, Q7 and
> Q8 proposals were all accepted as written, with no change to your code — see `docs/decision-log.md`
> for the answers and for three defects that only appeared once the two sides were joined. The
> interface below is unchanged, so nothing in this document needs revising.

---

## 0. The one rule that outranks everything else

```
MEMORY informs.
EVIDENCE verifies.
AGENT proposes.
ENGINEER decides.
```

A recalled memory is a **claim made in the past**, not a fact about the present system. Your agent
may cite it, display it, and use it to form a hypothesis. It may **never**:

- satisfy a verification precondition,
- upgrade a status to `supported`,
- stand in for a missing `Evidence` item,
- or be rendered in a way that implies it is current.

Missing evidence yields `insufficient_evidence` **regardless of how strong the memory match is.**
A perfect memory match with zero current evidence is still unverified.

## 1. What Rama's memory layer provides

| Capability | Status |
|---|---|
| Frozen `MemoryCase` schema (8 essential fields) + fail-closed validation | Done, tested |
| `MemoryStore` Protocol, provider-agnostic | Done, tested |
| Hindsight Cloud adapter (retain / recall) | Done, **verified live** |
| Application-level idempotency ledger | Done, **verified live** |
| Recall deduplication to one row per case | Done, **verified live** |
| Deterministic relevance classification | Done, tested |
| Abstention with an explained reason | Done, tested + live |
| Contradiction detection and surfacing | Done, tested + live |
| Six synthetic seed cases + deterministic loader | Done, **loader verified live** |
| `update()` / `invalidate()` | **Non-functional** — see §7 |

What it deliberately does **not** do: no relevance judgement on your behalf, no hypothesis
generation, no verification, no decision, no auto-retention.

## 2. The exact interface you consume

```python
from debugagent.memory import (
    MemoryStore,          # Protocol
    MemoryError,          # base
    MemoryUnavailable,    # backend unreachable
    MemoryAuthError,      # credentials rejected
    MemorySchemaError,    # invalid input; nothing was stored
    classify_candidates,  # pure function: (query, RecallSet, policy) -> (MatchReport, AbstentionDecision)
    AbstentionPolicy,
)
```

Construction is Rama's; you receive the object:

```python
from debugagent.config import load_memory_config
from debugagent.memory.hindsight_store import HindsightMemoryStore

store = HindsightMemoryStore(load_memory_config())   # reads env; never prints secrets
```

Protocol, verbatim (`src/debugagent/memory/store.py`):

```python
def recall(self, *, query: str, tags: list[str] | None = None,
           types: list[str] | None = None, max_tokens: int | None = None) -> RecallSet: ...
def retain(self, case: MemoryCase) -> RetentionDecision: ...
def update(self, case_id: str, *, text: str, reason: str) -> None: ...      # see §7
def invalidate(self, case_id: str, *, reason: str) -> None: ...             # see §7
```

- `recall` **never** decides relevance. It returns what the backend returned, deduplicated.
- `retain` is idempotent on `sha256(problem_signature | session_id)[:16]`. It **never** decides
  engineering outcomes — it only validates and stores.
- Program against the `MemoryStore` Protocol, not `HindsightMemoryStore`. Do not import
  `hindsight_client` anywhere in your code.

Classification is a **pure function** — no I/O, no LLM, safe to call in tests with a stub store:

```python
report, decision = classify_candidates(query_text, recall_set, AbstentionPolicy())
```

## 3. Structure of a recall result

```python
RecallResult(
    case_id: str,             # application case_key = sha256(signature|session)[:16]
    text: str,                # rendered stored case
    score_final: float,       # provider score — NOT calibrated, see §7
    score_semantic: float|None,
    score_keyword: float|None,
    environment: dict[str,str],   # {"service": ..., "runtime": ...} as stored
    outcome: str|None,
    root_cause_key: str|None,     # derived text, see §6
    mentioned_at: str|None,       # Hindsight-native timestamp, not an app field
)

RecallSet(items=[...], recalled_at=str, bank_id=str)
RecallSet.provenance() -> list[str]   # case ids, one per item
```

Then classification adds:

```python
MatchReport(candidates=[MatchCandidate], excluded=[MatchCandidate],
            top_score=float, threshold_used=float)
MatchCandidate(case_id, relevance_class, score_final, environment, reason, conflicts_with)
AbstentionDecision(abstained, relevance_class, top_score, threshold_used, reason)
```

`relevance_class` ∈ `relevant | partial | irrelevant | contradictory | stale`.
`candidates` are usable; `excluded` are kept for the trace log (never silently dropped).

## 4. How to read relevance and abstention

`decision.abstained` is your signal to proceed **without** memory:

- `abstained=True, relevance_class="irrelevant"` → nothing usable. Hypotheses must be
  `relevance_state: generic` with empty `supporting_case_ids`.
- `abstained=True, relevance_class="contradictory"` → recalled cases disagree with no clear
  leader. Do not pick one. Either investigate the disagreement or go generic.
- `abstained=False, relevance_class="relevant"` → present these as *prior experience*.
- `abstained=False, relevance_class="partial"` → weak reference; label it as such, do not dress it
  up as support.

Two rules that are easy to get wrong:

1. **Clearing the floor is not verification.** A `relevant` class means "worth reading", nothing more.
2. **`partial` is not abstention.** A weak match is presented cautiously, not discarded. Only
   `abstained=True` means "no usable memory".

`decision.reason` is written for a human and is safe to show in the CLI.

## 5. How provenance is preserved

- Every recalled item carries `case_id`. Cite **only** these ids in `Hypothesis.supporting_case_ids`.
- `MatchReport.provenance()` returns exactly the candidate ids — your citation set.
- `MatchCandidate.environment` is the **originally stored** environment, not a rewritten one. Show
  it verbatim in the MEMORY section so the engineer can spot a mismatch; never silently merge it
  into the current case.
- Excluded candidates stay in `MatchReport.excluded` for the trace log.
- Citation integrity is a shared contract test (T3): hypothesis ids ⊆ `MatchReport` ids.

## 6. How contradictions are surfaced

A conflict is: **same service, different `root_cause_key`**, among recalled cases that overlap the
query. Both sides are marked `relevance_class="contradictory"` and each carries
`conflicts_with` naming the disagreeing case ids. `decision.reason` states the margin.

- Cases that **agree** are corroboration, not conflict.
- An `unconfirmed` root cause never conflicts.
- If the best candidate leads the best conflicting candidate by `contradiction_margin`, the layer
  does **not** abstain — but the conflict is still surfaced. Show it; let the engineer judge.

You must not resolve a contradiction by choosing the higher-scoring side. Surface both, with their
distinguishing conditions.

## 7. What you must NOT assume about Hindsight scores

This is the section most likely to cause a wrong demo if skipped.

1. **`score_final` is not calibrated and drifts with bank size.** It is a *query-relative* signal.
   On a small bank a good match scored ~1.0. With 26+ near-identical cases the same class of match
   scored **0.003** while `semantic` stayed **0.78**. Never treat a `final` score as an absolute
   quality measure, and never show it to the engineer as one.
2. **Do not compare a `final` score across queries.** 0.8 in one query says nothing about 0.8 in
   another. Only within-query ordering is meaningful.
3. **A score is not a verdict.** No score, at any value, means "this fix will work here".
4. **Recall returns several rows per stored memory** (measured: 6 stored cases → 35 rows). The
   adapter already deduplicates to the best row per `case_key`, so `items` is one row per case —
   but do not assume the backend is one row per memory if you ever bypass the adapter.
5. **Thresholds are uncalibrated provisional defaults.** `min_final_score=0.05`,
   `semantic_floor=0.70`, `weak_reference_floor=0.6`, `contradiction_margin=0.2`,
   `stale_after_days=365`. They came from a six-case bank. They are configuration, not truth, and
   **must not be narrated as validated numbers** in the demo. Calibration against the final seed
   bank is still outstanding (C2).
6. **`semantic` is used for acceptance only, never for ordering.** It is a fallback so a real match
   is not discarded when `final` collapses. It is not a second relevance ranking.
7. **Absence of a match is not absence of history.** Abstention can also mean the bank changed
   under us, not that the engineer has never seen this.

## 8. What you send into the memory layer

**Into `recall`:** a `query: str` — the normalized problem text. Optionally `tags`, `types`,
`max_tokens`. `types` defaults to `("world", "experience")`; `max_tokens` defaults to 1024.
There is no `min_scores` pass-through that is verified — abstention happens app-side.

**Into `retain`:** exactly one `MemoryCase`, and **only after** `engineer_decision` is set. All ten
fields are required:

| Field | Type | Rule |
|---|---|---|
| `problem_signature` | `str` | non-empty |
| `symptoms` | `list[str]` | may be empty |
| `environment` | `dict[str,str]` | non-empty strings as keys and values |
| `observed_evidence` | `list[str]` | URI/reference form, no log blobs |
| `investigation_trace` | `list[str]` | ordered |
| `failed_approaches` | `list[{approach, why_failed}]` | may be empty, key must be present |
| `root_cause` | `str \| None` | `null` unless confirmed |
| `resolution` | `str \| None` | `null` if none |
| `outcome` | enum | `resolved｜workaround｜escalated｜deferred` |
| `verification_notes` | `str` | non-empty |

`case_id` and `session_id` are optional. **Set `session_id`** — omitting it makes the idempotency
key coarser than intended.

Validation fails closed: an invalid case raises `MemorySchemaError` and **nothing is written**.

## 9. What the memory layer does NOT decide

It does not decide relevance (it classifies; you interpret), whether a hypothesis is supported, whether
a precondition is met, whether a fix applies to the current environment, whether to retain, or what the
engineer should do. Retention is your call site; the layer only executes and reports.

## 10. Your integration points

```
normalize()  ->  DebugInput                     [yours, deterministic, no LLM]
recall()     ->  RecallSet                      [Rama, I/O]
classify_candidates() -> MatchReport + AbstentionDecision   [Rama, pure]
hypothesize() -> Hypothesis                     [yours, ONE LLM call, cites only MatchReport ids]
collect()    ->  Evidence                       [yours, current observations only]
verify()     ->  VerificationResult             [yours, checks Evidence ONLY]
decide()     ->  engineer_decision              [engineer, logged]
retain()     ->  RetentionDecision              [Rama, only after decision]
```

Rules to hold:

- `classify_candidates` is pure — pass a stub `RecallSet` in tests, no network.
- `generate_hypotheses` may cite only ids present in `MatchReport.provenance()`.
- `verify` checks preconditions against **`Evidence` only**. A recalled `environment` or
  `root_cause` may populate `mismatched_environment_fields`, and may be *displayed*, and nothing else.
- A hypothesis with no supporting case is `relevance_state: generic` — never dressed as
  memory-informed.
- `supported` requires an explicit engineer decision. Absence is a contract violation (T4).
- `retain()` is called **only** when `engineer_decision` is set (T5).
- Never swallow memory errors: an unreachable bank is not "no memory found".

## 11. Known limitations you must design around

| # | Limitation | What it means for you |
|---|---|---|
| L1 | `update()` / `invalidate()` are non-functional on `hindsight-client 0.10.1` (no `update_memory`; verified `AttributeError`). Product-level curation is M0-verified via REST/UI, so the capability exists — the binding does not | Do not call them. Out of the MVP path. Do not claim curation works |
| L2/L3 | `root_cause_key` is derived text, not a curated taxonomy, so **paraphrased** root causes are not detected as conflicting | Under-detects conflicts. It never invents one — safer direction, but a real blind spot |
| L4 | Thresholds are provisional and uncalibrated | Never present them as validated |
| L5 | `semantic_floor` and `contradiction_margin` are constructor-only, not env-driven | Setting env vars alone will not tune them |
| L6 | Isolated tests under-reported risk: all three behavioural defects appeared only against a **dirty/large** bank | Re-run live against the final bank, not a fresh one |

## 12. Your next concrete step

Build `pipeline/recall_match`: `recall()` → `classify_candidates()` → render the MEMORY section,
passing `MatchReport` forward. It is pure apart from the single `recall()` call, so it is testable
with a stub store immediately — and it needs nothing from me. Contract test T3 (citation integrity)
is the natural first test to write against it.
