# M1 Contract-Freeze Checklist

**Status:** To be completed **together** in the 30-minute session, before any parallel coding.
**Date:** 2026-09-27. **Reference:** `docs/m1-contract.md` (normative), `docs/m1-freeze-decisions.md`.

Legend: `[ ]` open · `[x]` frozen. Nothing here is frozen yet.

## 1. Decisions (see m1-freeze-decisions.md)
- [ ] C1 `llm/` ownership
- [ ] C2 `min_final_score` initial value
- [ ] C3 seed domain family
- [ ] C4 CLI command names + section labels
- [ ] C5 `observed_evidence` representation
- [ ] C6 adopt: abstention computed app-side, `min_scores` unverified
- [ ] C7 adopt: Memory Defense not claimed as a secret guardrail

## 2. `schemas.py` (shared, frozen first — the only shared mutable artefact)
- [ ] All 10 schemas from `m1-contract.md` §3 exist as code: `DebugInput`, `NormalizedDebugCase`,
      `MemoryCase`, `RecallResult`, `RecallSet`, `Evidence`, `Hypothesis`, `VerificationResult`,
      `Resolution`, `RetentionDecision`, `AbstentionDecision`, `MatchReport`
- [ ] The 8 essential memory fields are present and required (empty list allowed for `failed_approaches`,
      `null` allowed for unconfirmed `root_cause` / `resolution`)
- [ ] Hindsight-native `mentioned_at` / fact ids are **not** duplicated as app fields
- [ ] Frozen: changes require both members in the same commit

## 3. MemoryStore interface (Rama)
- [ ] `recall(query, tags, types, max_tokens)` → `RecallSet`; `min_scores` optional pass-through only
- [ ] `retain(case)` → `RetentionDecision`, idempotent on `signature` + session id
- [ ] `update()` / `invalidate()` **declared but deferred to Phase 2**
- [ ] Errors: `MemoryUnavailable`, `MemoryAuthError`, `MemorySchemaError` — never swallowed
- [ ] Interface exposes no Hindsight/provider objects upstream

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
- [ ] `AbstentionDecision` always carries `top_score` and `threshold_used`
- [ ] Empty `RecallSet` → `abstained=True` with a visible signal (not an error)
- [ ] Threshold lives in config; re-calibration path documented
- [ ] Irrelevant candidates are excluded from the proposal but retained in the trace log

## 8. Retention ownership
- [ ] Mukul owns the call site (post-verification only); Rama owns validation + write
- [ ] Only Rama sets `retained: true|false`
- [ ] Failed approaches observed this session are retained alongside the resolution
- [ ] Hindsight fact id is captured and shown

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

## 11. Contract tests T1–T6 (shared, written before implementation)
- [ ] T1 schema round-trip (valid accepted, six defect classes rejected)
- [ ] T2 recall seam: stub store → `classify_candidates` without I/O
- [ ] T3 citation integrity: hypothesis ids ⊆ `MatchReport` ids
- [ ] T4 no auto-decide: `supported` without a decision is rejected
- [ ] T5 retention gate: `retain()` not called without a confirmed resolution (spy store)
- [ ] T6 abstention path: empty `RecallSet` → abstained + generic hypotheses

## 12. Parallel-implementation safety
- [ ] Each member creates their own branch from `rama-m0`
- [ ] `pipeline/recall_match`, `hypothesize`, `verify`, `resolve_retain` are not edited in parallel
- [ ] First integration slot agreed (who joins, when)
