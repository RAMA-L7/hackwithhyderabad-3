# Domain-Neutral System Design

**Status:** Design direction — no implementation, no domain selected.
**Date:** 2026-09-27.
**Reference:** `docs/final-project-definition.md` (selected scope),
`docs/hindsight-capability-verification.md` (proven building blocks),
`docs/implementation-readiness-review.md` (readiness verdict: not ready pending Phase 0),
`docs/system-design-notes.md` (cross-cutting principles).

## 1. System Objective

An engineering debugging agent that learns from previous debugging cases while distinguishing
historical memory from current evidence. The product's value is a behavioral difference: with
relevant memory, the investigation follows a different, better-informed path than without it.
The architecture is independent of the eventual engineering domain — the domain is a
configuration and input to the system, not the architecture itself. Candidate instantiation
domains (examples only, none selected): software debugging, DevOps incidents, hardware
debugging, VLSI debugging, data-engineering failures, ML-engineering failures, other
engineering investigation workflows.

## 2. Product Boundary

The product is the memory-driven investigation loop, not any single domain's tooling. Inside
the boundary: problem intake, case normalization, historical recall, evidence comparison,
hypothesis formation, proposal generation, verification, resolution capture, and retention.
Outside the boundary (until explicitly decided): live tool integrations, autonomous remediation,
multi-domain operation, multi-user collaboration, production hardening. The boundary is drawn
around the learning mechanism so that swapping the domain changes inputs, not architecture.

## 3. Core Information Flow

```
Engineering Problem
        ↓
Case Normalization
        ↓
┌───────────────────────┐
│                       │
│ Historical Memory     │
│ Hindsight             │
│                       │
└───────────┬───────────┘
            │
            │ historical context
            ▼
Current Evidence ────────┐
                         │
                         ▼
                 Investigation Agent
                         │
                         ▼
                 Investigation Plan
                         │
                         ▼
                    Verification
                         │
                         ▼
                     Resolution
                         │
                         ▼
                  Outcome / Learning
                         │
                         ▼
                     Retention
                         │
                         ▼
                    Hindsight
```

This diagram describes system boundary and information flow, not implementation. Note the two
independent inputs to the agent (historical context, current evidence) and the single exit back
to memory (retained outcome). Nothing flows from memory into evidence; nothing flows from
proposal into memory except through a verified outcome.

## 4. System Components

- **Intake:** accepts an engineering problem description plus environment identifiers; performs
  no reasoning, only structuring.
- **Normalizer:** maps raw input onto the domain-neutral case shape (Section 5) via a
  domain adapter (Section 11). Deterministic where possible (field extraction, tag assignment);
  LLM-assisted only where free text must be condensed.
- **Memory Interface:** the sole path to historical cases (Section 12). The rest of the system
  never touches Hindsight directly.
- **Evidence Interface:** the sole path to current-case facts. For the MVP this is
  engineer-supplied input; later it may wrap tool outputs. Same shape discipline as memory,
  but a separate store with a separate trust level.
- **Investigation Agent:** synthesizes memory + evidence into hypotheses and proposals.
  The only LLM-driven reasoning component besides normalization.
- **Verification Gate:** compares proposal preconditions against current evidence and records
  the engineer's relevance confirmation. A proposal cannot proceed to "recommended action"
  without passing this gate.
- **Outcome Capture:** records what was tried, what worked, what failed, and the engineer's
  verification notes, then routes the completed case to retention.

## 5. Memory Model

The memory unit is a reusable debugging case, never conversation history:

```
DebugCase
├── problem_signature   # short canonical description + query seed (essential)
├── symptoms            # observed failure, measurements (essential)
├── environment         # domain-populated identifiers, e.g. service/version,
│                       # deployment/infra, or design/corner/tool (essential)
├── observed_evidence   # references/links to reports, logs, diffs (essential as refs)
├── investigation_trace # ordered steps tried (essential — the "path" being improved)
├── failed_approaches   # what failed + why (essential — powers warn-against-retry)
├── root_cause          # as verified by the engineer (essential)
├── resolution          # what fixed it + supporting evidence (essential)
├── outcome             # resolved / workaround / escalated (essential)
└── verification_notes  # confirmed/denied relevance of used recalls (essential)
```

Essential means the MVP loop consumes the field (recall, ranking, verification, or demo).
Optional for later: lessons-learned free text, suspected-cause lists beyond the trace,
confidence scores. Domain-specific detail lives only inside `environment` values and tag
vocabularies — e.g. a software case carries service/version, a VLSI case carries
design/corner/tool, a DevOps case carries deployment/infrastructure — while the abstraction,
lifecycle, and interfaces stay identical across domains.

## 6. Evidence Model

Current evidence is everything known about the case at hand, right now: the problem description
as given, environment identifiers, pasted measurements, and any live readings. Rules:

- Every evidence item carries source and capture point.
- Evidence is never written to the historical store except as part of a completed, verified outcome.
- Absence of evidence is itself recorded ("corner unknown") rather than filled in from memory.
- The verification gate in Section 8 may only consult this model plus recalled-case metadata —
  never recalled conclusions.

## 7. Agent Proposal Model

A proposal is a ranked list of hypotheses, each carrying: the hypothesis, the memory(s) supporting
it (with case IDs and original conditions), the refutation conditions (what evidence would rule it
out), and the recommended next investigative step. Proposals are explicitly labeled as untrusted
until verified. Ranking is application-level (outcome history × environment similarity × recency),
computed over recalled candidates — never delegated to Hindsight as an assumed capability.

## 8. Authorization Boundary

| Layer | May decide? |
|-------|-------------|
| Historical memory | No — advisory context only |
| Current evidence | No — ground to check against |
| Agent proposal | No — untrusted until verified |
| Engineer authorization | **Yes — the only deciding layer** |

Forbidden transitions (enforced by design, reviewable in code review):
historical memory → current evidence; agent proposal → verified fact; historical solution →
automatic action. The CLI must render all four layers distinctly so a reviewer can see which is
which at every step.

## 9. Recall and Abstention

Recall answers only "have we seen something similar before?" — never "this proves the cause."
Three outcomes, each with a defined path:

1. **Relevant historical case** → attach as investigation context with original conditions shown.
2. **Weak or partial similarity** → present cautiously, labeled as a possible reference, with the
   specific mismatches (environment, era, outcome) stated alongside.
3. **No meaningful match** → explicit abstention: proceed on the generic path with a visible
   "no relevant memory" signal. Never force memory into the response; never invent a case.

Abstention is tuned empirically (`min_scores` floors are per-query relative signals, per the
verification document), and the abstention path is a tested workflow, not an error branch.

## 10. Investigation Lifecycle

```
OBSERVE → NORMALIZE → RECALL → COMPARE → FORM HYPOTHESIS → CHECK CURRENT EVIDENCE
  → PROPOSE INVESTIGATION → VERIFY → RESOLVE → RETAIN USEFUL OUTCOME
```

Retention is selective: only completed cases with verified outcomes (including informative
failures) are retained. Raw interactions, unverified hypotheses, and duplicate-without-enrichment
cases are not. Learning means the bank's future recall returns sharper context — the mechanism
is richer evidence plus consolidation, not a model update.

## 11. Domain Adapter Concept

```
Core Debugging Engine
  ├── Memory Interface
  ├── Evidence Interface
  ├── Investigation Interface
  └── Outcome Interface
            │
            ▼
     Domain Adapter (one per domain, later)
```

The adapter translates domain inputs into the neutral case shape (field mappings, tag taxonomy,
environment facets, display labels) and translates neutral proposals back into domain language.
The core engine — lifecycle, verification, retention policy, abstention — never changes when the
domain changes. No adapter is implemented in the MVP; the first instantiation hard-codes one
adapter's worth of taxonomy as configuration, structured so a second domain plugs in without
core changes. Adapter selection is a separate, later decision — this document does not pre-select it.

## 12. Hindsight Integration Boundary

Conceptual interface owned by the application (not implemented yet):

```
MemoryStore
├── retain()      # write one normalized case
├── recall()      # query + tags + types, returns facts with provenance
├── update()      # PATCH corrections to a retained case
└── invalidate()  # reversible soft-retire of stale/superseded cases
```

Everything behind this interface may use only verified capabilities (recall parameters, curation
API, bank isolation, Memory Defense, scores as relative signals). The orchestrator, ranking,
schema validation, staleness policy, and verification gate live outside it, so the memory
backend could be re-examined without touching agent logic. No undocumented Hindsight behavior
is assumed anywhere in this design.

## 13. Domain-Neutral Spike

A conceptual five-case probe set (data authored at spike time, not now):

- **Case A:** similar problem, same environment type, known resolution → must retrieve as useful context.
- **Case B:** similar symptoms, different root cause → must surface as reference with the difference stated, never as identical.
- **Case C:** unrelated problem → must be ignored (or abstain if nothing else matches).
- **Case D:** same root cause, different environment → must be used carefully, with the environment mismatch shown.
- **Case E:** insufficient evidence → must abstain explicitly.

The spike passes only if all five behaviors are observed and logged. It tests the memory mechanism
before any agent implementation begins, in whichever single domain the spike cases are written —
the mechanism under test is domain-independent.

## 14. MVP Boundary

In: one debugging workflow, one engineer persona, persistent debugging memory, current-evidence
input, memory recall, investigation proposal, evidence-aware response, outcome retention, visible
before/after behavior. Out: multiple domains, autonomous production changes, automatic remediation,
enterprise integrations, live EDA integrations, incident-management integrations, multi-agent
orchestration, advanced analytics, broad autonomous debugging. Additions require joint
decision-log entries.

## 15. Future Extension Points

Second domain adapter behind the same interfaces; evidence adapters wrapping live tool outputs;
team namespaces (separate banks + attribution); outcome-driven ranking weights learned from
retained verification notes; knowledge-page summaries of consolidated patterns; evaluation harness
over the retained bank. Each is a separate decision with its own design review — none is promised.

## 16. Design Risks

1. Signature/tag design may not discriminate cases until spike data exists — mitigated by the
   domain-neutral spike preceding schema freeze.
2. Abstention thresholds are uncalibrated until real recall scores are observed.
3. Normalizer quality bounds everything downstream; a weak normalizer makes recall look like a
   Hindsight failure.
4. First-domain choice still pending — the neutral design holds regardless, but seed authorship
   cannot start until it is made.
5. Phase 0 blockers from the readiness review (language, hosting, key, split, pre-seed rule)
   still gate all implementation, including the spike environment.

## 17. Open Design Questions

The fifteen design questions from the planning brief, with current positions: smallest useful case
(Section 5 — 8 essential fields); similarity (signature + tags + semantic, spike-tuned); relevant-
but-unsafe (environment/era mismatch — Section 9 outcome 2); abstention (Section 9, empirically
tuned); memory vs evidence contents (Sections 5–6); retention trigger (verified outcome only,
Section 10); outdated/conflicting handling (invalidate + consolidation + human backstop, per
verification doc); domain entry (adapter config, Section 11); domain-independence (lifecycle,
gates, interfaces — everything except taxonomy/display); deterministic vs LLM (normalizer
deterministic-first, agent LLM-driven, gates deterministic); validation points (retain-time
schema check, pre-proposal env comparison, pre-retain outcome check); user visibility (four
layers rendered distinctly); smallest demo (Section 9 + readiness doc Section 9); empirical
assumptions (spike Sections 13 + readiness acceptance criteria).

## 18. Architecture Acceptance Criteria

- [ ] Core lifecycle, memory model, and interfaces contain no domain-specific concepts.
- [ ] Swapping the domain taxonomy requires no change to lifecycle, gates, or retention policy.
- [ ] Memory, evidence, proposal, and authorization are structurally separated (four stores/layers).
- [ ] Abstention path exists and is tested.
- [ ] Hindsight sits behind `MemoryStore`; all calls map to verified capabilities.
- [ ] No recalled solution can reach execution without a logged engineer authorization.
- [ ] Spike design (A–E) is executable against the interfaces as specified.
- [ ] MVP boundary from Section 14 is respected; extensions are listed, not built.
