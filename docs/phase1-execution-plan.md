# Phase 1 Execution Plan — Implementation Tree and Sequence

**Status:** Planning only. No application code written, no dependencies installed, no keys
provisioned. Awaiting authorization to begin Milestone M0.
**Date:** 2026-09-27. **Deadline:** MVP demo-ready by September 29 afternoon.
**Reference:** `docs/two-phase-implementation-plan.md` (scope/non-goals),
`docs/architecture-review.md` (boundaries, sequence rationale),
`docs/llm-provider-architecture.md` + `docs/provider-verification.md` (LLM layer),
`docs/hindsight-capability-verification.md` (memory layer),
`docs/implementation-readiness-review.md` (Phase 0 exit criteria).

## 1. Reconciled Phase 1 flow (team decision, unchanged from architecture)

```
User Case
  → Input Normalizer
  → Investigation Orchestrator
      → Hindsight MemoryStore (recall + matching)
      → Evidence Comparison
      → Hypothesis Generation   [LLM Router → Primary Adapter ↘ Fallback Adapter]
  → Engineer Verification
  → Resolution Proposal
  → Selective Memory Retention
```

Boundary invariants (non-negotiable, asserted in code and review):
LLM proposes · Hindsight provides remembered information · current evidence verifies ·
the engineer decides · an LLM hypothesis is never promoted to verified evidence · no
provider-specific code outside `llm/` adapters · no LLM-provider setting may alter
`MemoryStore` or the workflow.

## 2. Proposed implementation tree (Python, minimal — nothing speculative)

```
.
├── README.md                        # setup + demo walkthrough (Phase 1 deliverable)
├── pyproject.toml                   # deps + console entry point
├── .env.example                     # config template, no secrets (real .env git-ignored)
├── src/debugagent/
│   ├── config.py                    # env-driven settings; redacted startup log
│   ├── cli.py                       # case start / recall view / verify / resolve
│   ├── domain.py                    # single-domain taxonomy as CONFIG (not code branches)
│   ├── schemas.py                   # case, match-report, hypothesis schemas (app-owned)
│   ├── llm/
│   │   ├── adapter.py               # LLMAdapter ABC + normalized error taxonomy
│   │   ├── router.py                # primary→fallback, retries, observable logging
│   │   ├── openrouter.py            # endpoint/auth/error specifics + require_parameters
│   │   ├── baseten.py               # endpoint/auth/error specifics
│   │   └── validate.py              # local schema validation (fail-closed)
│   ├── memory/
│   │   ├── store.py                 # MemoryStore ABC (retain/recall/update/invalidate)
│   │   └── hindsight_store.py       # verified Hindsight calls only
│   ├── pipeline/
│   │   ├── normalize.py             # deterministic-first normalization
│   │   ├── recall_match.py          # recall + relevance classes + contradiction surfacing
│   │   ├── evidence.py              # current-evidence model + env diff
│   │   ├── hypothesize.py           # LLM hypotheses with citations (never "verified")
│   │   ├── verify.py                # precondition checks + engineer confirmation
│   │   └── resolve_retain.py        # outcome capture + selective retention
│   ├── seeds/
│   │   ├── cases/*.json             # seed cases (single domain, 3–4 patterns)
│   │   └── load.py                  # idempotent loader
│   └── logging_conf.py              # structured logs incl. fallback_used, provider, model
├── tests/
│   ├── test_llm_router.py           # failover, auth-no-failover, no-degrade rules
│   ├── test_memory_store.py         # retain/recall/update/invalidate round-trip
│   ├── test_loop_golden.py          # seeded case → expected recall + proposal shape
│   └── test_abstention.py           # empty/weak bank → generic path, zero invented cases
└── docs/                            # existing planning docs (unchanged)
```

Deliberately absent: `web/`, analytics modules, `reflect()` usage, knowledge pages,
auto-retention, multi-agent orchestration, domain-adapter package (one domain ships as config;
a second domain later becomes config plus, at most, a small adapter module).

## 3. Phase 1 execution sequence

| Milestone | Work | Exit criterion (observable) | Blocked by |
|-----------|------|---------------------------|-------------|
| **M0** | Provider + memory runtime tests (RT-1…RT-8 in `provider-verification.md`); stand up Hindsight; 5 probe retains; tag+query recall steerability; abstention calibration | All runtime-test results logged; no unclassified failures | **Keys + install permission (team)** |
| **M1** | Interface contract freeze: `schemas.py`, `MemoryStore` ABC, `LLMAdapter` ABC, CLI command surface | Contract reviewed by both members; committed | M0 |
| **M2** | `MemoryStore` implementation + idempotent seed loader + bank inspection helper | Round-trip tests green; seeds loadable twice without duplication | M1 |
| **M3** | `LLMAdapter` + router + two provider adapters + local validation | RT-6 failover drill green; RT-8 auth drill green; no-degrade rule tested | M1, M0 |
| **M4** | Normalizer + evidence model + env diff (deterministic) | Normalized draft validates; env diff marks unknowns explicitly | M1 |
| **M5** | Recall + matching + relevance classes (relevant / partial / irrelevant / contradictory / stale) + abstention path | Abstention test green (zero invented cases); contradiction case surfaces both | M2, M4 |
| **M6** | Hypothesis generation + verification gate + resolution + selective retention | Golden-path loop green end-to-end; every applied fix traces to a logged check | M3, M5 |
| **M7** | CLI surface + demo scenario rehearsal + paired with/without-memory observations | Walkthrough completable by the non-author; observations recorded raw | M6 |
| **M8** | Demo video (2–5 min), README setup verified from clean checkout, submission checklist | Submission complete before Sept 29 afternoon | M7 |

Parallelization (only after M1 is frozen): `memory/` + `seeds/` (one owner) ∥ `llm/` (other
owner) ∥ `pipeline/normalize+evidence` (either). `pipeline/recall_match`, `hypothesize`, `verify`,
`resolve_retain` are strictly sequential after their inputs — no parallel edits to shared
schemas without review.

## 4. Remaining Phase 0 items not covered by this turn's decisions

| Item | Status | Note |
|------|--------|------|
| Hindsight hosting (local Docker vs embedded vs Cloud) | **Open** | Blocks M0 environment setup; default proposal: local Docker, switchable by config |
| Key provisioning/ownership (OpenRouter, Baseten, Hindsight LLM key) | **Open** | Blocks M0 runtime tests; keys must live in environment only |
| Task-split confirmation | Open | Not architecture-blocking; scheduling risk only |
| Organizer rule: pre-seeded memory allowed? | Open | If live-learning-only, the demo script changes (retain the first case live) |
| First domain taxonomy values | Open (by design) | Frozen as *config* in M1; values chosen during M2 seed authoring |

## 5. Gate for starting implementation

Implementation may begin only when: (a) the team authorizes starting, (b) dependency
installation is permitted, and (c) the two provider keys plus the Hindsight LLM key are present
in the environment. M0 is the first executable milestone and is entirely runtime-verification
work — no pipeline code should be written before its results are logged, because the recall
and abstention knobs it produces are inputs to M1–M6 configuration.
