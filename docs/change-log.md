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

---

## Stage 9 — Domain-neutral system design (2026-09-27)

Scope: design-direction document only. Established the product as a domain-neutral debugging
agent (domain = configuration/input, not architecture), with the memory/evidence/proposal/
authorization separation, an 8-field essential memory model, three-outcome recall with explicit
abstention, the OBSERVE→…→RETAIN lifecycle, a domain-adapter concept, a Hindsight
`MemoryStore` abstraction limited to verified capabilities, a five-case (A–E) spike design,
MVP boundary, extension points, risks, and architecture acceptance criteria. New document:
`docs/domain-neutral-system-design.md`. Reviewed existing docs for contradictions: none
blocking found — `docs/final-project-definition.md`'s "e.g. timing violations" reads as an
example (domain still unfixed); `docs/team-task-split.md` background references are
proposed-only and pending confirmation. No existing documents modified in this stage.
No application code written; no dependencies installed; no secrets added; final project
decision untouched; EGER not invoked as a dependency (no attribution in the new design).

---

## Stage 12 — LLM provider architecture (2026-09-27)

Scope: design only. Recorded the team decision (Python; primary OpenRouter Space Bunny Alpha;
fallback DeepSeek V4.1 Flash via Baseten) and specified a provider-independent `LLMAdapter`
(agent talks only to the adapter; provider code isolated per adapter; model/URL/keys from
environment, never hardcoded; switching providers touches no memory/recall/evidence/
verification/workflow code). Documented explicit observable fallback (fail over on timeout/
unavailable/rate-limited; auth errors raise instead; missing fallback capabilities raise rather
than degrade silently) and structured-output preservation via validation gates. Existence
check (web, 2026-09-27): Space Bunny Alpha exists on OpenRouter but had a recent provider-side
outage — stability must be re-verified at build time; DeepSeek V4.1 Flash on Baseten exists
with an OpenAI-compatible endpoint. Open verification items (structured-output modes,
tool-calling compatibility, timeouts/limits/errors, key provisioning, retention posture)
assigned to the spike. New document: `docs/llm-provider-architecture.md`; decision logged in
`docs/decision-log.md`. No application code written; no dependencies installed; no secrets
added; final project decision untouched; EGER untouched.

---

## Stage 13 — Phase 0 reconciliation and provider verification (2026-09-27)

Scope: planning reconciliation only. Confirmed and logged the Phase 0 decisions (Python; primary
OpenRouter Space Bunny Alpha; fallback Baseten DeepSeek V4.1 Flash; adapter/router boundary;
env-only configuration; failover on timeout/unavailable/retry-exhaustion with no failover on auth
errors and no silent structured-output degradation; Sept 29 MVP priority; Hindsight unaffected by
provider choices; confirmed Phase 1 flow). Performed documentation-level verification against
official provider docs (no API calls, no keys, no installs): 17 facts VERIFIED (endpoints, auth
schemes, accepted parameters, response shape + cost usage, both providers' error taxonomies,
OpenRouter `response_format` json_schema mechanism, Baseten tool-calling/structured-output/JSON-mode
support, V4.1 Flash context 1048k / max output 32k / reasoning-on-by-default, live-configurable
limits, `x-session-affinity`), 1 PARTIALLY VERIFIED (Space Bunny 1M context and free pricing from
OpenRouter listings; max-output unknown), 7 NOT VERIFIED (endpoint-level structured-output support
for the primary route, primary stability, Baseten pricing/quota, attribution headers, observed
latency/timeouts/rate limits, practical schema conformance, retention posture). Key design
consequence recorded: OpenRouter states structured-output support is per-endpoint and exact
compliance is not guaranteed — therefore local schema validation is mandatory and
`require_parameters: true` is used for routing, making the "no silent downgrade" rule concrete.
New documents: `docs/provider-verification.md` (verification matrix + 8 runtime-test items with
pass criteria and failure responses) and `docs/phase1-execution-plan.md` (reconciled flow,
minimal Python implementation tree, M0–M8 sequence with exit criteria and parallelization rules,
remaining open Phase 0 items, start gate). `docs/implementation-readiness-review.md` annotated
with a status note (verdict superseded on the resolved items; still open: hosting, key
provisioning, task split, pre-seed rule, runtime verification). No application code written; no
dependencies installed; no secrets added; final project decision untouched; EGER untouched.

---

## Stage 10 — Architecture review (2026-09-27)

Scope: implementation-oriented review of the domain-neutral design against all planning docs
and the verified Hindsight matrix. Defined ownership boundaries (agent/Hindsight/evidence/
engineer) with two managed overlaps (normalizer leakage via validated visible output;
CLI rendering as trust surface with fixed templates); traced one investigation across nine
transitions with failure handling; confirmed all 8 memory fields are consumed (timestamps/IDs
come from Hindsight natively — no duplication); specified six recall cases incl. contradiction
and staleness handling plus empirical abstention; fixed evidence anti-contamination rules and
the hypothesis object shape; audited authorization (engineer-only; auto-retain wrapper
explicitly excluded from outcome retention); mapped every application requirement to a verified
capability with confidence + fallback (zero unresolved dependencies); judged the domain-adapter
interface sufficient with a four-function minimum; stripped the MVP to the central hypothesis
(`reflect()`, knowledge pages, web UI, auto-retain, analytics removed); specified a
domain-neutral demo and behavioral evaluation without invented numbers; set a risk-first
sequence (contract → spike → model → recall → loop → taxonomy → CLI → eval → demo).
Final decision recorded in the review: architecture is sound; A–G verdict items listed there.
New document: `docs/architecture-review.md`. No existing documents modified in this stage.
No application code written; no dependencies installed; no secrets added; final project
decision untouched; EGER not invoked anywhere in the review.

---

## Stage 11 — Two-phase delivery plan (2026-09-27)

Scope: replanned delivery around two fixed dates without changing architecture or scope.
Phase 1 (due Sept 29 afternoon): one complete vertical slice — intake through selective
retention — with visible before/after memory behavior, CLI, seeds, tests, setup instructions,
and demo video; explicit non-goals (`reflect()`, knowledge pages, web UI, auto-retain,
analytics, second domain, hardening). Phase 2 (due Oct 2, post-submission): uncommitted
candidate enhancements ranked after Phase 1 by feedback, effort-to-value, demo-stability risk,
and portfolio value. Recorded: 20-section plan (`docs/two-phase-implementation-plan.md`)
with delivery strategy, end-to-end workflow, verified-only Hindsight operations, memory model,
demo scenario, acceptance criteria, testing strategy, submission checklist, prioritization
criteria, risk table (8 risks incl. pre-seed rule and demo-day failure), dependency ordering,
critical path (pre-seed answer → decisions → spike → seeds → loop → CLI → rehearsal →
submission), and definitions of done. `docs/implementation-plan.md` annotated: Phases 0–8
remain the work breakdown; two-phase sequencing takes precedence on conflicts. Binding rules
restated: Hindsight is memory not decider; no silent memory→evidence conversion; engineer
decides; no invented capabilities or benchmarks; EGER background-only; no domain pre-selected.
No application code written; no dependencies installed; no secrets added; final project
decision untouched.

---

## Stage 14 — M0 runtime verification implementation (2026-09-27, branch `rama-m0`)

Scope: M0 verification only. **No pipeline code was written** — the investigation pipeline, CLI,
UI, ranking, auto-retention, analytics, `reflect()`, knowledge pages, second domain, and all
Phase 2 features remain unbuilt. `src/debugagent/` does not exist yet. The finalized architecture
documents were not modified.

Branch discipline: all M0 work was committed on `rama-m0`, branched from `origin/main` at `1800c62`
("Finalize Phase 1 architecture and runtime verification plan", itself pushed to `origin/main` in
this stage's first commit). The inherited `origin/main` upstream was unset so no push could reach
`main`. **No merge into `main` was performed.**

Environment inspection (reported; nothing installed): Python 3.10.11 (Microsoft Store build) with
pip 26.0.1 and no `py` launcher; no virtual environments present; **Docker not installed** (CLI
absent, daemon unresponsive); none of `OPENROUTER_API_KEY`, `BASETEN_API_KEY`, `HINDSIGHT_API_KEY`,
`HINDSIGHT_API_LLM_API_KEY`, `HINDSIGHT_URL` present; no `.env` file present; **no dependency files
exist**, so the proposed `pyproject.toml` has no conflict to resolve.

M0 scaffolding created (stdlib-only, zero installs): `.env.example` (template with empty key values;
real `.env` git-ignored), `m0/config.py` (env loading, secret redaction), `m0/schema_validate.py`
(dependency-free JSON-Schema subset validator, fail-closed), `m0/llm_probe.py` (RT-1…RT-8: primary
and fallback availability, structured output + local validation, error classification, failover
drill via unreachable base URL, auth-no-failover rule, latency calibration),
`m0/hindsight_probe.py` (H-1…H-4E: connectivity, retain, recall, and the A–E recall/abstention
probes), `m0/results_recorder.py` (per-test audit records), `m0/run_all.py` (runner; exit 0 pass /
1 fail / 2 blocked), `m0/README.md` (usage + provenance checklist to confirm live).

Runtime results (`m0/results/m0-results-20260927T180842Z.{json,md}`, runner exit code `2`):
**0 PASS · 0 FAIL · 16 BLOCKED** — RT-1…RT-8 blocked on the two provider keys; H-1 blocked on no
running Hindsight plus Docker absent; H-2…H-4E blocked on H-1. No mock or assumed result was
recorded. One code defect found and fixed during the run: cp1252 console encoding could not print
the `→` character (stdout reconfigured to UTF-8); the runner's exit-code contract was then
exercised end-to-end.

Credential-free checks (all run on `rama-m0` before commit): `python -m compileall m0` clean;
offline self-check `SELFCHECK_FAILURES = 0` covering 6 validator cases (valid object accepted;
bad-enum, missing-required, extra-property, wrong-type, and `minLength` violations all caught),
9 status-code mappings, explicit assertions that 401/403 are **not** failover-eligible, secret
redaction behaviour, config load reporting `configured=False` with blocking (not failing) tests,
and the presence of all five A–E probe definitions.

Consequence recorded: the two-phase plan's default "Hindsight hosting = local Docker" is not
executable on this machine. Options (install Docker, pip/embedded server, or Hindsight Cloud) are
left as a team decision; `docs/decision-log.md` is intentionally unchanged because no runtime result
altered a decision.

---

## Stage 15 � M0 runtime verification results (2026-09-27, branch `rama-m0`)

Milestone M0 executed live for the first time. **17 PASS � 0 FAIL � 0 BLOCKED** (runner exit `0`).
Audit trail: `m0/results/m0-results-20260927T183607Z.{json,md}` (final) plus the earlier runs in the
same directory, retained deliberately � including the run that reported a false-confidence 16/16
PASS before the probes were given real assertions.

Environment prepared: virtualenv `.venv` (git-ignored) and `hindsight-client 0.10.1` � the only
dependency installed. `.env` was populated by the user and treated as read-only: never modified,
never printed, never committed. The Hindsight Cloud endpoint and a dedicated probe bank were supplied
as process-environment overrides for the run because `.env` still carries a local address for
`HINDSIGHT_URL`; **the owner should update `.env`**.

Verified: primary and fallback availability; structured output on both routes with local validation;
error classification (primary returns 400 rather than the documented 404 for an unknown model � both
are non-failover classes); primary?fallback routing with `fallback_used=True`; auth errors not
failing over; Hindsight Cloud connectivity, bank provisioning, retain and recall; and the rebuilt
A�E recall probes with real per-probe assertions.

Key findings recorded in `docs/provider-verification.md`: the primary route **cannot** serve strict
structured outputs (`require_parameters: true` yields a routing 404) and works only as a schema hint,
so local validation is the sole guarantee; relevance scores band reproducibly (relevant � 0.97�1.09,
vague � 0.35�0.44, unrelated � 0.002�0.006), making a calibrated abstention floor feasible; recall
returns ~15 results per query, so relevance filtering stays an application responsibility; and
structured-call latency (3.4�7.2 s, up to ~13.9 s through failover) is the main demo risk.

Seven harness defects were found and fixed, all confined to the M0 tooling: console encoding, missing
`api_key` on client construction, strict-routing-only structured calls, an over-specific error-code
assertion, bank provisioning (including that `create_bank` rejects `memory_defense`), probes that
could not fail, and a wrong abstention baseline. No architecture document was changed, and no
application pipeline code exists � M0 remains verification-only.

`docs/decision-log.md` updated with the two decisions that genuine runtime results changed (hosting ?
Cloud, and primary structured-output handling).
---

## Stage 16 � M1 implementation contract handoff (2026-09-27, branch `rama-m0`)

Scope: planning and contract-freeze preparation only. **No application implementation has started** �
`src/debugagent/` does not exist. No dependencies installed, no secrets exposed, `main` untouched,
nothing committed in this stage (working tree only, pending explicit instruction).

Inputs re-read before writing: `docs/domain-neutral-system-design.md`, `docs/architecture-review.md`,
`docs/phase1-execution-plan.md`, `docs/provider-verification.md` (M0 final run), and the M0 harness
results. Four new documents produced:

- `docs/m1-contract.md` � per-component contracts (input, normalizer, MemoryStore, recall/matching,
  hypothesis, evidence, verification, resolution, retention, CLI) with input/output/required/optional/
  errors/ownership/prohibitions, plus the 10 minimum Phase 1 schemas. The 8 essential memory fields
  remain represented and Hindsight-native timestamps/ids are explicitly not duplicated.
  M0 evidence is baked in as binding rules: local structured-output validation is the only guarantee,
  failover on timeout/429/5xx only (never 400/401/402/403/404), provisional abstention bands derived
  from observed scores, recall volume requiring app-side filtering, and a one-LLM-call-per-step
  latency budget.
- `docs/phase1-team-interface.md` � proposed ownership (Rama: memory layer, schema, seeds, matching,
  abstention, retention storage; Mukul: normalization, LLM layer, hypothesis, evidence, verification,
  resolution, CLI; shared: schemas, contract tests, e2e, demo), the Python-level boundary declarations,
  the integration seam, six contract tests, and five items requiring joint confirmation (C1�C5).
- `docs/phase1-mvp-checklist.md` � required path and components, explicit scope exclusions, objective
  definition of done (functional, no invented numbers), tomorrow's dependency-ordered start sequence,
  and a Phase 1 risk watch list.
- `docs/phase1-demo-scenario.md` � one frozen before/after demo in a single domain (service/API runtime
  failures, the M0 probe family), with the rules that no performance numbers may be claimed and that
  a visible primary?fallback is a feature, not a failure.

Architecture conflict check: none found. The contracts implement the already-approved
domain-neutral design; no architecture document was modified. The one genuinely new item is C1
(`llm/` ownership), which is unassigned in the proposed split and is flagged for joint confirmation
rather than decided here.
---

## Stage 17 � M1 contract review and corrections (2026-09-27, branch `rama-m0`)

Scope: documentation review and corrections only. **No application implementation**, no dependency
installs, no `.env` change, no commit or push in this stage.

New documents: `docs/m1-freeze-decisions.md` (the five joint decisions C1�C5 with proposal, rationale,
recommended default, and consequences of the alternative, plus two low-controversy technical items
C6/C7) and `docs/m1-contract-freeze-checklist.md` (12-section freeze checklist covering schemas,
MemoryStore, LLMAdapter, pipeline interfaces, error behaviour, abstention, retention ownership,
evidence/proposal/decision separation, CLI trust surface, contract tests T1�T6, and parallel-work
safety).

Genuine corrections applied to `docs/m1-contract.md`, each from an actual defect found in review:

1. **Latency / internal contradiction.** �2.2 allowed an LLM-assisted normalizer, which would have
   doubled LLM calls per interaction and contradicted the "one call per step" budget. Normalizer is now
   **deterministic only** in Phase 1; the single LLM call per interaction is hypothesis generation.
2. **Unverified capability in the critical path.** �4.4 relied on `min_scores` for filtering, but M0
   never exercised it (probes used `types` + `max_tokens`). Abstention is now required to be computed
   in `classify_candidates` from returned scores; `min_scores` is an optional pass-through marked
   unverified.
3. **Memory-as-evidence path.** �2.7 now carries an explicit prohibition: preconditions are checked
   against `Evidence` only; recalled environment/root_cause may be displayed and may populate
   `mismatched_environment_fields` but must never satisfy a precondition or upgrade a status; missing
   Evidence yields `insufficient_evidence` regardless of memory.
4. **Ambiguous retention ownership** ("Rama storage + validation, Mukul decision to attempt") is now
   split precisely: Mukul owns the call site (only after `engineer_decision`), Rama owns validation,
   idempotency and the write, and Rama alone sets `retained`.
5. **Silent-fallback visibility.** Hypothesis output now carries `provider`/`model`/`fallback_used`, and
   the CLI trust surface must render them in the PROPOSAL header.
6. **Scope tightening.** `MemoryStore.update()` / `invalidate()` are declared but deferred to Phase 2;
   the MVP path corrects or retires nothing.
7. **Overclaim corrected.** Memory Defense: M0 confirmed the configuration call returns without error
   but did not verify active redaction, so the contract no longer treats it as a proven secret guardrail.
8. **CLI section.** Command names marked provisional pending C4; explicit trust-surface requirements
   added (four separated sections, provider/fallback header, case id + original environment, decision
   shown, never style a hypothesis as verified).

Review of the four audit areas requested found no duplicate implementation responsibility beyond the
retention seam (now frozen), no interface forcing cross-layer coupling, no path allowing retention
before verification, no silent fallback or silent structured-output degradation, and no unnecessary
Phase 1 features after item 6. Architecture documents were not modified; the domain-neutral design
remains the single source of truth.
## Stage 18 - Rama memory and Hindsight layer implemented and tested live (2026-09-28)

Rama-side implementation only. No Mukul-area code: no LLM adapter, normalizer, evidence,
verification, resolution, or CLI.

Added `pyproject.toml` and the `src/debugagent` package: `config.py` (env-driven `MemoryConfig`,
no secret defaults), `schemas.py` (frozen eight-field `MemoryCase` plus `RecallResult`,
`RecallSet`, `MatchReport`, `AbstentionDecision`, `RetentionDecision`), `memory/store.py` (the
`MemoryStore` Protocol and `MemorySchemaError`), `memory/hindsight_store.py` (Hindsight Cloud
adapter, structured metadata, tags, content rendering, local idempotency ledger, recall dedup),
`memory/matching.py` (deterministic query normalisation, relevance bands, abstention, staleness,
contradiction surfacing, provenance), and `seeds/` (six synthetic service/API runtime-failure
cases plus a deterministic loader that reports inserted/skipped/rejected).

`update()` and `invalidate()` are implemented against the client surface and are explicitly
unverified against the live backend; they stay out of the MVP path, consistent with the contract.

Test suite: 78 tests, all passing. 71 deterministic offline tests (schema, matching, store
against a fake backend, seed loader) and 7 live tests against Hindsight Cloud
(`tests/test_integration_hindsight.py`, skipped when `HINDSIGHT_URL` is unset). Live verification
covered retain, duplicate retain, recall with provenance, irrelevant-query abstention,
relevant-query acceptance, and input rejection before network. The seed loader was run live
twice against one bank: 6 inserted, then 0 inserted and 6 skipped, so idempotency is confirmed
against the real service rather than only against a fake.

Three backend behaviours were measured live that the M0 documentation does not describe. Each
one broke a working-looking implementation and is now pinned by a test:

1. `final` is query-relative and collapses as a bank fills. With 26+ near-identical retained
   cases a genuine match scored `final` 0.003 while `semantic` stayed 0.78, so an absolute floor
   on `final` alone silently dropped real matches. `effective_score()` now falls back to
   `semantic` when `final` is below floor, used only as an acceptance signal and never to order
   results. Observed separation: relevant `semantic` ~0.78-0.81, irrelevant ~0.52.
2. Recall returns multiple rows per stored memory: 6 retained cases produced 35 rows with
   differing chunk text and scores. `dedupe_by_case()` keeps the best row per `case_key` so
   provenance is one entry per case.
3. Contradiction grouping by service alone is a false-positive machine: 32 cases that all agreed
   on one root cause were flagged as disagreeing and abstained on a real match. Grouping is now
   keyed on (service, root_cause_key), so agreement is corroboration and only genuinely different
   root causes conflict.

Consequence flagged, not silently applied: C2 in `docs/m1-freeze-decisions.md` recommends
freezing a single abstention threshold. Finding 1 shows one absolute `final` threshold does not
hold across bank sizes, so the recommendation should freeze the policy shape (floor plus semantic
fallback plus contradiction margin) and defer the numeric values until seed volume is final. The
decision document was not edited unilaterally.

Ambiguities and the smallest reversible choice for each are recorded in
`docs/implementation-note-rama-memory.md`, including that `root_cause_key` is derived text rather
than a curated taxonomy and therefore cannot detect paraphrase-level disagreement.

No `.env` change, no commit or push of `.env`, `main` and `origin/main` untouched.

## Stage 19 - Documentation updated from M1 evidence; C2 reshaped; Mukul handoff (2026-09-28)

Documentation only. No behaviour change, no new feature, no Phase 2 scope (no MCP, web UI,
analytics, `reflect()`, knowledge pages). Implementation at `6f60af1` is unchanged.

**C2 no longer freezes a number.** `docs/m1-freeze-decisions.md` §C2 was rewritten from "the initial
`min_final_score` value" to "the abstention policy *shape*". Frozen: app-side abstention; `final`
decides ordering and is the primary acceptance signal; `semantic` is acceptance-only and never
ordering; the semantic fallback is mandatory; a floor yields a candidate, never a verified fact;
contradictions are surfaced and never silently resolved; agreement is not conflict; every abstention
carries `top_score` / `threshold_used` / `reason`; all thresholds are configuration. Explicitly not
frozen: every numeric value. A new checklist item makes the calibration pass against the final seed
bank a tracked, blocking task.

Each threshold is now labelled by evidence tier, and the three tiers are defined once in both
`m1-freeze-decisions.md` and `m1-contract.md`: **observed** (reproducible measurement),
**provisional default** (implementer choice, unvalidated), **final calibrated** (does not exist yet).
`min_final_score 0.05`, `semantic_floor 0.70`, `weak_reference_floor 0.6`,
`contradiction_margin 0.2` and `stale_after_days 365` are recorded as provisional defaults and are
nowhere presented as validated production thresholds.

Contract rule 3 was rewritten to state the two-signal rule and the measured reason for it. Rule 4
was extended with the multi-row finding, and a new rule 9 states that contradiction detection keys
on (service, `root_cause_key`). A new normative §5 "Known Limitations of the Implemented Memory
Layer" records seven limitations with their impact: L1 curation non-functional on the installed SDK,
L2 derived `root_cause_key`, L3 paraphrase blind spot, L4 uncalibrated thresholds, L5 thresholds split
across two configuration surfaces, L6 isolated tests under-reported risk, L7 local single-writer
ledger. §5 is evidence, not contract, and is labelled as such in the status header.

**Two facts established during this review, both now documented rather than assumed:**

1. `hindsight-client 0.10.1` exposes **no** memory-curation method. `update_memory` does not exist
   and the adapter's `update()` / `invalidate()` raise `AttributeError` against a real client, passing
   only against the test fake. This is not a contradiction of M0, which verified curation at the
   product level via REST and UI: the capability exists, the Python binding does not. Recorded as L1
   and in the implementation note; not fixed, since that is Phase 2 work requiring the REST API or an
   SDK upgrade.
2. `semantic_floor = 0.70` does **not** separate the M0 vague class from relevant. Observed M0
   `semantic`: relevant 0.867 / 0.884 / 0.802, vague 0.766, irrelevant 0.584 / 0.505 / 0.475. A floor
   of 0.70 admits the vague case, so the vague → `partial` behaviour currently depends on
   `score_final`. Recorded as an open calibration question in §C2.3 and as a checklist item, not
   hidden and not papered over.

`docs/handoff-mukul-memory.md` is new. It covers what the layer provides, the exact Protocol and
signatures to consume, recall-result structure, how to read relevance and abstention, provenance,
contradiction surfacing, the seven things Mukul must not assume about Hindsight scores, the exact
`MemoryCase` field contract to send in, what the layer does not decide, and his integration points.
Its first section is the trust rule — MEMORY informs, EVIDENCE verifies, AGENT proposes, ENGINEER
decides — with an explicit prohibition on treating a recalled memory as current evidence or letting
one satisfy a verification precondition. It also flags that `semantic_floor` and
`contradiction_margin` are constructor-only, so Mukul tuning by environment alone will not reach them.

The freeze checklist was updated to the new legend (`[~]` implemented-but-unverified, `[!]` known
limitation), marked the memory-layer items, marked T1/T2 as implemented and T3-T6 as blocked on
Mukul's code, and added a handoff-readiness section. Documentation was changed to describe what the
code does, not what it was intended to do.

No `.env` change. `main` and `origin/main` untouched.
