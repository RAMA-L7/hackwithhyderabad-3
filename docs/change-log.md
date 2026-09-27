# Project Change Log

## Purpose

This file records significant planning, architecture, collaboration, and implementation changes
for the HackwithHyderabad 3.0 repository. It is a human-readable summary; the Git history
(`git log`) is the authoritative technical record of file-level changes, and
`docs/decision-log.md` is the authoritative record of project decisions.

## Timeline

### Stage 1: Initial Planning

- Initial project-planning package created (`README.md`, `docs/` planning documents, `.gitignore`).
- Three candidate ideas documented: Engineering Debugging Memory Agent, AI Evaluation Memory Agent,
  Engineering Incident Memory Agent.
- No final project selected.
- Hindsight by Vectorize identified as the required persistent-memory technology.
- EGER (Evidence-Grounded Engineering Reasoning) explicitly treated as background inspiration only —
  not a validated framework, not a project dependency.
- No application code created.

### Stage 2: Planning Quality Review

Corrections recorded in `docs/decision-log.md` (dated 2026-09-27 in the log):

- Removed unsupported performance/time claims (e.g. "3 hours → 15 minutes", "45 min → 8 min",
  κ-metric improvements); replaced with neutral workflow-comparison and "proposed evaluation" language.
- Qualified Hindsight capability assumptions: architecture proposals explicitly marked
  "proposed application-level behavior" or "to verify against current Hindsight documentation".
- Clarified that EGER is personal research and not a validated dependency, while preserving the
  architectural principle: "Memory informs. Evidence verifies. Agent proposes."
- Made the Engineering Debugging MVP independent of live EDA tool integration
  (pre-seeded structured cases; tool parsing moved to optional extension).
- Added the core design question "What changes because the agent remembers?" to `README.md`
  and `docs/system-design-notes.md`.
- Added a verification checklist to `docs/hackathon-requirements.md`.
- Final project decision remained open.

### Stage 3: Mukul Collaboration

- Mukul joined the team (GitHub: `mukul-raii`).
- Mukul contributed two additional project ideas, recorded in `docs/decision-log.md`:
  - Idea 4: Deal Intelligence Agent (VC deal diligence memory).
  - Idea 5: Competitive Intelligence Agent (competitive-landscape / thesis-accuracy memory).
- Candidate set expanded from three to five; documented in `docs/project-ideas.md`,
  `docs/idea-comparison.md`, and `docs/demo-concepts.md`.
- Project selection remained open; discussion questions preserved in `docs/decision-log.md`.

### Stage 4: Git Collaboration Setup

- Local Git repository initialized on branch `main` (previously no `.git` existed).
- Initial commit (see below) containing the full planning package plus collaboration files.
- `.gitignore` extended with `*.token`, `.envrc`, and `.venv/` (planning docs remain tracked).
- `CONTRIBUTING.md` created (branch → PR → review workflow, secrets rule).
- `docs/collaboration-workflow.md` created (exact commands for Rama + Mukul).
- Secret check performed before committing: no API keys, tokens, `.env`, or credential files found
  (only the word "secret" in documentation guidance).
- Private GitHub repository created via authenticated GitHub CLI (no README/.gitignore/license
  initialized remotely, since the local repo already contains them).
- Local connected as `origin`; existing `main` branch pushed (no force-push, no reset, no re-commit).
- Collaborator invitation sent to `mukul-raii` (pending acceptance at time of writing).
- `docs/change-log.md` (this file) created and `README.md` Change Tracking section added,
  committed as a follow-up documentation commit and pushed.

Git record (from `git log`, dates in local timezone):

- `24ef5b2` — 2026-09-27 — "Initialize HackwithHyderabad planning repository"
  - 10 files, +2340 lines: `.gitignore`, `CONTRIBUTING.md`, `README.md`,
    `docs/collaboration-workflow.md`, `docs/decision-log.md`, `docs/demo-concepts.md`,
    `docs/hackathon-requirements.md`, `docs/idea-comparison.md`, `docs/project-ideas.md`,
    `docs/system-design-notes.md`.

## Current State

- Repository visibility: PRIVATE (`RAMA-L7/hackwithhyderabad-3`).
- Default branch: `main` (local `main` tracks `origin/main`).
- Commits so far: `24ef5b2` "Initialize HackwithHyderabad planning repository",
  then `d2a983b` "Document project history and collaboration setup" (both pushed to `origin/main`).
- Working tree state: clean at time of setup.
- Remote: `origin` → `https://github.com/RAMA-L7/hackwithhyderabad-3.git`.
- Collaboration status: `mukul-raii` accepted the invitation and has write access
  (verified 2026-09-27; no branches or commits from Mukul yet).
  Recommended branches: `rama-planning`, `mukul-ideas`; changes via Pull Request into `main`.
- Application code: none exists (planning-only repository).
- Project selection: open — five candidates under evaluation, no winner declared.

## Next Planned Changes

- Mukul accepts the collaborator invitation.
- Team discussion to select the final project (see open questions in `docs/decision-log.md`).
- Verify Hindsight API capabilities against official documentation before implementation.
- Confirm hackathon submission rules with organizers.

---

## Stage 5 — Documentation consistency cleanup (2026-09-27)

Scope: read-only inspection of `README.md` and all `docs/` planning files, followed by minimal
documentation edits. No project selected. No application code created. No dependencies installed.

### Files inspected

- `README.md`
- `docs/project-ideas.md`
- `docs/idea-comparison.md`
- `docs/demo-concepts.md`
- `docs/decision-log.md`
- `docs/change-log.md`
- `docs/hackathon-requirements.md`
- `docs/system-design-notes.md`
- `.gitignore`, `CONTRIBUTING.md`, `docs/collaboration-workflow.md` (spot-checked)

### Issues found and fixed

1. `docs/demo-concepts.md` contained two sections titled "Cross-Idea Demo Requirements".
   The second (after Idea 5) actually held the Generic-Chatbot-vs-Our-Demo comparison table.
   Renamed it to "Demo Differentiation Checklist" (restoring the original heading); no content removed.
2. Same table's "Demo metric" row read "Measured time / consistency / transfer", implying measured
   results. Changed to "Proposed workflow comparison (time / consistency / transfer)".
3. `docs/idea-comparison.md` had overlap analysis for Ideas 1↔3 but none for Ideas 4↔5.
   Added a concise "Overlap Between Deal Intelligence and Competitive Intelligence" section
   (workflow, memory unit, feedback loop, staleness, demo story; recommendation to keep separate).
4. Stale discussion questions updated: Q1 now lists all five domains; Q2 notes Mukul contributed
   Ideas 4–5 (preference TBD); Q5 now includes the synthetic deal/landscape data path.
5. Softened residual time phrasing in the Ideas 1↔3 overlap table ("solves in minutes…")
   to neutral workflow comparison language.
6. Attribution hardening (no Hindsight capability invented):
   - `docs/idea-comparison.md`: "Hindsight recall → application-ranked hypotheses" with an explicit
     note that ranking is application-level and recall capabilities are to be verified.
   - `docs/system-design-notes.md` (Mermaid): edge label "Ranked Memories" → "Recalled Memories".
   - `docs/project-ideas.md` (Idea 3 architecture): "ranked hypotheses (application-level ranking)".

### Issues intentionally left unresolved

- Hindsight API capabilities (schema, recall/query, auth, limits) remain unverified —
  tracked in `docs/hackathon-requirements.md` ("Verification Required Before Implementation").
- Open questions Q1–Q19 in `docs/decision-log.md` remain for team discussion.
- "Final Project Decision" in `docs/decision-log.md` remains empty.

### Confirmations

- EGER remains background inspiration only; no validated/published/required claims introduced.
- All five ideas remain candidates; no winner declared.
- No application code created; no dependencies installed or modified.

---

## Stage 6 — Hindsight capability verification and project-selection analysis (2026-09-27)

Scope: full re-read of all planning documents, official-source verification of Hindsight
capabilities, evidence-based analysis of all five candidates. No project selected.
No application code created. No dependencies installed.

### Documents reviewed

- `README.md`, `docs/hackathon-requirements.md`, `docs/project-ideas.md`,
  `docs/idea-comparison.md`, `docs/system-design-notes.md`, `docs/demo-concepts.md`,
  `docs/decision-log.md`, `docs/change-log.md`, `CONTRIBUTING.md`,
  `docs/collaboration-workflow.md`.

### Official sources reviewed

- https://hindsight.vectorize.io/ (Overview, v0.10 docs)
- https://hindsight.vectorize.io/developer/api/recall
- https://hindsight.vectorize.io/developer/api/memories
- https://github.com/vectorize-io/hindsight (README)

### Capabilities verified (selection)

retain/write with LLM extraction; 4-arm recall (semantic, BM25 keyword, graph, temporal) with
RRF + cross-encoder reranking and score outputs; `types` filtering; tag scoping (`tags_match`,
`tag_groups`); custom metadata + context labels; observations with provenance and
`prefer_observations`/`source_facts`; memory curation (PATCH edit/invalidate/restore, history);
contradiction reconciliation via consolidation; strict bank isolation; bank mission/directives/
disposition + `reflect()`; mental models/knowledge pages; Python/TypeScript/Go/CLI/MCP clients;
LangGraph/CrewAI/Vercel integrations; LLM wrapper; Docker/K8s/pip/embedded/Cloud deployment
(incl. Windows); webhooks; Memory Defense redaction. Full matrix in
`docs/hindsight-capability-verification.md`.

### Capabilities not verified (still unknown)

Rigid custom domain schemas; native TTL/expiry; server auth model; numeric rate limits; retain
idempotency/batching; list pagination details; export API; full audit logging. Design consequence:
schemas, ranking, staleness, and thresholds stay application-level — which removes (not adds)
dependency risk. No idea is blocked on an unverified capability under this reading.

### Five ideas analyzed

Per-idea analysis (workflow, memory unit, Hindsight dependency, complexity, data, demo,
evaluation, risks, unknowns, evidence needed) plus a neutral cross-idea table in
`docs/project-selection-analysis.md`. Shortlist for deeper discussion (not winners):
Ideas 3, 1, and 4, each with stated reasoning and reversible held-back notes on Ideas 5 and 2.
Biggest decision risks recorded: hosting/LLM-key model, pre-seeded-memory policy, Mukul's
preference, VC seed realism, eval/UI effort risks, unverified auth/limits.

### Confirmations

- No final project selected; "Final Project Decision" in `docs/decision-log.md` untouched.
- No application code created; no dependencies installed; no secrets introduced.
- No fabricated metrics; no Hindsight capabilities invented; EGER background-only.
- `README.md` Documentation Index extended with the two new analysis documents only.

---

## Stage 7 — Final project selection: Engineering Debugging Agent (2026-09-27)

- **Selection decision:** Idea 1 — Engineering Debugging Agent with persistent memory using Hindsight.
  Recorded in `docs/decision-log.md` ("Final Project Decision"). Ideas 2, 3, 4, and 5 were
  considered but not selected; their analyses are preserved for reference.
- **Team members involved:** Rama Krishna Ketha and Mukul Rai (joint decision; changeable only by
  explicit agreement of both members).
- **Key selection reasoning:** strongest team-domain fit (Rama's engineering-debugging background
  supports realistic seed cases); narrow single-workflow/single-persona MVP with a visible
  before/after memory demonstration; core loop maps to verified Hindsight capabilities with the
  case schema as an application-level convention; no live EDA tool integration required for MVP.
- **New planning documents:** `docs/final-project-definition.md` (problem, persona, workflow, memory
  unit, Hindsight integration per verified capabilities, MVP scope, demo story, evaluation plan,
  success criteria), `docs/implementation-plan.md` (phased plan, no code), `docs/team-task-split.md`
  (proposed split only, to be confirmed by both members).
- **Application implementation has not started.** No source files, dependencies, or secrets added.
  This stage is architecture and implementation planning only.
- **Next planned phase:** confirm task split → Hindsight spike (local setup + seed retain/recall) →
  architecture sign-off → phased implementation per `docs/implementation-plan.md`.
- **Unchanged:** EGER remains background inspiration only; no validation claims. All prior stage
  records preserved.

---

## Stage 8 — Implementation readiness review (2026-09-27)

Scope: readiness review of the selected Engineering Debugging Agent before any code.
Reconstructed final requirements, MVP workflow, system boundary, agent loop, minimum memory
unit (8 necessary fields; lessons-text and blobs deferred), minimum evidence model
(engineer-supplied, deterministic), memory/evidence/proposal/authorization separation,
Hindsight integration restricted to verified capabilities, first demo scenario (reuses the
documented hold-causes-setup-pessimism narrative, no new numbers), technical decisions
triaged (5 must-decide-now, 5 deferrable, 7 not-for-MVP), workstream dependencies, phased
sequence with an added interface-contract checkpoint, per-phase acceptance criteria, risks,
and documentation notes. New document: `docs/implementation-readiness-review.md`.
Verdict recorded there: **NOT READY TO IMPLEMENT** — five Phase 0 decisions (language,
hosting, LLM key ownership, split confirmation, pre-seeded-memory rule) must be logged first;
recommended first task after that is the joint Hindsight spike.
No application code written; no dependencies installed; no secrets added; final project
decision untouched; EGER background-only.
