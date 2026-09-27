# Phase 1 MVP Checklist, Definition of Done & Start Order

**Status:** Operational checklist for the 29 Sept MVP. No performance targets are stated anywhere;
acceptance is functional and observable.
**Date:** 2026-09-27. **Reference:** `docs/m1-contract.md`, `docs/two-phase-implementation-plan.md`.

## 1. Required End-to-End Path

```
Engineer input
 → Normalize
 → Recall historical cases (Hindsight)
 → Compare current vs historical conditions
 → Generate hypotheses
 → Show supporting evidence
 → Engineer verification
 → Resolve
 → Selective retention
```

## 2. Required Components

| # | Component | Owner | Done when |
|---|-----------|-------|-----------|
| 1 | Hindsight Cloud bank provisioned + MemoryStore implemented | Rama | retain/recall/update/invalidate work against a real bank |
| 2 | Memory case schema + validation (8 essential fields) | Rama | contract test T1 green |
| 3 | Seed cases + idempotent loader | Rama | loader runs twice, no duplicates |
| 4 | Recall matching, relevance classes, abstention | Rama | contract tests T2/T6 green |
| 5 | Normalizer (deterministic-first) | Mukul | no invented field values |
| 6 | LLM adapter + router (primary → fallback, fail-closed) | Mukul (confirm C1) | M0 behaviours preserved as unit tests |
| 7 | Hypothesis generation with citations | Mukul | contract test T3 green |
| 8 | Evidence model + verification gate + resolution | Mukul | contract tests T4/T5 green |
| 9 | CLI with four separated sections | Mukul | walkthrough completable by the other person |
| 10 | End-to-end demo scenario | shared | repeatable run |

## 3. Explicit Exclusions (scope protection)

Web UI · analytics · `reflect()` · knowledge pages · multi-agent orchestration · second engineering
domain · automatic authorization · automatic retention without engineer verification · live EDA/log
integrations · production hardening (auth, scale, monitoring, backup) · any infrastructure not needed
to run the demo loop. Adding any of these requires a joint decision-log entry.

## 4. Definition of Done (objective, no invented numbers)

**Note (2026-09-28):** these are end-to-end items and none can be ticked yet, because the Mukul-owned
pipeline, `llm/` and CLI do not exist. The memory-layer prerequisites behind items 2, 3, 4 and 8
(seeding + idempotency, recall with original conditions, contradiction surfacing, retention
validation) are implemented and live-verified at `rama-m0` `6f60af1`; the remaining work on those
items is the pipeline and the CLI that consume them. See `docs/handoff-mukul-memory.md`.

- [ ] Fresh setup works from README on a clean checkout.
- [ ] Seed cases load successfully and idempotently.
- [ ] A relevant historical case is recalled for a similar new issue, with its original conditions shown.
- [ ] A similar-but-different-root-cause case is **not** presented as identical; differing conditions are listed.
- [ ] An unrelated problem triggers abstention with a visible "no relevant memory" signal.
- [ ] Every hypothesis cites supporting memory (or is explicitly `generic`), and states refutation conditions.
- [ ] Engineer verification is explicit; no proposal proceeds without a recorded decision.
- [ ] Only a verified resolution enters retention; failed approaches are retained too.
- [ ] A primary-LLM failure is served by the fallback, and the fallback is visible in logs.
- [ ] Structured LLM output is validated locally; invalid output never silently degrades.
- [ ] CLI clearly separates MEMORY / EVIDENCE / PROPOSAL / DECISION.
- [ ] The end-to-end demo runs repeatedly without manual repair.
- [ ] No secrets, proprietary data, or unsupported claims in repo, demo, or content.

## 5. Tomorrow's Start Order (with dependencies)

| Step | Work | Depends on | Owner |
|------|------|------------|-------|
| 1 | Freeze `docs/m1-contract.md` + `schemas.py` | M0 complete | both (30 min, together) |
| 2 | Confirm C1–C5 open items | step 1 | both |
| 3 | Create working branches (`rama-memory`, `mukul-loop`) from `rama-m0` | step 1 | each |
| 4 | Write contract tests T1–T6 as stubs (red) | step 1 | shared |
| 5 | Parallel build: `memory/`+`seeds/` ∥ `llm/`+`normalize`+`verify` | steps 3–4 | Rama ∥ Mukul |
| 6 | First integration: recall → classify → hypotheses | step 5 memory side | both |
| 7 | Seed validation against a real bank | step 5 memory side | Rama |
| 8 | Full loop end-to-end | step 6 | both |
| 9 | Contract tests green + abstention tuning | steps 6–8 | shared |
| 10 | CLI polish + demo rehearsal | step 8 | Mukul + shared |
| 11 | Demo video + README setup | step 10 | shared |
| 12 | Submission checklist | step 11 | both |

Critical path: 1 → 3/4 → 5 (memory side) → 6 → 8 → 10 → 11 → 12. Seed authoring (Rama) and the first
integration are the long poles; protect them by not starting CLI polish before step 8.

## 6. Risk Watch List for Phase 1

| Risk | Signal | Response |
|------|--------|----------|
| Abstention too eager/lax | H-4A/C/E behaviour drifts as seeds grow | **Already observed, not hypothetical:** with 26+ near-identical cases a real match fell to `final` 0.003 while `semantic` held 0.78. Calibrate **all** thresholds against the final seed bank (C2). Do not reach for a single number |
| Threshold narrated as a hard truth | any doc, comment or demo line quotes 0.05 / 0.70 as validated | they are provisional defaults; correct the narration (`docs/m1-freeze-decisions.md` §C2.2) |
| Latency in the demo | > ~15 s per interaction | cut LLM calls to one per step; shorten prompts |
| Citation drift | hypothesis cites unknown case ids | contract test T3 fails the build |
| Auto-accept creep | resolution recorded without decision | contract test T4/T5 |
| Scope creep | any item from §3 appears | joint decision-log entry required |
