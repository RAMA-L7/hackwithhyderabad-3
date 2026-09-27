# Implementation Readiness Review

**Status:** Review only — no application code written, no dependencies installed.
**Date:** 2026-09-27.
**Reference:** `docs/final-project-definition.md` (selected scope),
`docs/hindsight-capability-verification.md` (proven building blocks),
`docs/implementation-plan.md` (phased plan), `docs/team-task-split.md` (proposed split),
`docs/decision-log.md` (open questions Q1–Q19).

## 1. Final Project Definition

Build an Engineering Debugging Agent with persistent memory using Hindsight (decided 2026-09-27
by Rama Krishna Ketha and Mukul Rai). A debugging engineer describes an issue; the agent recalls
past debug cases from Hindsight, verifies them against current evidence, proposes a prioritized
investigation; the engineer decides and the outcome — including failed approaches — is retained.
Hindsight is a core architectural requirement. EGER remains background inspiration only and is
not a dependency. MVP is CLI-first, pre-seeded cases, no live EDA integration, no auto-fix.

## 2. MVP Workflow

Describe issue → Hindsight recall (signature + tags) → gather current evidence → environment
comparison → ranked proposal with provenance → engineer verifies and decides → engineer executes →
outcome (with verification notes) retained. The abstention path — no confident recall → generic
checklist, no hallucinated cases — is a first-class workflow, not an error case.

## 3. System Boundary

In scope: CLI, agent orchestrator, thin Hindsight layer, application-level case schema +
validation, verification gate, seed-case loader, demo logging.
Out of scope (MVP): EDA/report parsers, ticket/log ingestion, multi-user features, auto-fix
execution, second domain, production hardening, content deliverables.
External dependencies: a running Hindsight instance (local Docker, embedded, or Cloud — undecided)
and an LLM API key funding retain/recall calls (owner undecided). Both are blocking decisions.

## 4. Agent Loop

```
Input (issue description + environment context)
  → Recall relevant debugging memory (Hindsight recall: query + tags + types)
  → Analyze current evidence (symptoms, environment; engineer-supplied for MVP)
  → Generate investigation proposal (application-ranked hypotheses, each citing its memory)
  → Verify against available evidence (env comparison; engineer confirms relevance)
  → Produce response (proposal + provenance + what would refute each hypothesis)
  → Retain useful outcome (full case incl. failed approaches + verification notes)
  → Improve future investigations (richer bank → better recall → sharper proposals)
```

## 5. Memory Model

Minimum memory unit — one completed debugging case. Fields genuinely necessary for the MVP loop
(each field must earn its place by being used in recall, ranking, verification, or the demo):

- problem signature (text query seed + tag facets) — used by recall
- symptoms (observed failure, measurements) — used by recall and display
- environment/version identifiers (tool, version, config, corner) — used by verification gate
- investigation trace (ordered steps tried) — displayed as the "path" being improved
- failed approaches + why (required: powers the warn-against-retry behavior)
- confirmed root cause + supporting evidence references (required: powers the propose-first behavior)
- outcome classification (resolved / workaround / escalated)
- engineer verification notes (required: ground truth for recall quality + evaluation)

Deliberately deferred: lessons-learned free text (nice display, unused by logic), suspected-causes
lists beyond what the trace already shows, commit/report blobs (references only). Representation:
one retain call per case; case fields carried as content + flat metadata/tags/context convention
(Hindsight enforces no rigid schema — verified limitation, designed around).

## 6. Evidence Model

Minimum evidence for the MVP is engineer-supplied and deterministic: the issue description as
given, the stated environment identifiers, and any pasted measurements. No live tool output is
required. Rules: evidence is always labeled with source and capture point; recalled memory is
never stored as evidence; verification compares recalled-case environment fields against current
evidence field-by-field, and any mismatch is shown, not hidden.

## 7. Memory vs Evidence vs Proposal

| Layer | Content | Source | May it decide? |
|-------|---------|--------|----------------|
| Memory | Past cases with provenance (ID, original env, evidence links) | Hindsight recall | No — advisory only |
| Evidence | Current symptoms, environment, measurements | Engineer input (MVP) | No — it is the ground to check against |
| Agent proposal | Ranked hypotheses citing memories + refutation conditions | Orchestrator synthesis | No — proposal only |
| User authorization | Accept / modify / reject + relevance confirmation | Engineer | **Yes — the only deciding layer** |

Confusing any row with another is a design defect; the CLI must render all four distinctly.

## 8. Hindsight Integration

Per `docs/hindsight-capability-verification.md` (official docs reviewed 2026-09-27), the MVP uses
only: `retain()` (content + context + metadata/tags, one bank); `recall()` with `tags`/`tags_match`,
`types`, `max_tokens`, empirically tuned `min_scores` abstention; memories PATCH edit/invalidate
for corrections and staleness; bank isolation; opt-in Memory Defense redaction. Ranking,
schema validation, staleness policy, and abstention thresholds are application responsibilities.
Assumed nowhere: rigid schemas, native TTL, auth model, rate limits, idempotent retain
(seed loader must be idempotent by construction until verified).

## 9. First Demo Scenario

Same hold-causes-setup-pessimism pattern already documented in `docs/project-ideas.md` (Idea 1)
and `docs/demo-concepts.md` — reused unchanged, no new numbers:
issue A on one path (empty bank → generic checklist → engineer finds hold violation → case with a
failed approach retained, visibly) → issue B on an adjacent path (recall surfaces case A with
original conditions → failed-approach warning + check-hold-first proposal → explicit env comparison
→ engineer verifies and resolves → outcome retained). Pass criterion: the two investigation paths
are visibly different and every use of recall is traceable to a logged relevance check.

## 10. Technical Decisions

**Must decide now (blocks all build):**
1. Implementation language (Python vs TypeScript — both clients verified).
2. Hindsight hosting (local Docker vs embedded vs Cloud).
3. LLM API key ownership and budget for retain/recall calls.
4. Task-split confirmation (both members) and working branches.
5. Organizer rule: pre-seeded memory allowed vs live-learning-only (can invalidate the demo design).

**Can defer (decide during build):**
- Discriminating signature/tag fields (discovered while seeding).
- `min_scores` abstention threshold (calibrated in the spike).
- CLI command shape and recall-panel rendering.
- Exact demo domain breadth and seed count (within single-domain bound).
- Decision deadline/tiebreaker mechanics (needed before selection disputes, not before code).

**Not required for MVP:** EDA parsing, second UI, multi-user, auto-fix, production hardening,
content topics, export/backup, monitoring.

## 11. Implementation Dependencies

Chain: Phase 0 decisions → Hindsight standing → spike results → schema freeze → seed authoring →
agent loop → CLI → tests/eval → demo. The schema cannot freeze before the spike shows what is
recall-steerable; the agent loop cannot be built before schema + bank exist; the CLI needs the
loop's proposal/verification shapes; evaluation needs a working loop plus seeded bank. Seed
authoring (Rama's domain input) is the longest lead item and should start the moment the spike
confirms steerability — it must not wait for the CLI.

## 12. Rama/Mukul Workstreams

Proposed split in `docs/team-task-split.md` is sound in structure (Rama: Hindsight layer +
schema/seeds; Mukul: agent loop + CLI; shared: tests/demo/hygiene) but **unconfirmed**, which is
itself a must-decide-now item. Dependency risks if confirmed as-is:
- Mukul's agent loop needs Rama's bank + schema → agree the schema interface (field names,
  tag taxonomy) in Phase 1 before parallel work, or Mukul blocks.
- Rama's seeds need the recall behavior the spike reveals → spike is joint work, not solo.
- CLI needs proposal/verification shapes from the loop → Mukul should freeze those shapes early
  or pair on them; otherwise CLI rework.
- Mitigation: a 30-minute interface contract (case JSON shape, function signatures, bank ID/tags)
  signed off by both before Phase 2/4 parallelize.

## 13. Implementation Phases

Adopt `docs/implementation-plan.md` Phases 0–8 unchanged, with two adjustments from this review:
(a) insert an explicit **interface-contract checkpoint** at the end of Phase 1 (case JSON, Hindsight
layer functions, proposal/verification shapes) as the precondition for parallel Phase 2–5 work;
(b) start seed authoring immediately after the spike confirms steerability (early Phase 3 overlap
with Phase 4), since seeds are the critical path to a demonstrable loop.

## 14. Acceptance Criteria

- Phase 0: language, hosting, key owner, split, branches, and organizer pre-seed rule all logged
  in the decision log. (Observable: log entries exist.)
- Spike: 5 sample cases retained; tag+query recall ordering demonstrably steerable; abstention
  observed on an empty bank with zero hallucinated cases. (Observable: logged session.)
- Schema: validator rejects malformed cases; taxonomy documented; 10–15 seeds cover 3–4 patterns.
- Loop: end-to-end run against seeded bank without errors; every applied recalled fix has a logged
  relevance check; contradiction case surfaces both candidates.
- CLI: full demo narrative completable from the CLI alone by someone other than its author.
- Tests: Hindsight-layer smoke tests, golden-path loop test, abstention test — all green.
- Demo: before/after paths visibly differ; verification gate visible both ways; no sensitive data.
- Content: articles/posts describe only built, observed behavior.

## 15. Risks

1. **Undecided foundations** (language/hosting/key/split) — any code before Phase 0 risks rework.
2. **Pre-seed rule** — a live-learning-only requirement invalidates the demo design; ask organizers first.
3. **Similarity quality on terse signatures** — may need tag-heavy recall fallback; spike will reveal.
4. **Seed realism burden** — 10–15 convincing cases is the largest labor block; start early.
5. **Abstention tuning** — scores are per-query relative; a fixed threshold will misfire until calibrated.
6. **Scope creep vectors** — EDA parsing, second domain, web UI, auto-fix; each needs a joint
   decision-log entry per project rules.

## 16. Open Questions

Carried forward (owners in `docs/decision-log.md`): language, hosting, key ownership, split
confirmation, branch plan, pre-seed policy, live-demo requirements, deadlines, signature/tag
discrimination, abstention calibration, CLI design, demo domain breadth, tiebreaker. New from this
review: interface-contract contents (case JSON, layer functions, proposal shapes) and whether the
spike runs jointly or solo.

## 17. Documentation Cleanup Required

None blocking. Minor notes (not acted on here, per minimal-change rule): `docs/demo-concepts.md`
still contains illustrative "minutes/hours" phrasing inside Idea 1/3 demo narratives from the
candidate phase — acceptable as narrative illustration, but the team should not quote it as a
claim; `docs/idea-comparison.md` and candidate-phase docs correctly remain historical records.
No contradictions found between the selected-project docs (`final-project-definition`,
`implementation-plan`, `team-task-split`) and the verification document.

## 18. Implementation Readiness Verdict

**NOT READY TO IMPLEMENT** — architecture, memory model, evidence model, demo scenario, phase plan,
and acceptance criteria are all defined and consistent, but five must-decide-now items from
Section 10 (language, hosting, LLM key ownership, split confirmation, pre-seeded-memory rule)
are unresolved, and writing code before them risks rework. The project becomes READY the moment
Phase 0 exit criteria are logged in the decision log. Recommended first implementation task after
that: the Hindsight spike (retain ~5 sample cases → tag+query recall → abstention check), run
jointly, results logged before any loop code is written.
