# Architecture Review — Memory-Driven Investigation Agent

**Status:** Review only — no code, no dependencies, no domain selected.
**Date:** 2026-09-27.
**Reference:** `docs/domain-neutral-system-design.md` (design under review),
`docs/hindsight-capability-verification.md` (proven building blocks),
`docs/final-project-definition.md` (selected scope),
`docs/implementation-readiness-review.md` (readiness verdict).
**Principle under test:** Memory provides relevant prior knowledge. Evidence establishes what is
true now. The agent proposes. The authorized human or deterministic system decides.
EGER is not invoked anywhere in this review and is not a dependency of anything below.

## 1. System Boundaries

| Owner | Owns | Must never do |
|-------|------|---------------|
| Agent (orchestrator) | Intake structuring assist, recall query formulation, hypothesis synthesis, proposal ranking, abstention decision | Write to evidence; execute actions; treat recall scores as truth |
| Hindsight (via `MemoryStore`) | Durable case storage, multi-strategy retrieval with scores, consolidation, edit/invalidate curation, redaction | Rank by business value; decide relevance; verify currency |
| Current evidence | The present case's symptoms, environment, measurements with source + capture point | Contain recalled text (contamination = defect) |
| Engineer/user | Relevance confirmation, proposal accept/modify/reject, execution, outcome sign-off | Be bypassed on any path to action or retention |

**Overlaps found (2, both manageable):**
- O1. The Normalizer uses LLM condensation, so interpretation can leak into memory at write time.
  Mitigation: normalizer output is schema-validated, engineer-visible before retain, and editable —
  normalization is deterministic-first, LLM only for condensing free text.
- O2. The CLI renders all four layers, making rendering a trust surface: a mislabeled recall looks
  like evidence. Mitigation: fixed render templates per layer (memory cards always show case ID +
  original conditions + relevance state), reviewed as part of CLI acceptance.

## 2. Data Flow Trace (one investigation, end to end)

| Transition | Input | Transformation | Output | Owner | Failure mode → handling |
|------------|-------|----------------|--------|-------|-------------------------|
| Intake | Raw problem description + env identifiers | Structure into neutral shape (adapter taxonomy) | Normalized case draft | Normalizer (+adapter config) | Malformed input → schema validation rejects with explicit errors |
| Recall | Normalized signature + tags + query | Hindsight recall (query + `tags_match` + `types` + budget) | Ranked candidate facts with scores + provenance | `MemoryStore` | No confident match → abstention path; service down → generic path + visible "memory unavailable" |
| Compare | Candidates + current evidence | Env-field diff; mismatch listing | Match report (supporting/contradicting/irrelevant) | Orchestrator (deterministic diff) | Missing env fields → explicit "unknown" markers, never filled from memory |
| Hypothesize | Match report | Application ranking (outcome × env × recency) | Ranked hypotheses, each citing case IDs | Orchestrator | All weak → abstain to generic checklist |
| Check evidence | Top hypothesis preconditions | Precondition lookup in evidence model | Pass/fail/unknown per precondition | Verification gate (deterministic) | Unknown → proposal labeled conditional, never assumed |
| Propose | Verified hypotheses | Render proposal object (Section 6) | Investigation plan for engineer | Orchestrator | — (proposal is untrusted by construction) |
| Verify/authorize | Proposal | Engineer confirm/modify/reject + relevance marks | Authorized action + decision record | Engineer | Rejection → loop back with corrected relevance; logged |
| Resolve | Authorized action | Engineer executes externally | Observed result | Engineer | Result contradicts hypothesis → captured as failed approach, not discarded |
| Retain | Completed case + decision record | Validate → `retain()` new case; PATCH prior cases if corrected | Bank enriched; audit trail extended | `MemoryStore` (+app validation) | Validation failure → case queued for engineer fix, never silently stored |

## 3. Memory Model Review (8 fields)

Each field mapped to its consumer — all eight earn their place:

- `problem_signature` → recall query seed. Necessary.
- `symptoms` → recall + display. Necessary.
- `environment` → verification gate diff. Necessary.
- `observed_evidence` (as references) → provenance display + refutation checks. Necessary.
- `investigation_trace` → the "path" the demo improves. Necessary.
- `failed_approaches` + why → warn-against-retry behavior. Necessary.
- `root_cause` + `resolution` + evidence refs → propose-first behavior. Necessary.
- `outcome` → ranking weight + pattern statistics. Necessary.
- `verification_notes` → recall-quality ground truth + evaluation inputs. Necessary.

Genuinely missing: nothing structural. Timestamps, IDs, and edit history come from Hindsight
natively (`mentioned_at`, fact IDs, `edited_at`) and must NOT be duplicated as app fields.
Deferred (correctly): lessons free-text, suspected-cause lists beyond the trace, confidence scores.

## 4. Recall Behavior (six cases)

- **Relevant:** attach with original conditions; cite in proposal.
- **Similar but non-identical:** present with explicit mismatches (env, era, outcome); proposal
  must state what would need to be true to reuse it.
- **Irrelevant:** drop silently from the proposal but keep in trace log for evaluation scoring.
- **Contradictory (same symptoms, different cause):** surface both with distinguishing conditions;
  Hindsight consolidation reconciles in-stream contradictions, engineer resolves the rest. Never
  pick one silently.
- **Stale:** dates make age visible; application staleness policy (no native TTL — verified
  limitation) flags or invalidates; stale cases need re-verification before use.
- **Insufficient evidence:** explicit abstention with visible "no relevant memory" signal; generic
  path continues. Abstention thresholds are tuned empirically (scores are per-query relative).

## 5. Evidence Boundary

Current evidence = {symptoms as stated now, environment identifiers as stated now, measurements
pasted or read now}, each with source + capture point. Anti-contamination rules: recalled text is
never copied into the evidence store; the env-diff reads recalled metadata and current evidence
as two separate inputs; "unknown" fields stay unknown. The evidence store is append-only within a
session and is discarded or archived (not merged into history) except via a verified outcome.

## 6. Proposal Boundary

Hypothesis object shape: `{hypothesis, supporting_case_ids[], original_conditions[],
refutation_conditions[], recommended_next_step, relevance_state}`. `relevance_state` ∈
{supported, conditional, weak-reference, abstained}. The proposal renderer must show all fields;
a hypothesis without cited cases is labeled as generic reasoning, never dressed as recalled
knowledge. This keeps hypotheses permanently distinguishable from verified facts.

## 7. Authorization Boundary

Audit of every component for accidental authority: orchestrator cannot execute (no tool access in
MVP); `MemoryStore` cannot decide (read/write/curate only); `min_scores` abstention is a gate,
not a decision (it withholds, never approves); Memory Defense redaction is a content guardrail,
not engineering authority; the CLI renders but never auto-accepts. Exactly one authority exists:
the engineer's explicit confirmation, logged with the decision. **Caution recorded:** do NOT use
Hindsight's LLM-wrapper auto-retain for outcome retention — automatic writes would bypass the
verification gate. Retention stays an explicit, engineer-confirmed call.

## 8. Hindsight Integration Map

| Application requirement | Hindsight capability | Verified source | Confidence | Fallback if unavailable |
|---|---|---|---|---|
| Write one case | `retain()` (content + context + metadata/tags) | Overview; GitHub README; recall-page fields | High | None needed — verified; if server down, queue cases locally and retry (no silent loss) |
| Retrieve by meaning + exact terms + entities + time | 4-arm recall (semantic/BM25/graph/temporal), RRF + cross-encoder, scores | `/developer/api/recall` | High | Degrade arms in order temporal→graph→keyword→semantic? No — arms are server-side; fallback is abstention + generic path |
| Filter by environment/outcome/domain | `tags` + `tags_match`/`tag_groups`, `types`, `max_tokens` | `/developer/api/recall` | High | Broader recall + stricter application-side filtering |
| Abstain on weak matches | `min_scores` (reranker/final floors) | `/developer/api/recall` | Medium (thresholds must be tuned; scores are relative) | Conservative default (abstain unless top score clears a high initial bar), tune during spike |
| Correct/supersede/retire cases | PATCH edit/invalidate/restore + history | `/developer/api/memories` | High | Append-only correction case + application-side exclusion list |
| Provenance for audit | IDs, `source_facts`, observation history, `edited_at`, reasons | `/developer/api/memories`, recall page | High | — |
| Isolation per project/persona | Banks, strict no-cross-bank-leakage | Overview; GitHub README | High | Single bank + strict tag partitioning (weaker; prefer banks) |
| Secrets guardrail | Memory Defense redaction (opt-in, 45 patterns) | Overview; GitHub README | Medium (existence verified; patterns/behavior not deep-read) | Application-side secret scan before retain |
| Python/TS clients; Docker/embedded/Cloud deploy | SDK + installation docs | GitHub README; docs site | High | — (choice still open, not capability) |
| Rigid domain schema | — | Not found in reviewed pages | Unresolved | Application-level convention (already the design) |
| Native TTL/expiry | — | Not found; use dates + invalidate | Unresolved | Application staleness policy (already the design) |
| Server auth model; rate limits; retain idempotency | — | Not found in reviewed pages | Unresolved | Local-only demo; idempotent loader by construction; load-test before claims |

No application requirement depends on an unresolved row.

## 9. Domain Adapter Sufficiency

The adapter concept is sufficient for later multi-domain support if its minimum interface is fixed
as: `normalize(raw) → CaseDraft`; `taxonomy() → {facets, tag vocabulary, env fields}`;
`render(case/proposal) → domain language`; `verify_facets() → comparable env field list`.
All four are pure configuration + small functions — no core changes needed per domain. Gaps to
close at adapter time (not now): taxonomy versioning across domains sharing one bank (prefer one
bank per domain initially), and display-label collisions. No adapter is built in the MVP; the
first instantiation hard-codes one taxonomy shaped exactly like this interface.

## 10. MVP Boundary (stripped to the hypothesis)

Central hypothesis: "Persistent memory changes the investigation behavior in a useful and
observable way." Proving it needs: intake → normalize → recall (±abstention) → env-diff →
proposal with citations → engineer verify → resolve → retain. Everything else is removed:
`reflect()`, mental models, knowledge pages (verified but unnecessary), web UI, auto-retain
wrapper, analytics, second domain, production hardening. Kept deliberately: Memory Defense
(cheap, risk-reducing), curation endpoints (needed for corrections demo), score outputs
(needed for abstention tuning).

## 11. Demo Architecture (domain-neutral)

```
Issue 1 → recall (empty: abstain, visible) → generic path → resolve → retain (visible)
Issue 2 (similar) → recall (hit, conditions shown) → env-diff (visible) → proposal cites case,
  warns failed approach → engineer verifies → resolve → retain
Contrast view: path 1 vs path 2 step sequences side by side
```

No performance numbers anywhere. The observable delta is structural (different steps,
warnings present, citations present), not quantitative.

## 12. Evaluation (behavioral, no invented accuracy)

Method: same engineer, paired similar cases, alternating memory-on/memory-off runs.
Observable properties, each with a direct instrument: relevant recall (engineer relevance marks);
irrelevant-memory rejection (dropped-from-proposal log); mismatch detection (env-diff hits);
evidence grounding (every applied fix traces to a relevance check); abstention (empty-bank and
weak-match runs produce abstain, zero hallucinated cases); correct retention (bank inspection:
complete cases, failed approaches present, no unverified hypotheses stored). Report raw counts
with method; no percentages-as-claims without denominators and repeats.

## 13. Implementation Sequence (risk-first)

Contract (case JSON, `MemoryStore` functions, proposal/verification shapes — joint sign-off)
→ Hindsight spike (stand up, 5 retains, tag+query steerability, abstention calibration, A–E
probe set) → memory model freeze + seed authoring (start immediately post-spike) → recall
behavior (ranking, contradiction surfacing, staleness flags) → investigation loop → domain
taxonomy as config (first-instantiation shape) → CLI → evaluation runs → demo. This reorders the
existing plan only slightly: abstention calibration moves into the spike, seed authoring overlaps
the loop build, and the interface contract becomes an explicit gate. Phase 0 decisions
(language, hosting, key, split, pre-seed rule) precede everything.

## 14. Team Boundary

No ownership is embedded in this architecture. Collaboration requirements (not assignments):
the spike and the interface contract are joint work (both members present); seed authoring needs
domain input plus recall-behavior feedback, so those two streams must stay in daily contact;
CLI needs frozen proposal shapes early. The proposed split in `docs/team-task-split.md` remains
unconfirmed and is not assumed here.

## 15. Final Architecture Decision

**A. What is solid:** four-layer separation with forbidden transitions; 8-field memory unit (all
fields consumed); evidence anti-contamination rules; proposal object shape; engineer-only
authority; recall six-case handling with abstention; Hindsight map with zero unresolved
dependencies; MVP stripped to the hypothesis; behavioral evaluation without invented metrics.
**B. Still unresolved:** language, hosting, key ownership, split confirmation, pre-seed rule
(Phase 0); signature/tag discrimination and abstention thresholds (spike-empirical); first domain
taxonomy values (post-spike).
**C. What should be removed:** `reflect()`, mental models, knowledge pages, auto-retain wrapper,
web UI, analytics from MVP scope (verified-but-unnecessary).
**D. What should be added:** interface-contract checkpoint; abstention calibration inside the
spike; seed-authoring overlap with loop build; render-template review for the trust surface (O2).
**E. Must verify before coding:** Phase 0 five items; spike steerability proof; taxonomy
discrimination on real seed drafts.
**F. Phase 0 exit criteria:** language, hosting, key owner, split + branches, pre-seed rule —
all five logged in `docs/decision-log.md`. No code before this.
**G. First implementation milestone:** joint Hindsight spike green — 5 cases retained, steerable
tag+query recall, calibrated abstention with zero hallucinated cases on empty/weak banks, A–E
probe behaviors logged — before any loop code is written.
