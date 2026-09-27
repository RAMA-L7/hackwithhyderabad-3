# Engineering Debugging Agent

**Status:** Selected project (decided 2026-09-27 by Rama Krishna Ketha and Mukul Rai).
Implementation has not started.
**Reference:** Decision in `docs/decision-log.md`; capability evidence in
`docs/hindsight-capability-verification.md`; prior candidate analysis in
`docs/project-selection-analysis.md`.

## Problem

Hardware/software engineers repeatedly encounter similar failure modes — timing violations,
signal-integrity issues, configuration mismatches, race conditions — but the institutional
knowledge of how each was diagnosed and fixed lives in ticketing systems, wikis, chat history,
or individual memory. Each debugging session therefore starts near zero: engineers re-derive
investigation order, retry approaches that previously failed in the same context, and rediscover
known root causes. Past investigation traces, failed attempts, and verified fixes are rarely
queryable at the moment they would change what an engineer tries next.

## Target User

The initial persona is a single debugging engineer working planned (non-incident) debugging
sessions — e.g. a VLSI physical-design/verification engineer, embedded engineer, or backend
engineer diagnosing test failures, timing violations, or functional bugs. Out of scope for the
MVP: on-call incident response, team-wide knowledge management, and multi-persona workflows.

## Core Workflow

```
Engineering issue
  → initial investigation (agent has no relevant memory: generic checklist)
  → memory retrieval (Hindsight recall by problem signature + tags)
  → evidence/context gathering (current environment, reports, code state)
  → agent reasoning (prioritized hypotheses from recalled cases)
  → proposed diagnosis/actions (agent proposes; engineer decides)
  → resolution (engineer executes and confirms)
  → outcome retained as memory (full case incl. verification notes)
```

The loop is only complete when the outcome — including what failed — is retained for the
next similar issue.

## Why Persistent Memory Matters

What changes when the system remembers previous debugging cases:

- Investigation starts from a prioritized order grounded in past successes instead of a
  generic checklist.
- Approaches that previously failed in the same context are surfaced as warnings instead of
  being retried.
- Verified root-cause patterns are proposed earlier, with links to the evidence that
  supported them last time.
- Junior engineers gain access to reasoning traces they did not personally experience.

Without memory the agent is a checklist generator; with memory it is an experience-informed
investigation partner. That behavioral difference — not personalization — is the value.

## Memory Unit

One debugging memory represents a single completed debugging case: one problem signature,
one environment, one investigation trace, and one verified outcome. Similar cases are linked
by tags and signatures; they are not merged, so conflicting experiences stay individually
auditable.

## What Should Be Remembered

Proposed application-level fields for one case (convention, not a Hindsight-enforced schema):

- issue symptoms (observed failure, measurements)
- relevant context (subsystem, configuration, tool and version, corner/conditions)
- observed evidence (references/links to reports, logs, diffs — not raw blobs)
- suspected causes (hypotheses considered, with rationale)
- confirmed root cause (as verified by the engineer)
- attempted fixes, including unsuccessful approaches and why they failed
- successful resolution and its supporting evidence
- environment/version information (tool, PDK/library, config identifiers)
- outcome classification (resolved / workaround / escalated / deferred)
- lessons from the case (actionable notes for future investigations)
- engineer verification notes (confirmed or denied relevance of any recalled case used)

Field presence and validation are enforced by application code at retain time. Hindsight stores
the case content plus flat metadata/tags/context; it does not enforce a rigid domain schema
(see "Hindsight Integration" and the verification document).

## What Must Not Be Treated as Fact

Recalled historical information is not proof about the current issue. A past fix that worked
under a different tool version, configuration, or corner may be wrong or harmful now. The agent
must therefore:

- present recalled cases as suggestions with their original conditions attached;
- require an explicit environment/context comparison before proposing a recalled fix;
- never apply a recalled action autonomously — the engineer confirms relevance and decides;
- surface contradicting cases (same symptoms, different root cause) rather than hiding them;
- mark low-relevance or stale recalls as such instead of omitting the uncertainty.

## Agent Workflow

Four separated layers; confusion between them is a design defect:

1. **Memory** — recalled past cases with provenance (case ID, original environment, evidence links).
2. **Current evidence** — live state of this issue (symptoms, environment, reports, code), gathered now.
3. **Agent proposal** — prioritized hypotheses and next steps synthesized from 1 + 2, each labeled
   with which memory supports it and what verification would confirm or refute it.
4. **Final human decision** — the engineer accepts, modifies, or rejects the proposal; the decision
   and its basis are recorded as part of the retained outcome.

## Hindsight Integration

Only capabilities verified in `docs/hindsight-capability-verification.md` (official docs, 2026-09-27):

- `retain()` writes each case (content + context label + flat metadata/tags) to an isolated bank;
  LLM extraction derives searchable facts/entities/dates automatically.
- `recall()` retrieves by natural-language query with verified parameters directly matching our
  needs: `tags`/`tags_match` for environment/outcome filtering, `types` for fact categories,
  `temporal_window`/`query_timestamp` for recency-aware ranking, `max_tokens` budget control,
  and `min_scores` floors for abstention ("no confident match → fall back to generic checklist").
- Scores (`final`, `reranker`, `semantic`, `keyword`) are per-query relative signals for ordering;
  any abstention threshold must be tuned empirically during implementation, not assumed.
- Case updates (e.g. correcting a root cause, marking a case superseded) use the verified memories
  curation API (PATCH edit/invalidate/restore); in-stream contradictions are additionally reconciled
  by Hindsight's consolidation, with human curation as the backstop.
- Bank isolation gives one clean namespace for the debugging memory; Memory Defense redaction can
  be enabled on the bank as a secrets/PII guardrail.
- Python and TypeScript clients are verified (implementation language still to be decided);
  local Docker deployment is verified (hosting choice still to be decided).

Explicitly not assumed: rigid domain schemas, native TTL/expiry, server auth model, numeric rate
limits, retain idempotency. Staleness handling, ranking by outcome/environment, and schema
validation are application responsibilities.

## MVP Scope

- One engineering debugging workflow (planned debugging sessions; single failure domain to start,
  e.g. timing violations).
- One primary user persona (debugging engineer).
- One clear memory loop: describe issue → recall → verify → propose → resolve → retain.
- Pre-seeded structured cases in Hindsight (no live EDA tool integration).
- CLI-first interface.
- One clear demonstration of improvement over repeated cases: the second similar issue follows a
  visibly different, memory-informed investigation path.

## Out of Scope

- Live EDA/observability tool integrations (report parsers, log ingestion, ticket-system sync).
- Multi-persona or team-collaboration features (shared namespaces, attribution, permissions).
- Automatic fix execution — the agent proposes, the engineer acts.
- Multi-domain debugging (clocking, power, DFT, software) beyond the single starting domain.
- Production hardening (auth, scale, monitoring, backup) beyond what the demo needs.
- Content deliverables (article, video, posts) until the MVP behavior is demonstrated.

## Demo Story

Before/after memory demonstration, no numerical claims:

1. **Before:** engineer describes a first issue; the bank holds no relevant case; the agent offers a
   generic investigation checklist; the engineer works through it, finds the root cause, and the
   full case (including a failed approach) is retained — memory creation visible on screen.
2. **After:** a second, similar issue arrives; the agent recalls the retained case, warns against
   the previously failed approach, and proposes the verified hypothesis first — with an explicit
   environment comparison and verification step before the engineer acts.
3. **Contrast:** side-by-side investigation paths (generic order vs memory-prioritized order) with
   the verification gate visible in both directions (recall used; recall absent).

## Evaluation Plan

Compare behavior with and without useful historical memory, using only observable in-demo evidence:

- Recall precision: fraction of recalled cases the engineer marks relevant.
- Recall utility: fraction of relevant recalls that changed the next investigation step.
- Failed approaches avoided: count of warned-against approaches the engineer did not retry.
- Verification discipline: every applied recalled fix traceable to an explicit relevance check.
- Qualitative review: does the second investigation read as informed by the first?

No baseline numbers, targets, or improvement percentages are claimed in advance. If quantitative
comparison is pursued, the method (same engineer, similar cases, with/without memory) and raw
observations will be documented alongside the result.

## Open Technical Questions

- Implementation language and Hindsight hosting choice (local Docker vs embedded vs Cloud; whose
  LLM API key funds retain/recall calls).
- Which signature + tag fields actually discriminate cases (to be discovered during seeding).
- Abstention threshold calibration for `min_scores` (empirical, during implementation).
- CLI interaction design (command shape, recall panel rendering, verification prompts).
- Whether organizer rules permit pre-seeded memory or require live learning in the demo.
- Exact demo domain within debugging (timing violations vs broader) and seed-case count.

## Success Criteria

Observable, functional, without fabricated benchmarks:

- A first issue can be worked end-to-end and its full case (including a failed approach) is
  retained and inspectable in Hindsight.
- A second similar issue triggers recall of the first case with its original conditions attached.
- The agent's proposal for the second issue visibly differs from its generic first-issue behavior
  and cites the recalled case.
- No recalled fix is applied without an explicit, logged relevance check by the engineer.
- Contradicting or irrelevant recalls are surfaced as such, never silently applied.
- The full loop runs live (retain → recall → verify → propose → resolve → retain) in the demo
  environment without errors.
