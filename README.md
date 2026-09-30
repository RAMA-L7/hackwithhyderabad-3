# Debugging Knowledge Agent — built on Hindsight

**Status:** Phase 1 implemented and merged into `main`\
**Memory layer:** [Hindsight by Vectorize](https://hindsight.vectorize.io/)\
**Team:** Rama Krishna Ketha, Mukul Rai · [Team Status](#team-status)

A command-line debugging assistant for a single engineer. It recalls past debugging cases from
Hindsight, proposes hypotheses in one LLM call, makes the engineer verify them against current
evidence, and retains the verified outcome — including what failed — for the next similar issue.

```
MEMORY informs · EVIDENCE verifies · AGENT proposes · ENGINEER decides
```

A recalled case is never evidence and never satisfies a verification requirement. Commands are in
[Run the Phase 1 agent](#run-the-phase-1-agent); the full loop is in
[docs/phase1-implemented.md](docs/phase1-implemented.md).

## What one session does

1. **Normalize** the engineer's description deterministically (service, runtime, proxy, region).
2. **Recall** similar past cases from a Hindsight bank and classify each one — `relevant`,
   `partial`, `contradictory`, `irrelevant` or `stale` — or abstain with a stated reason.
3. **Collect current evidence** from the engineer, and only from the engineer.
4. **Propose** ranked hypotheses in one structured LLM call. A hypothesis may cite only a case that
   was actually recalled.
5. **Verify** — the engineer decides each hypothesis against current evidence.
6. **Retain** the outcome, including failed approaches, when the engineer reports a resolution.

## How Hindsight is used

- **Retain / recall.** Each resolved investigation is stored as one case in a Hindsight bank, and
  recalled by query when a similar issue comes up.
- **Fact extraction.** Hindsight turns retained case text into separate facts — symptom, root cause,
  fix, failed approach. The store collapses those per-fact rows back into one entry per case and
  keeps every distinct fact, because the best-scoring row is usually only the symptom.
- **Verbatim metadata.** Environment values and `failed_approaches` are written to Hindsight metadata
  verbatim and re-attached at recall as `Failed before: …`, because LLM fact extraction does not
  reliably keep them.
- **Application-side abstention.** Hindsight returns scores; the decision to act on a recalled case is
  made in this codebase. `final` is query-relative, so it decides ordering, and `semantic` may only
  admit a case when `final` has collapsed. These thresholds are calibration parameters, not
  validated constants.

***

## Architecture Evolution

This project started as a **Phase 1 Engineering Debugging Agent** and is being evolved toward a
**Hub-and-Spoke Multi-Agent Coordinator** architecture.

The governing principle is unchanged from Phase 1:

```
MEMORY informs · EVIDENCE verifies · AGENT proposes · ENGINEER decides
```

The architecture deliberately keeps these four authorities **separate**. A recalled case informs; it
never verifies. A tool reading the current repository verifies; it never decides. An agent proposes;
it never signs off. The engineer decides.

**This is a work in progress, not a finished multi-agent runtime.** The sections below distinguish
what is *implemented*, what has been *audited or experimentally validated*, and what is *planned*.
The multi-agent topology is the target design — **workers do not currently execute, and there is no
Coordinator runtime or parallel fan-out today.**

The architecture decision is recorded in
[docs/adr-001-hub-spoke-coordinator.md](docs/adr-001-hub-spoke-coordinator.md).

### Current Phase 1 flow (implemented and running)

```
Input
  ↓
Normalize
  ↓
Memory Recall
  ↓
Current Engineering Facts
  ↓
Hypothesis Generation
  ↓
Engineer Decision
  ↓
Retention
```

This is the flow described in [What one session does](#what-one-session-does), and it is what the CLI
executes today.

### Planned hub-and-spoke topology (not yet implemented)

```
                    ┌─────────────────────┐
                    │     Coordinator     │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              ↓                ↓                ↓
       Memory Specialist   Code/Log Verifier  Patch Generator
              │                │                │
              ↓                ↓                ↓
          Hindsight        Read/Grep/Glob     Read/Diff
              │                │                │
              └────────────────┴────────────────┘
                               ↓
                         Coordinator
                               ↓
                         Agent Proposal
                               ↓
                         Engineer Decision
```

Read this diagram as the **planned** target. It is not a claim that the three workers already run,
and not a claim that they run in parallel. Today the Coordinator can only *specify* and
*authorize* a task; it cannot yet execute one, and no worker process is started.

### Runtime architecture

The components that exist today, and the boundary each one sits behind:

| Layer                | Component | Responsibility and boundary |
| -------------------- | --------- | --------------------------- |
| Orchestration        | `LLMRouter` | Provider-independent LLM access. Two call paths: `complete_structured()` for single-shot structured output, and `complete_with_tools()` for a bounded tool-calling loop. Both exist; only the first is on the Phase 1 path. |
| Tool-calling seam    | `ToolDefinition`, `ToolCall`, `ToolResultMessage`, `ToolLoopResult` | Transport for a tool round trip. Tool calling is **opt-in per route** via `LLM_<ROLE>_TOOLS`, and the turn loop is bounded (`MAX_TOOL_TURNS`, default 4). Tool execution is **caller-supplied** — the router does not run tools itself. |
| Memory port          | `MemoryPort` protocol, `HindsightMemoryPort`, `OfflineMemoryPort` | The seam the pipeline talks to. Translation, abstention policy and error classification live here, so the pipeline never depends on a vendor SDK. |
| Memory store         | `HindsightMemoryStore`, `Ledger` | The only place Hindsight is touched. Owns the client lifecycle, the idempotency ledger, and the retain path. |
| Evidence             | `pipeline/evidence.py`, `pipeline/verify.py` | Evidence is built **only** from what the engineer supplies now. The module deliberately exposes no parameter through which recalled memory could enter. |
| Delegation           | `TaskSpec`, `WorkerContext`, `SubAgentResult`, closed worker registry | Defines and authorizes delegation. **No worker execution exists yet.** |

Two boundaries are enforced in code rather than by convention:

- **Evidence has no memory parameter.** `build_evidence()` accepts the normalized case and the
  engineer's own facts. Recalled memory cannot reach it, so it cannot become evidence.
- **Verification refuses to proceed without the engineer.** `verify()` raises if a hypothesis has no
  recorded engineer decision, and a missing evidence item yields `insufficient_evidence` *however
  strong the memory match* — memory never fills a gap.

This is what makes the separation real rather than aspirational:

```
Proposal ≠ Evidence ≠ Knowledge ≠ Authorization
```

- **Knowledge** is what Hindsight recalls: prior cases, offered as context.
- **Evidence** is what the engineer supplies now, and what deterministic tools read from the
  repository.
- **Proposal** is what the model produces, including any patch diff.
- **Authorization** is the engineer's decision, and nothing else confers it.

### Agent definition payload

Every agent is declared with exactly four fields, and no more:

| Field            | Purpose                                                    |
| ---------------- | ---------------------------------------------------------- |
| `description`    | What this agent is for and when the Coordinator should use it |
| `prompt`         | The agent's standing instructions                          |
| `allowed_tools`  | The complete set of tools it may use — nothing else        |
| `model`          | Which LLM route it uses                                    |

The planned worker roster, as currently declared in the registry:

| Worker                | `allowed_tools`                                        | `model`  |
| --------------------- | ------------------------------------------------------ | -------- |
| **Memory Specialist** | `hindsight_recall`, `hindsight_get_facts`               | `primary` |
| **Code/Log Verifier** | `read`, `grep`, `glob`                                 | `primary` |
| **Patch Generator**   | `read`, `diff`                                         | `primary` |

Workers are intentionally **capability-scoped**: a worker's tool list is the whole of its authority,
and the registry re-checks every requested tool against it. A worker cannot use a tool it was not
granted, and no worker can spawn further workers.

**Anti-recursion boundary:**

```
Coordinator → Worker        (permitted, depth 1)
Worker      → no recursive worker delegation
```

The `task` tool is **coordinator-only**. A worker declaring it in `allowed_tools` is rejected at
registration time, and `max_spawn_depth` is fixed at 1. Delegation depth cannot grow.

### Implementation status

| Area                                            | Status        |
| ----------------------------------------------- | ------------- |
| Architecture decision / ADR                     | Implemented   |
| Agent task definitions                          | Implemented   |
| Worker registry / authorization                 | Implemented   |
| Anti-recursion controls                        | Implemented   |
| Context isolation structures                   | Implemented   |
| LLM tool-calling seam                          | Implemented   |
| Tool turn bounding                             | Implemented   |
| Hindsight concurrency hardening                | Implemented   |
| Deterministic concurrency regression tests     | Implemented   |
| Remote Hindsight idempotency experiment        | Validated     |
| Remote state-idempotent retention (P3-2B)      | Implemented   |
| Retention failure legibility (P3-2C)           | Implemented   |
| Cross-phase P3-2 integration coverage (P3-2D) | Implemented   |
| VLSI engineering workers                       | Planned       |
| Coordinator execution                          | Planned       |
| Parallel worker fan-out                        | Planned       |

### Test status

```
349 tests passed
9 skipped
```

The Hindsight-dependent tests skip unless `HINDSIGHT_URL` is set. P3-0/P3-1 added **deterministic**
concurrency regression coverage: these use barriers and events rather than sleeps, and the concurrency
suite has been verified to fail against the pre-fix implementation and pass after it.

This is a test status for the implemented layers only. **The multi-agent runtime is not tested,
because it does not exist yet** — there is no Coordinator execution, no worker execution and no
fan-out to test.

### Hindsight reliability

**P3-1 — local concurrency (implemented).** The idempotency ledger previously had no synchronization
at all, so concurrent retains could lose local updates and write the same case to Hindsight twice.
The fix is a **process-local lock keyed on the normalized, resolved ledger path** — not on the
instance — because a new store (and therefore a new ledger) is constructed on every `build_port()`
call, so two instances on one ledger file must share one lock.

- The lock is a **`threading.RLock`**, and is **shared by every store using the same ledger path**, so
  cross-store access to one ledger file is serialized, not just access within one instance.
- The **retain path is serialized as a whole**: the idempotency check, the remote write and the local
  record all happen inside one critical section, which is what prevents the same case being written
  remotely twice.
- The **ledger is refreshed before the idempotency check**, so a second store on the same path sees a
  write made by the first rather than acting on a stale snapshot.
- Concurrent ledger updates are protected against lost updates.
- **`recall()` is intentionally not serialized by the retain lock.** It touches no ledger state, and
  locking it would serialize reads for no correctness gain.
- The synchronization is **process-local**. It does not coordinate separate processes.

**What this does not provide:** it does **not** provide distributed exactly-once semantics. A lock
cannot make a remote write and a local write atomic together.

**P3-2C — remote capability experiment (validated, not integrated).** A live experiment against an
isolated test bank established:

- Hindsight `document_id` is a **real server-side identity**: stored units are retrievable by it, and
  distinct ids do not interfere.
- `update_mode="replace"` produces **one** remote document for repeated writes sharing a
  `document_id` — confirmed across 1, 2, 3, 4 and 6 writes, with both identical and differing content.
  (An `append` control was used to confirm the measurement could detect accumulation.)
- Repeating the same `document_id` **after an ambiguous outcome** is state-idempotent: when a write
  succeeded remotely but the caller could not tell, retrying the same `document_id` did **not** create
  a second remote document.

**Important limitation:** `replace` is **destructive**. Writing two genuinely different cases under
one `document_id` caused the first case's extracted facts to be **completely overwritten** in the
experiment. This is a real data-loss risk whenever a `document_id` is reused across distinct cases.

Therefore

```
document_id = case_key  +  update_mode = "replace"
```

is now integrated, on a case identity that is specific enough that two distinct cases cannot share a
`document_id`. A 16-hex collision is not the concern; an over-broad key was, and the outcome digest
is what closed it.

### A constraint on future parallel fan-out

The live experiment also produced a directly relevant observation: **concurrent calls to the
synchronous Hindsight retain path were not safe under ordinary Python threads** in the tested
configuration — concurrent retains raised
`RuntimeError: Timeout context manager should be used inside a task` from the client's synchronous
facade.

This is scoped deliberately. It describes the tested client (`hindsight-client` 0.10.1) and the tested
synchronous code path only. It is **not** a claim that the Hindsight service is globally thread-unsafe,
and it is not a claim about every configuration or about the async API.

It is, however, an important constraint: a future parallel fan-out cannot assume one shared
synchronous client is safe across workers, and the client question is a live follow-up before
parallel worker execution.

### P3-2 status — implemented, with a documented residual gap

**State-idempotent remote retention is now implemented (Phase A + P3-2B).** The retain call carries
`document_id=case_key` with `update_mode="replace"`, and the case identity is content-addressed, so
retaining the same case again converges on **one** remote document instead of creating a second.

**Failure legibility is implemented (Phase C).** Retention failures are typed and
distinguishable — `persist`, `ambiguous`, `unavailable` — and a corrupt ledger is surfaced rather than
silently read as empty. See [Failure legibility](#failure-legibility-p3-2c-implemented).

**Cross-phase coverage is implemented (Phase D).** Deterministic tests cover the joins between the
phases, not just each in isolation: the computed identity reaching `document_id`, one identity used
consistently across ledger, metadata and remote document, the key surviving into recall and
`dedupe_by_case`, and P3-1's serialized retain composing with P3-2B's document identity under
concurrency.

**What is still NOT implemented:** local/remote atomicity, automatic reconciliation, cross-process
locking, and resource limits. **Exactly-once is not claimed and is not achievable** — a retry still
issues another remote request and pays another extraction cost.

- **Local ledger persistence and remote Hindsight retention are two separate systems.** The ledger is
  a local JSON file; the memory is a remote service. There is no transaction spanning them.
- **Local synchronization cannot provide cross-system exactly-once semantics.** P3-1's lock provides
  process-local mutual exclusion. A lock cannot make a remote write and a local write atomic together.
- **The synchronous retain path exposes no idempotency key.** `operation_id` exists in the client but
  is honoured only for asynchronous retain; the client warns if it is passed to a synchronous call.
  The synchronous response also carries no remote memory identifier, so there is no handle to
  reconcile against. This remains unchanged — `operation_id` and `retain_async` are not used.
- **A usable remote primitive was confirmed live and is now adopted.** A fixed `document_id` with
  `update_mode="replace"` gives *state-idempotent* replacement, including after an ambiguous outcome.
- **`replace` is destructive, and safety comes from the identity, not the mode.** It overwrote the
  first case's content in the experiment, so it is only safe because `case_key` is content-addressed:
  two cases that genuinely differ cannot share a `document_id`. Changing the identity formula would
  weaken this guarantee.
- **Session identity must not be lost when deriving the remote identity.** The case key is
  `sha256(problem_signature | session_id | outcome_digest)[:16]`, and `MemoryCase.to_dict()` did not
  serialize `session_id` — so a case that round-tripped through serialization silently lost part of
  its own identity. **Fixed and pinned by tests.**
- **The synchronous Hindsight client is not thread-safe.** Live testing raised a `RuntimeError` under
  concurrent synchronous retains. This constrains parallel fan-out (P5); it does not affect serial
  retention.
- **Exactly-once is not claimed.** The strongest defensible description is *state-idempotent*: an
  identical repeat converges on the same remote state, while the operation itself is performed again,
  at another extraction/token cost.

### Planned P3-2 implementation

This is the intended sequence. It is **not** a description of completed code, and no phase below has
been started.

**Phase A — case identity.** *(begun)* Make the case identity specific enough that two genuinely
different cases cannot collide, and stop losing session identity on the way through serialization and
the retention flow. Establish the identity contract with deterministic tests before it is used for
anything remote.

**Phase B — remote state-idempotent retention.** *(implemented)* The corrected case identity is used as
the Hindsight `document_id` with `update_mode="replace"`, so a repeated retain converges on one remote
document. Documented as *state-idempotent replacement*, explicitly **not** exactly-once operation
semantics, with the destructive-replace risk recorded.

**Phase C — failure legibility.** *(implemented)* The failure modes are now distinguishable rather than
ambiguous: a typed `persist` failure when the remote write may have succeeded but the local ledger
could not be written; an `ambiguous` failure when the remote outcome is genuinely unknown, kept
distinct from a clean `unavailable`; corrupt ledger state surfaced instead of silently read as
empty; and the retain response's own success flag verified rather than assumed. There is still **no
automatic retry and no automatic reconciliation** — an ambiguous outcome is escalated, not resolved.

**Phase D — regression and integration coverage.** *(implemented)* Deterministic tests cover the
complete A → B → C contract, including the joins between the phases: the computed identity reaching
`document_id`, one identity used consistently across the ledger, Hindsight metadata and the remote
document; the computed key surviving into recall and `dedupe_by_case`; and P3-1's serialized retain
composing with P3-2B's document identity under concurrency. No sleeps and no timing-dependent
assertions.

### Failure legibility (P3-2C, implemented)

A caller can now tell three retention failures apart, because they need different handling:

| `MemoryFailure.kind` | Meaning | What the caller must not assume |
|---|---|---|
`persist` | The remote write **may have succeeded**; the local ledger could not be written | Not that nothing was stored. The remote document may exist; it was **not** rolled back and cannot be from here |
`ambiguous` | The transport broke with **no answer**, after the request may have been transmitted | Not that the case was stored, nor that it was not. The remote outcome is **unknown** |
`unavailable` | The service answered, and the answer was a failure | Safe to treat as a clean retry — nothing was stored |

Details:

- **No raw `OSError` escapes the memory layer.** A local write failure after a successful remote
  write is raised as a typed persistence failure that states the remote document may already exist.
  Previously it leaked a bare `OSError` straight through `MemoryPort`.
- **Ambiguity is not assumed.** Only transport failures that leave the remote outcome unknown are
  ambiguous — read timeouts, connection resets, interrupted responses. A failure that provably
  happened *before* the request reached the service (DNS failure, connection refused, connect
  timeout) is a **clean** failure, because nothing could have been stored. Any HTTP response,
  including 5xx, is clean: the service answered.
- **`success=False` is not recorded as a successful retain.** If Hindsight reports that it did not
  store the case, the idempotency ledger is not updated, rather than marking a case retained that is
  not retrievable.
- **A corrupt ledger is surfaced, not silently emptied.** A malformed, unreadable, or
  version-incompatible ledger raises a persistence failure instead of reading as "this agent has
  retained nothing" — which would otherwise re-retain every case. The corrupt file is **never**
  overwritten or deleted, and a *missing* ledger on first use remains normal.

**What this does not do:** it does not close the cross-system atomicity gap. Local and remote are
still not in a transaction, so a `persist` failure still means the two are out of step. There is
**no automatic retry** and **no automatic reconciliation** — an ambiguous outcome stays ambiguous and
is escalated to the engineer. A retry remains state-idempotent at the remote document (P3-2B) but
is still another remote operation and another extraction cost.

### Explicitly out of scope

None of the following is solved, and none should be read as solved:

- Exactly-once distributed retention.
- Cross-process ledger locking. P3-1's lock is process-local.
- Automatic reconciliation when the remote outcome is unknown.
- Local/remote atomicity across a `persist` failure.
- Any Hindsight server-side transaction spanning the remote write and the local ledger.
- Migration to asynchronous retain with `operation_id`, unless separately approved.
- Undocumented or unverified `document_id` / `update_mode` semantics.
- Synchronous Hindsight client thread-safety.

### Implementation roadmap

Phase identifiers and gates follow
[ADR 001](docs/adr-001-hub-spoke-coordinator.md).

```
P0  Architecture decision                                  implemented
    ↓
P1  Agent delegation seam                                  implemented
    ↓
P2  LLM tool-calling seam                                 implemented
    ↓
P3-0/P3-1  Memory concurrency hardening                   implemented
    ↓
P3-2  Remote state-idempotent retention                   audited; capability confirmed; implementation pending
    ↓
P4  Memory Specialist only, single task, serial            not started
    ↓
P5  Parallel fan-out with join barrier and partial failure not started
    ↓
P6  Code/Log Verifier, then Patch Generator                not started
    ↓
Multi-agent VLSI debugging workflows                      future
```

P4, P5 and P6 are reproduced from the ADR's migration table and are recorded here only as phase
boundaries. **No speculative implementation detail is added for them here**, and the client
thread-safety question remains an open prerequisite before P5.

### Why the memory system does not cross the trust boundary

The separation is a structural property of the code, not a convention that could drift.

- **Memory is knowledge, not evidence.** Hindsight returns prior cases. They are context that can rank
  a hypothesis; they are not observations of the current incident and cannot stand in for one.
- **Memory does not authorize actions.** A retention decision is reported to the engineer and gates
  nothing. Recalling a relevant case does not permit a change, and a confident recall does not
  substitute for a decision.
- **Evidence stays deterministic and independently verifiable.** Evidence is built from the engineer's
  own statements, and repository reads and diffs describe the current state rather than opining about
  it. It can be checked without trusting the model.
- **Memory changes must not alter engineering truth.** The retained record, including failed
  approaches, is written after the engineer's decision. Writing memory changes what is remembered
  next time; it does not change what was true during this incident.
- **Local memory bookkeeping is not an authority over engineering artifacts.** The ledger is an
  idempotency optimization over a local file. It exists to avoid duplicate remote writes, and it must
  never be able to stand in for evidence, or to decide that an engineering artifact should or should
  not exist.

### Planned VLSI engineering direction

The intended future workers target VLSI engineering workflows, including:

- SDC analysis and repair
- Constraint verification
- Timing and debug evidence
- Synthesis and STA investigation
- Place-and-route related analysis
- Log and code inspection
- Patch proposal

**These VLSI-specific capabilities are planned next and are not yet part of the implemented
multi-agent runtime.** No VLSI worker exists, and no VLSI execution has been started.

The trust hierarchy is unchanged by any of it:

```
Memory           → contextual information
Evidence / tools → verification
Agent            → proposal
Engineer         → final decision
```

A patch proposal is never a sign-off. No agent authorizes an engineering change.

### Architectural guarantees

- **Authority separation.** Proposal ≠ evidence ≠ knowledge ≠ authorization. Each of the four is a
  distinct thing and is never substituted for another.
- **Worker capability isolation.** A worker's authority is exactly its `allowed_tools`, re-checked at
  authorization time rather than trusted at declaration time.
- **No arbitrary recursive delegation.** `Coordinator → Worker` only, with `max_spawn_depth` 1 and a
  coordinator-only `task` tool.
- **Memory cannot become evidence.** A recalled case informs hypothesis ranking and never satisfies a
  verification requirement.
- **Memory cannot authorize an engineering action.** Retention decisions gate nothing; they are
  reported, not enforced.
- **Deterministic engineering tools remain authoritative for verification.** Repository reads and
  diffs describe the current state; they are not opinions.
- **Agent output remains a proposal**, and the engineer remains the final decision authority.

***

## Current Candidate Ideas

| # | Idea                                   | One-Line Concept                                                                                       |
| - | -------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| 1 | **Engineering Debugging Memory Agent** | Remembers past technical problems, investigations, and solutions to accelerate future debugging        |
| 2 | **AI Evaluation Memory Agent**         | Remembers past LLM evaluation cases, reasoning, and failure patterns to improve evaluation consistency |
| 3 | **Engineering Incident Memory Agent**  | Remembers past incidents, root causes, and resolutions to speed up incident response                   |
| 4 | **Deal Intelligence Agent**            | Remembers past VC deal evaluations — thesis fit, diligence findings, red flags, decisions, outcomes — to sharpen future diligence |
| 5 | **Competitive Intelligence Agent**     | Remembers past competitive landscape maps — competitor sets, moat theses, accuracy — to calibrate future research |

Detailed breakdown: [docs/project-ideas.md](docs/project-ideas.md)\
Comparison matrix: [docs/idea-comparison.md](docs/idea-comparison.md)

***

## Team Status

**Team Member 1**\
Background: VLSI Physical Design, Engineering workflows, AI evaluation, RLHF, Prompt engineering, LLMs, RAG, AI agents, Technical research\
Personal research: EGER (Evidence-Grounded Engineering Reasoning) — *background inspiration only, not a validated framework*

**Team Member 2**\
Name: Mukul\
Background: To be documented after discussion

***

## Planned Workflow

1. **Review this documentation** — Both members read all documents
2. **Team discussion** — Answer questions in [docs/decision-log.md](docs/decision-log.md)
3. **Select final project** — Record decision in decision log
4. **Implementation planning** — Create detailed spec, architecture, task breakdown
5. **Build** — Implement with Hindsight as central memory layer
6. **Demo preparation** — Record 2–5 minute demo video
7. **Content creation** — Technical article + social post per member
8. **Submission** — GitHub repo, live demo, video, content deliverables

***

## Official Hindsight Resources

* Website: <https://hindsight.vectorize.io/>

* GitHub: <https://github.com/vectorize-io/hindsight>

* Documentation: Refer to official docs for current API and capabilities

***

## Run the Phase 1 agent

The pipeline is integrated on `integration/phase1`. It is stdlib-only apart from `hindsight-client`.

```bash
# 0. dependencies. Both are declared in pyproject.toml; pyyaml is what
#    `debug --input FILE` needs to read the .yaml issue files in demo/inputs/,
#    and without it those files fail with "YAML input needs PyYAML".
python3 -m pip install "hindsight-client==0.10.1" pyyaml

# 1. credentials: HINDSIGHT_URL / HINDSIGHT_API_KEY / HINDSIGHT_BANK_ID, and the LLM_* pair
cp .env.example .env          # then fill it in; .env is git-ignored

# 1b. a fresh bank MUST be paired with a fresh ledger directory.
#     data/memory_ledger.json keys idempotency on sha256(problem_signature|session_id),
#     which does not include the bank id - so a new bank with the default data dir reports
#     all six seeds as "already retained" and stays EMPTY.
#     bash: export HINDSIGHT_BANK_ID=demo-$(date +%s)
#           export DEBUGAGENT_DATA_DIR="$TMPDIR/demo-ledger-$HINDSIGHT_BANK_ID"
export HINDSIGHT_BANK_ID=demo-$(date +%s)
export DEBUGAGENT_DATA_DIR="$TMPDIR/demo-ledger-$HINDSIGHT_BANK_ID"

# 1c. confirm the bank is fresh: before seeding, list_memories should return 404.
#     Do NOT judge freshness by the memory-unit count - it is asynchronous (the same
#     freshly seeded bank was seen at 17 units and later 25, with no further writes).
#     The reliable signal is the seed report in step 2: 6 inserted, 0 skipped, 0 rejected.

# 2. seed the bank once (idempotent - a second run inserts nothing).
#    close() releases the backend HTTP session; without it Python prints
#    "Unclosed client session" / "Unclosed connector" when the process exits.
PYTHONPATH=src python -c "from debugagent.config import load_memory_config; \
from debugagent.memory.hindsight_store import HindsightMemoryStore; \
from debugagent.seeds.loader import load_seed_cases; \
store = HindsightMemoryStore(load_memory_config()); \
print(load_seed_cases(store).to_dict()); store.close()"

# 3. investigate one issue; renders MEMORY / EVIDENCE / PROPOSAL / DECISION
PYTHONPATH=src python -m debugagent.cli debug --env .env

# 4. print the trace of the last session
PYTHONPATH=src python -m debugagent.cli inspect
```

`--memory hindsight` is the default. `--memory offline:demo/offline-memory.json` runs the same loop
with no network, for rehearsing the flow before recording.

Tests: `PYTHONPATH=src python -m unittest discover -s tests`. The Hindsight tests skip unless
`HINDSIGHT_URL` is set; with it set, 9 of them run against the real service.

The trust rule the CLI enforces: **MEMORY informs · EVIDENCE verifies · AGENT proposes · ENGINEER
decides.** A recalled case is never evidence and never satisfies a verification requirement.

## Documentation Index

| Document                                                         | Purpose                                               |
| ---------------------------------------------------------------- | ----------------------------------------------------- |
| [docs/hackathon-requirements.md](docs/hackathon-requirements.md) | Organized requirements, judging criteria, constraints |
| [docs/project-ideas.md](docs/project-ideas.md)                   | Detailed breakdown of 5 candidate ideas               |
| [docs/idea-comparison.md](docs/idea-comparison.md)               | Neutral decision matrix with reasoning                |
| [docs/system-design-notes.md](docs/system-design-notes.md)       | Cross-cutting architecture principles                 |
| [docs/demo-concepts.md](docs/demo-concepts.md)                   | Demo narratives for each idea                         |
| [docs/decision-log.md](docs/decision-log.md)                     | Structured decision tracking                          |
| [docs/hindsight-capability-verification.md](docs/hindsight-capability-verification.md) | Verified Hindsight capabilities vs proposals |
| [docs/project-selection-analysis.md](docs/project-selection-analysis.md) | Evidence-based analysis of the 5 candidates      |
| [docs/final-project-definition.md](docs/final-project-definition.md) | Selected project: problem, workflow, memory, MVP |
| [docs/implementation-plan.md](docs/implementation-plan.md)           | Phased build plan (no code yet)                  |
| [docs/team-task-split.md](docs/team-task-split.md)                   | Proposed Rama/Mukul task split                   |
| [docs/implementation-readiness-review.md](docs/implementation-readiness-review.md) | Readiness verdict + unblock list (no code yet) |
| [docs/domain-neutral-system-design.md](docs/domain-neutral-system-design.md) | Domain-neutral architecture (no domain fixed) |
| [docs/architecture-review.md](docs/architecture-review.md) | Boundaries, Hindsight map, MVP cut, verdict |
| [docs/two-phase-implementation-plan.md](docs/two-phase-implementation-plan.md) | Phase 1 MVP (Sept 29) + Phase 2 (Oct 2) |
| [docs/llm-provider-architecture.md](docs/llm-provider-architecture.md) | Provider-independent LLM adapter design (no code yet) |
| [docs/provider-verification.md](docs/provider-verification.md) | Provider facts/unknowns + runtime-test gates |
| [docs/phase1-execution-plan.md](docs/phase1-execution-plan.md) | Implementation tree + M0–M8 sequence (Sept 29) |
| [docs/phase1-mukul-plan.md](docs/phase1-mukul-plan.md) | Mukul-side milestones MK0–MK9 + contract gaps |
| [docs/phase1-mukul-m0-plan.md](docs/phase1-mukul-m0-plan.md) | MK0 runtime verification (providers, schema, drift) |
| [docs/adr-001-hub-spoke-coordinator.md](docs/adr-001-hub-spoke-coordinator.md) | ADR: Hub-and-Spoke Coordinator pattern, trust hierarchy, migration phases |

***

## Key Constraints to Remember

- Hindsight memory must be **central**, not superficial
- Solve a **real professional problem** for a **specific persona**
- Demonstrate a **visible learning curve** (before/after memory)
- Keep **scope tight** — one workflow, one persona, one value prop
- Use **realistic data**, not toy examples
- Build something **portfolio-worthy**
- Do NOT claim Hindsight guarantees better results
- Separate: *previous experience* → *current evidence* → *agent reasoning* → *final outcome*

---

## Core Design Question

**"What changes because the agent remembers?"**

This is the central evaluation question for all candidate ideas. A strong memory workflow should resemble:

```
Interaction 1
    → experience retained
    → later/new case
    → relevant memory recalled
    → agent behavior changes
    → current evidence checked
    → outcome
    → new experience retained
```

Contrast with a weak pattern:
```
User question
    → retrieve old conversation
    → generate answer
```

Simple conversation-history retrieval is insufficient for demonstrating meaningful persistent memory.

---

## Collaboration

- GitHub is the single source of truth; `main` is the shared stable branch.
- Mukul (`mukul-raii`) is a collaborator.
- Do substantial work in branches (e.g. `rama-planning`, `mukul-ideas`), not directly on `main`.
- Changes are reviewed through Pull Requests before merging.
- Planning decisions are recorded in [docs/decision-log.md](docs/decision-log.md).
- Workflow details: [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/collaboration-workflow.md](docs/collaboration-workflow.md).

---

## Change Tracking

- Significant changes are recorded in [docs/change-log.md](docs/change-log.md).
- Architectural decisions are recorded in [docs/decision-log.md](docs/decision-log.md).
- Git history (`git log`) is the authoritative technical change record.
- Commit planning changes logically with clear messages.
