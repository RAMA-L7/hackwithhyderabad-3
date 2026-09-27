# M1 Implementation Contract

**Status:** Frozen contract for Phase 1 implementation. No code written yet.
**Date:** 2026-09-27 · **Deadline:** MVP demo-ready 29 Sept afternoon.
**Inputs:** `docs/domain-neutral-system-design.md`, `docs/architecture-review.md`,
`docs/phase1-execution-plan.md`, `docs/provider-verification.md` (M0 run `20260927T183607Z`),
`docs/two-phase-implementation-plan.md`.
**Binding invariants:** LLM proposes · Hindsight provides remembered information · current evidence
verifies · the engineer decides · an LLM hypothesis is never promoted to verified evidence ·
no provider-specific code outside `llm/` · no recalled fix reaches action without a logged check.

## 1. Ownership Map

| Layer | Owner | Must never |
|-------|-------|-----------|
| Input / CLI | Mukul | decide anything |
| Normalizer | Mukul | write to memory or evidence stores |
| MemoryStore / Hindsight | Rama | rank by business value or decide relevance |
| Recall + matching + abstention | Rama | fabricate a case, or force a match |
| LLM adapter / router | Mukul (proposed — needs confirmation) | execute tools, touch memory or evidence |
| Hypothesis generation | Mukul | label a hypothesis as verified |
| Evidence model | Mukul | accept recalled text as current evidence |
| Verification / Resolution | Mukul | skip engineer confirmation |
| Retention | Rama (storage) + Mukul (decision) | retain an unverified outcome |

## 2. Component Contracts

### 2.1 Input (CLI-provided)
- **Input:** engineer's free-text issue description plus any pasted measurements.
- **Output:** `DebugInput` (§3.1).
- **Errors:** empty/blank description → `InputError` (CLI prints guidance, exits non-zero).
- **Must not:** interpret, enrich, or guess domain fields.

### 2.2 Normalizer
- **Input:** `DebugInput`, `DomainTaxonomy` (config).
- **Output:** `NormalizedDebugCase` (§3.2) with `environment` fields the engineer did not supply
  left as `null` (never invented).
- **Errors:** schema validation failure → `NormalizationError` listing offending fields.
- **Ownership:** Mukul. **Deterministic only in Phase 1 — no LLM call.** `problem_signature` is
  derived from the raw text (first line / taxonomy rules); no model is consulted.
- **Rationale (latency, 2026-09-27):** every structured call costs 3.4–7.2 s (up to ~14 s through
  failover). An LLM-assisted normalizer would double the per-interaction cost for no demo benefit.
  One LLM call per interaction is the Phase 1 budget (hypothesis generation only).
- **Must not:** write to MemoryStore or Evidence; never overwrite engineer-supplied values;
  never invent an environment value the engineer did not state.

### 2.3 MemoryStore (interface owned by Rama)
- **Input:** `query: str`, `tags: list[str]`, `types: list[str]`, `max_tokens: int`.
  (`min_scores` is accepted as an optional pass-through but **must not** be relied upon — it was not
  exercised in M0; see §4.4.)
- **Output:** `RecallSet` (§3.4) — ranked `RecallResult` items with provenance, never bare text.
- **Errors:** `MemoryUnavailable`, `MemoryAuthError` (no silent swallow), `MemorySchemaError`.
- **Phase 1 scope:** implement `recall` + `retain` only. `update` and `invalidate` are **declared but
  deferred to Phase 2** — the MVP demo path does not correct or retire a case.
- **Ownership:** Rama. Only component that talks to Hindsight.
- **Must not:** decide relevance, filter by business rules, or expose provider objects upstream.

### 2.4 Recall / Matching / Abstention
- **Input:** `NormalizedDebugCase`, `RecallSet`, `AbstentionPolicy` (config).
- **Output:** `MatchReport` = ranked candidates each labelled `relevant | partial | irrelevant |
  contradictory | stale`, plus `AbstentionDecision` (§3.10).
- **Errors:** empty candidate set is **not** an error — it yields `abstained`.
- **Ownership:** Rama.
- **Must not:** silently drop candidates; every candidate keeps an entry in the trace log even when
  excluded from the proposal (needed for evaluation).

### 2.5 Hypothesis generation
- **Input:** `NormalizedDebugCase`, `MatchReport`, `AbstentionDecision`, `HypothesisSchema`.
- **Output:** `list[Hypothesis]` (§3.6), each citing `supporting_case_ids` and stating
  `refutation_conditions`; when abstained, hypotheses are labelled `generic` with empty citations.
  The result also carries `provider`, `model`, `fallback_used` for CLI display.
- **Errors:** schema-invalid LLM output → one retry → then router failover → then raise
  `StructuredOutputError`. **Never** accept unvalidated text.
- **Ownership:** Mukul. **One LLM call per interaction** (Phase 1 budget).
- **Must not:** assert a root cause as fact; invent case IDs that were not in the `MatchReport`;
  present a fallback answer without the `fallback_used` flag reaching the CLI.

### 2.6 Evidence (current-case model)
- **Input:** engineer-supplied symptoms, environment identifiers, measurements.
- **Output:** `Evidence` (§3.5) — each item carries `source` and `captured_at`.
- **Errors:** unknown field value is recorded as `null` + `known=false`.
- **Ownership:** Mukul.
- **Must not:** accept recalled content; evidence is append-only within a session and never merged
  into the historical store except through a verified resolution.

### 2.7 Verification (gate + engineer decision)
- **Input:** `list[Hypothesis]`, `Evidence`, recalled-case `environment` snapshots.
- **Output:** `VerificationResult` (§3.7) with per-hypothesis `status` and an explicit
  `engineer_decision` (`accept | modify | reject`) plus `relevance_confirmed: bool`.
- **Errors:** a hypothesis marked `supported` without `engineer_decision` is a contract violation.
- **Ownership:** Mukul.
- **Prohibition (memory-is-not-evidence):** preconditions are checked **against `Evidence` only**.
  A recalled case's `environment`, `root_cause`, or `resolution` may be *displayed* as the original
  conditions that informed a hypothesis, and may populate `mismatched_environment_fields` — but must
  **never** satisfy a precondition or upgrade a hypothesis's status. If Evidence lacks a field, the
  status is `insufficient_evidence`, regardless of what memory asserts.
- **Must not:** auto-accept; auto-decide relevance; proceed to resolution without a decision.

### 2.8 Resolution
- **Input:** accepted/modified hypothesis, engineer's executed action, observed result.
- **Output:** `Resolution` (§3.8) including `failed_approaches` observed this session.
- **Errors:** result contradicting the accepted hypothesis is recorded as a **failed approach**, not
  discarded.
- **Ownership:** Mukul.
- **Must not:** be inferred by the agent; must come from the engineer.

### 2.9 Retention
- **Input:** `Resolution` + `VerificationResult` + `NormalizedDebugCase` → assembles `MemoryCase` (§3.3).
- **Output:** `RetentionDecision` (§3.9) with `retained: bool` and a reason; on retain, the Hindsight
  fact id is returned for the audit trail.
- **Errors:** schema-invalid case → `RetentionRejected`; the case is queued for engineer correction,
  never silently stored.
- **Ownership — split precisely (was ambiguous, now frozen):**
  - **Mukul** owns the *call site*: `investigate()` calls `retain()` **only** when a
    `VerificationResult.engineer_decision` is set.
  - **Rama** owns the *implementation*: schema validation, idempotency, and the Hindsight write;
    Rama alone sets `retained: true|false` from the validation outcome.
- **Must not:** retain anything without `engineer_decision` set; must be idempotent per case
  (keyed on signature + session id) because Hindsight retain is not documented as idempotent.

### 2.10 CLI
- **Input:** engineer commands: `debug`, `verify`, `resolve`, `inspect` (names provisional — see C4).
- **Output:** four visibly distinct sections — **MEMORY**, **EVIDENCE**, **PROPOSAL**, **DECISION**.
- **Trust-surface requirements (frozen):** the PROPOSAL header must show the serving
  `provider` / `model` / `fallback_used`; MEMORY must show each case id, its original environment,
  and its relevance class; DECISION must show the engineer's explicit choice and note. No section may
  be visually merged with another, and a hypothesis must never be rendered with verification styling.
- **Errors:** unknown command → usage; any component error → one-line reason, no stack trace to user.
- **Ownership:** Mukul.
- **Must not:** merge the four sections visually; never imply a proposal is verified.

## 3. Data Schemas (Phase 1 minimum)

Timestamps and ids from Hindsight (`mentioned_at`, fact ids) are **not** duplicated as app fields.

### 3.1 DebugInput
`description: str` (req) · `measurements: list[str] = []` · `environment_hints: dict = {}`

### 3.2 NormalizedDebugCase
`problem_signature: str` (req) · `symptoms: list[str]` (req) · `environment: dict[str, str|None]`
(req, may contain nulls) · `raw_description: str` (req) · `source_case_ids: list[str] = []`

### 3.3 MemoryCase (the 8 essential fields)
| Field | Required | Type |
|-------|----------|------|
| `problem_signature` | yes | str |
| `symptoms` | yes | list[str] |
| `environment` | yes | dict[str, str] (ids only) |
| `observed_evidence` | yes | list[str] (references/URIs, never blobs) |
| `investigation_trace` | yes | list[str] (ordered) |
| `failed_approaches` | yes | list[{approach, why_failed}] (may be empty list, key present) |
| `root_cause` | yes | str \| null (null unless confirmed) |
| `resolution` | yes | str \| null |
| `outcome` | yes | enum `resolved｜workaround｜escalated｜deferred` |
| `verification_notes` | yes | str |
Tags/metadata for Hindsight: `outcome`, `domain`, `service` (derived from `environment`).

### 3.4 RecallResult / RecallSet
`RecallResult`: `case_id: str` (req) · `text: str` · `score_final: float` (req) · `score_semantic: float`
· `score_keyword: float|None` · `environment: dict` · `outcome: str`
`RecallSet`: `items: list[RecallResult]` (req) · `recalled_at: str` (req) · `bank_id: str` (req)

### 3.5 Evidence
`items: list[{value, source, captured_at, known: bool}]` (req) · `unknown_fields: list[str]`

### 3.6 Hypothesis
`hypothesis: str` (req) · `supporting_case_ids: list[str]` (req) · `relevance_state:
enum supported|conditional|weak-reference|generic` (req) · `refutation_conditions: list[str]` (req) ·
`recommended_next_step: str` (req)
(`relevance_state: generic` ⇔ empty `supporting_case_ids` — the no-memory case.)

### 3.7 VerificationResult
`hypothesis_ref: str` (req) · `status: enum supported|contradicted|insufficient_evidence` (req) ·
`mismatched_environment_fields: list[str]` · `engineer_decision: enum accept|modify|reject` (req) ·
`relevance_confirmed: bool` (req) · `engineer_note: str`

### 3.8 Resolution
`action_taken: str` (req) · `observed_result: str` (req) · `root_cause_confirmed: str|None` (req) ·
`failed_approaches_this_session: list[{approach, why_failed}]` · `evidence_refs: list[str]`

### 3.9 RetentionDecision
`retained: bool` (req) · `reason: str` (req) · `memory_case_id: str|None` · `validated: bool` (req)

### 3.10 AbstentionDecision
`abstained: bool` (req) · `relevance_class: enum relevant|partial|irrelevant|contradictory|stale` (req)
· `top_score: float` (req) · `threshold_used: float` (req) · `reason: str` (req)

## 4. Cross-Cutting Rules (from M0 evidence)

1. **Structured output:** local validation is the only guarantee. Primary route works as a schema
   hint; fallback supports strict routing. One retry → failover → raise. Never downgrade silently.
2. **Failover:** only on timeout / 429 / 5xx. **Never** on 400, 401, 402, 403, 404 (config/auth classes).
3. **Abstention policy (provisional, config-driven):** `min_final_score = 0.05`; scores
   0.05–0.6 = `weak-reference`; ≥0.6 = candidate for `relevant`. Observed bands: relevant ≈ 0.97–1.09,
   vague ≈ 0.35–0.44, unrelated ≈ 0.002–0.006. **Must be re-calibrated** as seed data grows; scores are
   query-relative per provider docs.
4. **Recall volume:** ~15 results/query by default → use `max_tokens` + **app-side filtering**.
   `min_scores` is documented but was **not exercised in M0** (M0 used `types` + `max_tokens` only);
   abstention must therefore be computed in `classify_candidates` from returned scores, never
   delegated to `min_scores`. Treat `min_scores` as unverified until a runtime test exercises it.
5. **Memory Defense:** M0 confirmed the `update_bank_config(memory_defense=…)` call returns without
   error, but **active redaction was not independently verified**. Do not claim it protects secrets
   until a runtime test demonstrates a redaction; rely on the "no secrets in seed data" rule instead.
6. **Latency budget:** ~3–7 s per structured call, up to ~14 s through failover → **exactly one LLM
   call per interaction** (hypothesis generation only; normalizer is deterministic). No `reflect()`.
7. **Hindsight Cloud:** single bank per project, auto-provision if absent; Memory Defense applied via
   `update_bank_config` (not `create_bank`, which rejects that field — M0-verified defect).
8. **Idempotent retention:** keyed on `problem_signature` + session id.

## 5. Open Contract Questions

Full proposals, rationale and consequences live in `docs/m1-freeze-decisions.md`; the session
checklist is `docs/m1-contract-freeze-checklist.md`. **None of these is decided yet.**

| # | Question | Owner | Needed by |
|---|----------|-------|-----------|
| C1 | Confirm `llm/` ownership (proposed: Mukul) | both | start of parallel work |
| C2 | `min_final_score` initial value (proposed 0.05) | Rama + Mukul | M5 |
| C3 | Single domain taxonomy for seeds (proposed: service/API runtime failures — same family as M0 probes) | both | M2 |
| C4 | CLI command names and output labels | Mukul | M7 |
| C5 | Whether `observed_evidence` stores URIs only or short text + URI | Rama | M2 |
| C6 | Adopt: abstention computed app-side; `min_scores` unverified (technical, low controversy) | review | M5 |
| C7 | Adopt: Memory Defense not claimed as a proven guardrail (technical, low controversy) | review | M2 |
