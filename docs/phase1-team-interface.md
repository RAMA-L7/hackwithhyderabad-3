# Phase 1 Team Interface & Ownership

**Status:** Proposed split for joint confirmation. Not a unilateral assignment.
**Date:** 2026-09-27 · **Revised:** 2026-09-28 (Rama's memory column is now implemented; see
`docs/handoff-mukul-memory.md` for what Mukul consumes).
**Reference:** `docs/m1-contract.md` (the contract both sides code against).

## 0. Implementation status of each side

| Side | Status |
|---|---|
| **Rama — memory layer** | **Implemented and tested** at `rama-m0` `6f60af1`: 78 tests, 7 live against Hindsight Cloud. Known limitations in `docs/m1-contract.md` §5 |
| **Mukul — pipeline, `llm/`, CLI** | **Not started.** Sections 2–3 below are still declarations, not implementations |
| **Shared — `schemas.py`** | Partially implemented: the memory schemas exist; `Evidence`, `Hypothesis`, `VerificationResult`, `Resolution`, `DebugInput`, `NormalizedDebugCase` are not written yet |

Nothing in this document may be read as claiming the Mukul side exists.

## 1. Ownership

| Area | Proposed owner | Scope |
|------|----------------|-------|
| `memory/store.py`, `memory/hindsight_store.py` | Rama | MemoryStore ABC + Hindsight implementation |
| Memory case schema implementation + validation | Rama | 8 essential fields, tag/metadata mapping |
| `memory/matching.py` | Rama | relevance classes, contradiction surfacing, staleness flags, abstention |
| `seeds/` + idempotent loader | Rama | seed cases + loader |
| Retention storage integration | Rama | validated write, fact-id capture, idempotency |
| `pipeline/normalize.py` | Mukul | deterministic-first normalization |
| `pipeline/hypothesize.py` | Mukul | structured hypotheses with citations |
| `pipeline/evidence.py`, `verify.py`, `resolve_retain.py` | Mukul | evidence model, verification gate, resolution, retention decision |
| `llm/` (adapter, router, provider adapters, validation) | **Mukul (proposed — confirm C1)** | only consumer is hypothesis generation |
| `cli.py` | Mukul | four visibly separated sections |
| `schemas.py` | **shared, jointly frozen** | the coupling point — changes require both |
| `tests/contract/`, `tests/e2e/`, demo, submission | shared | integration and acceptance |

## 2. Python-Level Boundaries (Rama's section is implemented; the rest are declarations)

```python
# ---- shared/frozen by both in M1 ----
# debugagent/schemas.py  : DebugInput, NormalizedDebugCase, MemoryCase, RecallResult, RecallSet,
#                           Evidence, Hypothesis, VerificationResult, Resolution,
#                           RetentionDecision, AbstentionDecision, MatchReport

# ---- Rama: memory layer ----
class MemoryStore(Protocol):
    def recall(self, *, query: str, tags: list[str], types: list[str],
               max_tokens: int, min_scores: dict | None = None) -> RecallSet: ...
    def retain(self, case: MemoryCase) -> RetentionDecision: ...          # idempotent
    def update(self, case_id: str, *, text: str, reason: str) -> None: ...  # PATCH correction
    def invalidate(self, case_id: str, *, reason: str) -> None: ...          # stale/superseded

# ---- Rama: matching (pure functions, no I/O) ----
def classify_candidates(case: NormalizedDebugCase, results: RecallSet,
                        policy: AbstentionPolicy) -> MatchReport: ...

# ---- Mukul: pipeline ----
def normalize(raw: DebugInput, taxonomy: DomainTaxonomy) -> NormalizedDebugCase: ...
def build_evidence(case: NormalizedDebugCase, engineer_inputs: list[str]) -> Evidence: ...
def generate_hypotheses(case: NormalizedDebugCase, match: MatchReport,
                        abstention: AbstentionDecision) -> list[Hypothesis]: ...
def verify(hypotheses: list[Hypothesis], evidence: Evidence,
           match: MatchReport) -> list[VerificationResult]: ...   # requires engineer decision
def build_resolution(verifications: list[VerificationResult], engineer_report: str) -> Resolution: ...

# ---- Mukul: LLM layer ----
class LLMAdapter(Protocol):
    def complete_structured(self, prompt: str, *, schema: dict, timeout_s: int) -> StructuredResult: ...
    def complete(self, prompt: str, *, timeout_s: int) -> LLMResult: ...

# ---- Mukul: orchestrator (owns the seam) ----
def investigate(raw: DebugInput, store: MemoryStore, ...) -> InvestigationSession: ...
```

## 3. Integration Contract

```
Mukul: investigate()
   └─ store.recall(query=case.problem_signature, tags=[...], types=[...], max_tokens=1024)
        └─ Rama: RecallSet (ranked, with provenance + scores)
   └─ Rama: classify_candidates() -> MatchReport + AbstentionDecision
   └─ Mukul: generate_hypotheses()   [one LLM call; citations must exist in MatchReport]
   └─ Mukul: verify()                [engineer decides; no auto-accept]
   └─ Mukul: build_resolution()
   └─ store.retain(MemoryCase)       [only if engineer_decision is set]
        └─ Rama: validation + idempotent write -> RetentionDecision
```

**Coupling rules**
1. The only shared mutable artefact is `schemas.py` (frozen in M1).
2. Mukul never imports Hindsight; Rama never imports the LLM layer.
3. Mukul's hypothesis citations are validated against `MatchReport.case_ids` — an unknown id is a
   contract error, caught by a contract test.
4. Retention is only ever called after `VerificationResult.engineer_decision` is set.

## 4. Contract Tests (shared, written first)

| Test | Asserts |
|------|---------|
| T1 schema round-trip | every schema validates its own minimal valid instance; invalid instances rejected |
| T2 recall seam | `RecallSet` from a stub store drives `classify_candidates` without I/O |
| T3 citation integrity | hypothesis `supporting_case_ids` ⊆ `MatchReport.case_ids` |
| T4 no auto-decide | `verify()` output with a supported hypothesis and no engineer decision is rejected |
| T5 retention gate | `retain()` is not called when no resolution was confirmed (spy store) |
| T6 abstention path | empty `RecallSet` → `abstained=True`, generic hypotheses with empty citations |

## 5. Items Needing Joint Confirmation

| # | Item | Why it matters |
|---|------|----------------|
| C1 | `llm/` ownership (proposed Mukul) | Only consumer is hypothesis generation; Mukul is the natural owner, but it is infrastructure |
| C2 | Abstention policy **shape** (frozen) — threshold *values* deferred to calibration against the final seed bank | Affects both matching (Rama) and CLI messaging (Mukul) |
| C3 | Seed domain family | Rama authors seeds; Mukul's normalizer must parse the same vocabulary |
| C4 | CLI labels | Must match the four-layer presentation the demo depends on |
| C5 | `observed_evidence` shape | Affects seed authoring and rendering |

## 6. Parallelisation Safety

Rama may work on `memory/`, `seeds/`, and matching while Mukul works on `normalize`, `llm/`,
`evidence`, and `verify` — **provided `schemas.py` is frozen first**. `pipeline/recall_match`,
`hypothesize`, `verify`, and `resolve_retain` change together and must not be edited in parallel by
two people.
