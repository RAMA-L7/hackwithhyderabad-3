# M1 Contract-Freeze Checklist

**Status:** To be completed **together** in the 30-minute session, before any parallel coding.
**Date:** 2026-09-27 · **Revised:** 2026-09-28 (C2 reshaped; section 9 added from M1 live evidence).
**Reference:** `docs/m1-contract.md` (normative), `docs/m1-freeze-decisions.md`.

Legend: `[ ]` open · `[x]` frozen · `[~]` implemented but unverified · `[!]` known limitation.
Nothing here is frozen yet unless marked otherwise.

## 1. Decisions (see m1-freeze-decisions.md)
- [ ] C1 `llm/` ownership
- [~] C2 abstention policy **shape** — freeze the shape (§C2.1); do **not** freeze threshold values
- [ ] C3 seed domain family
- [ ] C4 CLI command names + section labels
- [ ] C5 `observed_evidence` representation
- [ ] C6 adopt: abstention computed app-side, `min_scores` unverified
- [ ] C7 adopt: Memory Defense not claimed as a secret guardrail
- [ ] **C2 calibration pass** — set thresholds from recorded scores on the *final* seed bank
      (not a fresh or six-case bank). Blocks any claim of a calibrated threshold.

## 2. `schemas.py` (shared, frozen first — the only shared mutable artefact)
- [ ] All 10 schemas from `m1-contract.md` §3 exist as code: `DebugInput`, `NormalizedDebugCase`,
      `MemoryCase`, `RecallResult`, `RecallSet`, `Evidence`, `Hypothesis`, `VerificationResult`,
      `Resolution`, `RetentionDecision`, `AbstentionDecision`, `MatchReport`
- [ ] The 8 essential memory fields are present and required (empty list allowed for `failed_approaches`,
      `null` allowed for unconfirmed `root_cause` / `resolution`)
- [ ] Hindsight-native `mentioned_at` / fact ids are **not** duplicated as app fields
- [ ] Frozen: changes require both members in the same commit

## 3. MemoryStore interface (Rama)
- [x] `recall(query, tags, types, max_tokens)` → `RecallSet`; `min_scores` optional pass-through only
- [x] `retain(case)` → `RetentionDecision`, idempotent on `signature` + session id
- [!] `update()` / `invalidate()` **declared, implemented, but non-functional on SDK 0.10.1**
      (no `update_memory` on the client). Deferred to Phase 2; must not be claimed working
- [x] Errors: `MemoryUnavailable`, `MemoryAuthError`, `MemorySchemaError` — never swallowed
- [x] Interface exposes no Hindsight/provider objects upstream
- [x] Recall deduplicated to one row per `case_key` (measured need: 6 cases → 35 rows)

## 4. LLMAdapter interface (Mukul, subject to C1)
- [ ] `complete_structured(prompt, schema, timeout_s)` → validated result or raise
- [ ] `complete(prompt, timeout_s)` → `LLMResult` (for non-JSON text only)
- [ ] `LLMResult` carries `provider`, `model`, `fallback_used`
- [ ] Failover on timeout/429/5xx **only**; never on 400/401/402/403/404
- [ ] Local schema validation is the only conformance guarantee: retry once → failover → raise
- [ ] No silent degradation to unvalidated text, ever

## 5. Pipeline interfaces (Mukul)
- [ ] `normalize()` is **deterministic** — no LLM call (one LLM call per interaction total)
- [ ] `classify_candidates()` is pure (Rama), no I/O
- [ ] `generate_hypotheses()` may only cite case ids present in `MatchReport`
- [ ] `verify()` checks preconditions against **Evidence only** — never against recalled fields
- [ ] `build_resolution()` originates from the engineer, never inferred
- [ ] `investigate()` calls `retain()` **only** when `engineer_decision` is set

## 6. Error behaviour
- [ ] Component errors surface as one-line reasons to the CLI user (no stack traces)
- [ ] Memory/auth/config errors are never converted into "no memory found"
- [ ] An LLM failure is never presented as "the model found nothing relevant"

## 7. Abstention behaviour
- [x] `AbstentionDecision` always carries `top_score` and `threshold_used`
- [x] Empty `RecallSet` → `abstained=True` with a visible signal (not an error)
- [x] Semantic fallback used for **acceptance only, never ordering** (measured necessity:
      `final` collapsed to 0.003 while `semantic` held 0.78 on a large bank)
- [x] Contradictions surfaced, never silently resolved; keyed on (service, `root_cause_key`)
- [!] Threshold values are **provisional defaults**, not calibrated. Not to be narrated as
      universal truths. See `m1-freeze-decisions.md` §C2.2–C2.3
- [!] `semantic_floor = 0.70` does **not** separate the M0 vague class (0.766) from relevant
      (0.80–0.88). Open calibration question, recorded not hidden
- [x] Irrelevant candidates are excluded from the proposal but retained in the trace log

## 8. Retention ownership
- [x] Mukul owns the call site (post-verification only); Rama owns validation + write
- [x] Only Rama sets `retained: true|false`
- [x] Failed approaches observed this session are retained alongside the resolution
- [x] Hindsight fact id is captured and shown
- [x] Idempotency verified live: same bank seeded twice → 6 inserted, then 6 skipped

## 9. Evidence / proposal / decision separation
- [ ] `Evidence` items carry `source` + `captured_at`; missing values recorded as `known=false`
- [ ] No recalled text may enter the `Evidence` model
- [ ] A hypothesis without cited cases is `relevance_state: generic` (never dressed as memory-informed)
- [ ] `supported` requires an explicit engineer decision; absence is a contract violation

## 10. CLI trust-surface requirements
- [ ] Four sections rendered separately: MEMORY / EVIDENCE / PROPOSAL / DECISION
- [ ] PROPOSAL header shows provider, model, `fallback_used`
- [ ] MEMORY shows case id, original environment, relevance class
- [ ] DECISION shows the engineer's choice and note
- [ ] A hypothesis is never styled as verified

## 12. Contract tests T1–T6 (shared, written before implementation)
- [x] T1 schema round-trip — implemented for the memory schemas (`tests/test_schemas.py`);
      the Mukul-owned schemas (Evidence, Hypothesis, VerificationResult, Resolution) are not written yet
- [x] T2 recall seam: stub store → `classify_candidates` without I/O — implemented
      (`tests/test_store_offline.py`, `tests/test_matching.py`)
- [ ] T3 citation integrity: hypothesis ids ⊆ `MatchReport` ids — **blocks on Mukul's code**
- [ ] T4 no auto-decide: `supported` without a decision is rejected — **blocks on Mukul's code**
- [ ] T5 retention gate: `retain()` not called without a confirmed resolution (spy store)
      — **blocks on Mukul's call site**
- [ ] T6 abstention path: empty `RecallSet` → abstained + generic hypotheses — empty-set half
      implemented; the `generic` half **blocks on Mukul's code**

## 13. Parallel-implementation safety
- [ ] Each member creates their own branch from `rama-m0`
- [ ] `pipeline/recall_match`, `hypothesize`, `verify`, `resolve_retain` are not edited in parallel
- [ ] First integration slot agreed (who joins, when)

## 14. Handoff readiness (Rama → Mukul)
- [x] `docs/handoff-mukul-memory.md` written, covering the interface, the recall shape, how to read
      abstention, provenance, contradictions, and the explicit "memory is not evidence" rule
- [ ] Mukul confirms he can build `pipeline/recall_match` against the documented interface only
- [ ] First end-to-end demo run performed against the **final** seed bank, not a fresh one
