# Implementation Plan — Engineering Debugging Agent

**Status:** Planning only. No application code written. Phases run in order unless noted.
**Supersession note (2026-09-27):** delivery scheduling is now governed by
`docs/two-phase-implementation-plan.md` (Phase 1 due Sept 29 afternoon; Phase 2 due Oct 2).
The Phases 0–8 below remain the detailed work breakdown; where the two documents differ on
order, the two-phase plan's risk-first sequence (contract → spike → model → recall → loop →
taxonomy → CLI → eval → demo) takes precedence.
**Reference:** `docs/final-project-definition.md` (what), `docs/hindsight-capability-verification.md`
(proven building blocks), `docs/decision-log.md` (open questions Q1–Q19).

## Phase 0 — Decisions and Setup (before any build)

- Confirm task split in `docs/team-task-split.md` (both members).
- Decide implementation language (Python or TypeScript — both have verified Hindsight clients).
- Decide Hindsight hosting (local Docker, embedded, or Cloud) and LLM key/budget ownership.
- Confirm with organizers: pre-seeded memory allowed; live-demo requirements; deadlines.
- Exit criteria: open questions Q3, Q6, Q7, Q9, Q11, Q12, Q14 answered and logged.

## Phase 1 — Architecture

- Freeze the agent loop from `docs/final-project-definition.md` (memory → evidence → proposal → decision).
- Define module boundaries: CLI, agent orchestrator, Hindsight layer, case schema (application-level),
  verification gate, seed-data loader, demo instrumentation (logging, not metrics claims).
- Define error and abstention behavior (no confident recall → generic checklist path).
- Exit criteria: architecture section reviewed by both members; recorded in decision log.

## Phase 2 — Hindsight Integration

- Stand up Hindsight locally (or Cloud) per Phase 0 decision.
- Spike first: retain ~5 sample cases, recall with query + tags, confirm relevance ordering is
  steerable; calibrate `max_tokens` budget and `min_scores` abstention empirically.
- Implement thin Hindsight layer: retain case, recall cases, PATCH corrections, invalidate stale
  cases. No business logic leaks into this layer.
- Enable Memory Defense redaction on the bank.
- Exit criteria: spike results logged; layer functions covered by smoke tests.

## Phase 3 — Memory Design

- Freeze the case field convention (metadata/tags/context mapping) and application-side validation.
- Author 10–15 synthetic-but-realistic seed cases in the single starting domain.
- Define tag taxonomy (problem type, environment, outcome) and keep it controlled.
- Exit criteria: seed set reviewed for realism and pattern coverage (3–4 recurring patterns).

## Phase 4 — Agent Workflow

- Implement: issue intake → recall → environment comparison → ranked proposal (application-level
  ranking) → verification prompt → outcome capture → retain.
- Implement contradiction surfacing (same symptoms, different root cause) and abstention path.
- Every applied recalled fix must carry a logged relevance check.
- Exit criteria: full loop runs end-to-end against pre-seeded bank without errors.

## Phase 5 — UI/API

- CLI-first: `debug` command (describe issue), recall panel rendering, verification prompts,
  `resolve` command (record outcome).
- Keep a clean internal API so a web/Slack UI can follow later without rework (out of MVP scope).
- Exit criteria: demo-narrative walkthrough completable from the CLI alone.

## Phase 6 — Test / Evaluation

- Smoke tests for Hindsight layer; golden-path test for the full loop; abstention test
  (empty/irrelevant bank → generic path, no hallucinated recall).
- Run the evaluation plan from `docs/final-project-definition.md` (precision, utility, failed
  approaches avoided, verification traceability) and record raw observations only.
- Exit criteria: tests green; evaluation observations documented without invented numbers.

## Phase 7 — Demo

- Rehearse the before/after narrative (generic checklist → retained case → memory-informed
  second investigation with visible verification).
- Record 2–5 minute video; prepare live runnable as backup.
- Exit criteria: video recorded; live demo rehearsed; no sensitive data visible.

## Phase 8 — Documentation / Content

- Polish repo README (usage, architecture, demo link); keep planning history intact.
- Draft technical articles (1 per member) and social posts (1 per member) from actual built
  behavior; demo video finalized.
- Final submission checklist against organizer requirements.
- Exit criteria: all deliverables submitted.

## Explicit Non-Goals for This Plan

No live EDA integration, no multi-user features, no auto-fix execution, no production hardening,
no second domain. Any addition requires a decision-log entry agreed by both members.
