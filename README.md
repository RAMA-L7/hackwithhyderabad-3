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
| Tool payload resource limits (P3-B)            | Implemented   |
| Hindsight concurrency hardening                | Implemented   |
| Deterministic concurrency regression tests     | Implemented   |
| Remote Hindsight idempotency experiment        | Validated     |
| Remote state-idempotent retention (P3-2B)      | Implemented   |
| Retention failure legibility (P3-2C)           | Implemented   |
| Cross-phase P3-2 integration coverage (P3-2D) | Implemented   |
| VLSI engineering workers                       | Planned       |
| Memory Specialist worker (P4)                  | Implemented   |
| Coordinator execution                          | Planned       |
| Parallel worker fan-out (P5)                   | Planned       |

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
- **The synchronous Hindsight client is not thread-safe.** The P5 concurrency audit established exactly
  why, against the installed client: it lazily creates and caches a single `aiohttp.ClientSession` bound
  to the loop that created it, while its synchronous bridge gives each thread **its own** event loop. A
  second thread reusing that session fails with `RuntimeError: Timeout context manager should be used
  inside a task`. This applies to **reads as well as writes** — `recall` and `retain` share one client
  object. Now mitigated by the P5 serial memory lane; it still constrains parallel *memory* fan-out.
- **Exactly-once is not claimed.** The strongest defensible description is *state-idempotent*: an
  identical repeat converges on the same remote state, while the operation itself is performed again,
  at another extraction/token cost.

### P5 serial memory lane

`HindsightMemoryStore._retain_lock` **cannot** make the client safe, and the audit proved why:

| Property of that lock | Consequence |
|---|---|
| Keyed on the **ledger path**, not the client or bank | Two stores on different ledgers reach one shared client concurrently |
| Covers `retain` **only** | `recall`, `update` and `invalidate` take no lock at all |
| Does not exist during `__init__` | Bank provisioning reaches the client with no lock held |

So the fix is not a better lock in the store — it is **one lane**. `Coordinator` owns a `MemoryLane`, and
every Hindsight access passes through it: the delegated worker, the flow's own direct `recall()`, and the
retention write. `hindsight_store.py` is untouched; its lock comment now says explicitly what it does
*not* protect.

```
investigate(...) ──with hub.lane──▶ recall()            (the flow's own read)
                ──with hub.lane──▶ port.retain(...)     (retention write)
                ──with hub.lane──▶ MemorySpecialist      (delegated work)
```

Properties:

- **Opt-in.** With no Coordinator — the Phase 1 default — there is no lane and the flow is byte-identical.
- **One Coordinator per client.** The lane belongs to the Coordinator, so a Coordinator per session means
  a lane per session, and the boundary would be per-session while the hazard is per-client. A shared
  `MemoryLane` can be passed to several Coordinators; both behaviours are pinned by tests.
- **The lane grants no authority.** Retention still follows only the engineer's resolution, and the
  Memory Specialist still refuses to retain.
- **Authorisation stays outside the lane.** A refusal must not queue behind an in-flight memory call.
- **Fan-out is not implemented.** No thread pool, no join barrier, no partial-failure aggregation, and
  the lane wraps one worker call rather than a batch — so non-memory work can be interleaved later.

#### Composition root: one client -> one Coordinator -> one lane

A lane owned by a Coordinator is only as good as the Coordinator's reach, so the invariant is enforced
where objects are assembled. `debugagent.composition` is that place, and it is the only module that
should build a Coordinator for production use.

- `cli.main()` is the real composition root: it builds one `Runtime` and injects the Coordinator.
- `investigate()` also binds, so the invariant holds for library callers who never import `composition`.
- Re-binding the same Coordinator is free, and is the normal multi-session case.
- A second, different Coordinator over one client **raises `CompositionError`**. This is not a
  convention: a fan-out building one Coordinator per worker would hand the non-thread-safe client one
  lane each and silently serialise nothing.
- The registry keys on the **client**, not the port. Two `MemoryPort` objects over one client - what
  "a port per session" produces - collide, which a port-keyed registry would have missed.
- `unbind(port)` / `Runtime.close()` release a binding. The value is held strongly on purpose, so
  sharing does not depend on a host retaining its own `Runtime`; the cost is one Coordinator per
  client, and the release path is explicit.

#### Fan-out (P5)

The client is the bottleneck, not the CPU, so `Coordinator.fan_out()` parallelises only work that
never enters the Hindsight client. Everything else about the design follows from that.

```
fan_out(specs) ->
    authorise every spec, in input order, outside the lane   # a refusal never starts a thread
    per task, decide the lane from the SPEC's own tools:
        requests a memory tool  -> with self._lane: execute()   # still serialised
        requests none           -> execute()                    # free to overlap
    join barrier (threading.Barrier), then aggregate
```

- **One shared Coordinator, one shared lane.** No worker, thread or task constructs either. The lane is
  reached through `self`, and the composition root still enforces one client -> one Coordinator.
- **The lane decision is per TASK, not per worker.** A routed worker that handles both memory and compute
  work would otherwise serialise its own computation behind its memory calls - exactly the serialisation
- **The lane decision comes from the roster, not the worker.** `client_access` is a field on the
  registered `AgentDefinition`, alongside `allowed_tools` and `model`, so it is part of the P1
  capability model rather than a second system. `memory_specialist` is `client_access=True`;
  `code_log_verifier` and `patch_generator` are `False`.
- **A worker cannot opt out.** Nothing on the worker object is consulted, so a worker that reached
  the client but declared nothing - or declared the opposite - is still serialised. The old
  worker-side flag is gone, and a test pins that it cannot return quietly.
- **Omitting memory tools does not buy unsynchronised access.** A client-access agent is serialised
  for every task it runs; and a memory tool requested by a non-client agent is refused outright by
  `authorize()`. Two independent nets, either sufficient.
  that reaches the client by a route its tools do not describe.
- **Every task keeps its own `TaskOutcome`,** in input order, whatever its status. A failure in a batch
  of successes stays individually visible; nothing is merged into a count that would hide it.
- **`refused` is distinct from `failed`.** A refusal is a rule saying no, and it is never laundered into
  a generic failure - `raise_for_refusals()` is available for callers that require authorisation to hold
  everywhere, without abandoning three good results because one task was malformed.
- **Ordering is positional,** not completion order, so it is deterministic. `joined=False` reports a
  barrier that broke rather than a silently short result set.
- **No retries, no backoff, no exactly-once.** A failing task is attempted exactly once and stays failed.

**Threading is confined.** `registry.py` and `tasks.py` stay pure data. `delegate()` and

#### P6 - Code/Log Verifier (implemented)

Where the Memory Specialist answers "what happened before", the Verifier answers "what does the
CURRENT system actually say" - and that is where its authority stops.

```
Coordinator --task--? CodeLogVerifier --read/grep/glob--? SourcePort
       ?                        �
       +-- SubAgentResult (observations, never a verdict)
```

| Property | How it is enforced |
|---|---|
| **Observes; never verifies** | No result may claim a root cause, a confirmation or a verdict. The disclaimer is *inside* the artifact content, so it cannot be lost by a caller carrying only `content`, and observations carry `code:` / `log:` provenance so they can never read as engineer-stated. |
| **Never touches memory** | Its only seam is a read-only `SourcePort`. A recalled case injected into the task context is **refused**, not quietly read - the registry prompt asks for that, but a refactor away it would not hold. |
| **Only its authorised tools** | `read`, `grep`, `glob`. Each step checks the tool against `spec.allowed_tools`, so a task granting `grep` alone cannot make the worker open a file. |
| **Failure is never "nothing found"** | No targets is a `schema` failure. A missing file is an observation saying so, with contents never inferred. A batch where nothing could be read is `partial`, not `success`. A search that ran and matched nothing is a clean success that says so. |
| **No retention verb** | Asking it to retain is a `schema` failure. Recording an engineering outcome stays an engineer decision. |

`tests/test_p6_code_log_verifier.py` covers 42 unit and integration tests, including dispatch through
`Coordinator.fan_out` and the concurrency proof: verifier work overlaps freely (it is registered
`client_access=False`) while memory work still enters the client one thread at a time.

#### P6 - Patch Generator (implemented)

The third worker, and the one with the narrowest authority: it proposes a change and stops. The
engineer applies it.

```
Coordinator --task--? PatchGenerator --read + diff--? PatchSourcePort  (read-only)
       ?                        �
       +-- SubAgentResult (a PROPOSED diff, never applied)
```

| Property | How it is enforced |
|---|---|
| **Proposes; never applies** | The seam is a `PatchSourcePort` with exactly two operations, `read` and `diff`, and **no mutating method at all** - the absence is in the type, not in this module's discipline. A test asserts no `open`/`write`/`subprocess`/`os` import or call exists. |
| **A proposal is not a verification** | Every artifact is prefixed `PROPOSED PATCH` and states it is unapplied, unverified, and not written/committed/run. Provenance is `code:`, so it can never read as engineer-stated. |
| **Never touches memory** | No memory import or seam, and a recalled case in the task context is REFUSED - a past case is not a basis for changing the current repository. |
| **Only its authorised tools** | `read`, `diff`. Each is checked against `spec.allowed_tools`; a task granting only `read` cannot produce a patch, and both tools are asserted to be *used* when granted. |
| **Four distinct outcomes** | Refusal (`PermissionError` / `AuthorizationError`, before any seam call), insufficient input (`schema`), repository fault (`unavailable`), and successful proposal. A missing target or proposal is **never** reported as "no patch needed" - that belongs only to a real comparison that found the proposal byte-identical, which is a clean success that says so. |

One file per proposal, deliberately: a multi-file change has no single correct diff base, and guessing
one would produce a patch that silently applies to the wrong context. The Coordinator fans out one task
per file. All three workers are registered `client_access=False` except the Memory Specialist, so they
overlap freely while memory work stays serialised.

#### P6 production adapters (implemented)

Both workers now have real filesystem adapters, and the repository boundary lives in them rather
than in the workers - so a worker stays independent of how sources are stored, and a different
backend could satisfy the same Protocol.

```
RepositoryScope (the boundary)          <- one place, every path decision
  +-- FileSourcePort    read / glob / grep   -> Code/Log Verifier
  +-- RepoPatchSourcePort read / diff         -> Patch Generator
```

`build_repository_runtime(root)` is the composition root for both, mirroring `build_runtime` on the
memory side: the scope and the adapters are built once, and the workers are attached to a Coordinator -
the existing one when a caller already has a memory runtime, or a fresh one otherwise.

| Guarantee | How |
|---|---|
| **Containment** | Only paths that resolve INSIDE the root are readable. The check runs on the *resolved* path, so `repo/link -> /etc` is rejected rather than followed. `is_relative_to` compares whole segments, so a sibling named `repo-evil` is not "inside" `repo`. |
| **Glob safety** | A pattern's LITERAL PREFIX is resolved first, because that is where an escape hides - the suffix of `../../etc/*` looks harmless. Every match is then re-checked, because a symlinked directory inside the root can produce a match pointing out of it. Both nets are independently tested. |
| **Read-only** | No write, apply, subprocess or VCS call exists in the module. Proven by shape (no mutating method on either port) and by effect (the tree is byte-identical before and after a full workflow). |
| **Provenance** | Every returned reference is repository-relative and POSIX-separated, so an artifact never leaks the checkout layout and two machines produce the same refs. |
| **Bounded** | A 2 MB per-file read limit, a 500-match grep budget and a 1,000-path glob budget, all enforced rather than left to chance. |
| **Error vocabulary** | Each adapter raises the exception classes its OWN worker catches. Sharing one set would report a security refusal to the engineer as a worker outage. |

A rejected path surfaces as that worker's `schema` failure - an explicit refusal - never as "no such
file", which would be indistinguishable from a genuinely missing file.

#### P5 evaluation: Google ADK as the orchestration layer

#### P6 integration: the workers inside `investigate()`

P6 built three workers and left them with nothing to do - `investigate()` ran the Memory Specialist
and ignored the other two. `investigate()` now takes an optional `repository` (a real
`RepositoryRuntime` over a real directory) plus the caller's intent: `verifier_targets` and
`patch_requests`. Their results land on `session.workers` as one structured record, and the stage runs
after the evidence set is built and before hypotheses are generated, so the proposal can be informed by
what the workers observed.

The interesting decision is where verifier findings are ALLOWED to go. They belong on the evidence SIDE
of the trust boundary - they describe the system as it is, unlike a recalled case - but they are
deliberately NOT `Evidence` items. `evidence.py` and `verify.py` are untouched, an unconfirmed
observation stays an observation, and the engineer confirms it exactly as they confirm any observation.
Folding findings in would let a worker's read of a file stand as a verified property of the system.

| Result | Stands for | May become | Must never |
|---|---|---|---|
| `memory_delegation` | knowledge of PAST cases | context for the engineer | evidence, a decision |
| `workers.verifier` | observations of the CURRENT system | an EVIDENCE-labelled report section | an `Evidence` item, a verdict |
| `workers.patches` | candidate changes | a PROPOSAL section | an applied change, a verified fix |

Two details worth calling out. Findings are **ranked** by how many of the issue's own environment values
and symptoms appear in them, with `(-score, kind, ref, task_id, content)` as the sort key - a total order,
because a sort that can tie would make the output depend on which worker finished first. And
`investigate()` refuses a `repository` whose Coordinator is not the session's own: two Coordinators means
two `MemoryLane`s, and the non-thread-safe client hazard with them.

**Verdict: ADK should WRAP the Coordinator, not replace it, and should stay optional.** A working
prototype lives in `src/debugagent/adk_bridge.py`; `google-adk` is an *optional* dependency and is not
in `pyproject.toml`, so the suite runs unchanged without it (the bridge tests skip).

| Current | ADK | Fits? |
|---|---|---|
| `Coordinator` | `google.adk.workflow.Workflow` + `Node` + `JoinNode` + `START` | yes - the graph maps onto `("START", (memory, verifier, patcher), join)` |
| `Coordinator.fan_out` | graph fan-out | partly - see the blocking finding below |
| join barrier | `JoinNode` | yes |
| `authorize()` in `fan_out` | - | **no** - must stay in the Coordinator |
| `MemoryLane` | - | **no** - `max_concurrency` is graph-wide, not per-resource |
| `TaskOutcome` / refusal split | - | **no** - keep the richer contract |
| `client_access` on `AgentDefinition` | - | **no** - an application-level constraint |
| `SourcePort` / `PatchSourcePort` | - | **no** - unchanged, behind their own boundaries |

**The finding that decided the verdict:** a node body that BLOCKS serialises the graph. Two blocking
nodes under `("START", (a, b), join)` measured 0.63s wall at a concurrency of 1 - against 0.37s and 2
once the work is offloaded with `asyncio.to_thread`. Our workers are synchronous, so wiring `fan_out`
straight into a node would have produced a graph that *looks* parallel and is not - the worst kind of
orchestration bug, invisible until it mattered. The bridge therefore offloads, and a test holds the
blocking case as a control.

Two other frictions worth recording: `FanOutResult.results` excludes refusals, so a node recording only
results would DISCARD a refusal and report a clean run that silently skipped a task - the collector
records refusals too. And ADK requires every node name to be a valid Python identifier, which happens to
suit this repository because the roster's agent ids already are, but would reject a display name.

What ADK gave us that is genuinely worth keeping: a declarative graph with a real join barrier, and a
free assertion that the graph has no cycles. What it cannot give us: per-resource serialisation,
authorisation ordering, or a failure contract that distinguishes a refusal from an outage.
`_execute_authorized()` stay serial. `threading.Thread` and `threading.Barrier` appear only in
`fan_out`, and the tests assert that confinement.


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

### Resource limits (P3-B, implemented)

P2 bounded **turns only** and knowingly left the other payload axes unbounded. P3-B closes them.
Every limit **refuses**; none of them truncates.

| Limit | Value | Enforced at |
|---|---|---|
`MAX_TOOL_CALLS_PER_TURN` | 8 | `parse_tool_calls` — before any call is parsed or executed |
`MAX_TOOL_ARGUMENT_CHARS` | 16,000 | `parse_tool_calls` — the data boundary for provider input |
`MAX_TOOL_RESULT_CHARS` | 32,000 | `ToolResultMessage.from_dict` **and** the tool loop |
`MAX_TOOL_DEFINITION_CHARS` | 8,000 | `ToolDefinition.from_dict` — before a definition is offered |
`MAX_CONVERSATION_CHARS` | 256,000 | `LLMRouter.complete_with_tools` — before each request is sent |

Values are sized from measured real payloads, not invented: the six shipped seed cases render to
796–970 characters, and a tool result is a file excerpt or log tail, not a document. The call cap of
8 covers the three-worker planned roster with headroom.

**Why refusing, not truncating.** A truncated tool result is the failure mode that matters here: the
model cannot tell a clipped log from a complete one, and **a clipped log is engineering evidence that
looks whole**. Every error message says the payload was not truncated and names the limit, so the
caller narrows its query rather than proceeding on partial data. The conversation cap is checked
*before* the request, so an oversized turn costs nothing and fails with a typed
`LLMToolResourceLimit` instead of an opaque provider rejection. Per-item breaches raise
`ToolResourceLimit`, which subclasses `ToolCallError` so existing handlers keep working unchanged.

A result at exactly the cap is accepted — the boundary is not off-by-one — and an accepted result
arrives byte-identical, which the tests pin.

**Not covered:** these bound the tool loop only. They do not bound the memory layer's stored payload
(which has its own pre-existing caps) or the evidence path, which the ADR explicitly defers.

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
P3-2  Remote state-idempotent retention                   implemented
    ↓
P4  Memory Specialist only, single task, serial            implemented + wired into the demo flow
    ↓
P5  Parallel fan-out with join barrier and partial failure done - non-client work only; lane + composition root done
    ↓
P6  Code/Log Verifier, then Patch Generator                both implemented
    ↓
Multi-agent VLSI debugging workflows                      future
```

P4, P5 and P6 are reproduced from the ADR's migration table and are recorded here only as phase
boundaries. **No speculative implementation detail is added for them here**, and the client
thread-safety question is now closed and answered by the serial memory lane; fan-out itself remains unimplemented.

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

### P4 — Memory Specialist (implemented)

The **first real worker**. It executes **one** authorised task at a time, serially, through the
existing `MemoryPort` seam, and returns a `SubAgentResult` to the Coordinator.

```
Coordinator ──task──▶ MemorySpecialist ──recall_and_classify──▶ MemoryPort
       ▲                     │
       │                     └── SubAgentResult (success | partial | failed)
       │
   SubAgentResult carries memory: observations — a proposal input, nothing more
```

What it does:

- **Recalls** past cases for the Coordinator and reports them as `memory:`-provenance artifacts.
- **Refuses retention.** A `retain` objective returns `status="failed"` with the reason *"retention is
  an engineer decision, not a worker action"*. The worker is given no authority to record an
  engineering outcome, so that decision cannot be inherited by accident.
- **Runs every task through the P1 authorisation seam** — `build_task_spec` for construction,
  `authorize()` before execution — so depth, tool scope, and agent identity are checked by the same
  code that governs every other delegation.

Three properties are enforced structurally, not by prompt:

| Property | How it is enforced |
|---|---|
**MEMORY informs, never verifies** | The worker is constructed with a `MemoryPort`, never an evidence builder. `evidence.py` and `verify.py` are not importable from the `agents` package, and a test asserts the module never names them. Observations are explicitly labelled `MEMORY (past case, not current-system evidence)`. |
**Failure is never "nothing found"** | A clean empty recall is a **success** carrying the abstention reason. An unreachable backend is a **failure** with a `failure_kind`. Collapsing the two would tell the engineer their memory is empty when it is simply down. |
**Nothing authorizes** | Retention is refused by the worker. `SubAgentResult` is a proposal the Coordinator may rank; it can never satisfy a verification requirement. |

Failure mapping: the port's `kind` is mapped onto the agent vocabulary
(`unavailable`/`timeout`/`auth`/`invalid_output`/`schema`). P3-2C's `ambiguous` and `persist` map to
`unavailable` because the agent vocabulary has no member for them, and the **precise kind is preserved
in `failure_detail`** so the distinction is not lost. Failures are matched structurally on `kind`
rather than by `isinstance`, so the worker handles the real `HindsightMemoryPort` exception without
importing the Phase 1 pipeline — a non-memory exception still propagates, because a bug must never be
reported to the engineer as a memory outage.

**P4 scope, held to:** one task, serial. No scheduler, no worker pool, no fan-out, no concurrency in
the module. The Coordinator, the other two workers, and parallel execution remain **not** implemented.

**The ADR's P4 gate has passed.** Each act fixture in `demo/inputs/` is normalised through the real
`load_debug_input` / `normalize` path, and the resulting signature is dispatched through
`build_task_spec` → `authorize()` → `MemorySpecialist` against a **fresh bank directory per act**,
seeded with the real seed cases — so the real `classify_candidates` scoring runs against genuine stored
memory rather than a stub. All four acts recall; the abstention path, the backend-failure path, and
the retention refusal are each exercised, and the output contract is unchanged (the result still
round-trips through `SubAgentResult.from_dict` and a recall adds no ledger entry).

That rehearsal caught a real defect the unit tests could not: the worker read `view["candidates"]`
while `HindsightMemoryPort` nests the classified rows under `view["report"]["candidates"]`, so against
a real port **every** recall would have reported "no relevant past case". The worker now reads the
nested form, with the flat form accepted for simpler port implementations.

#### Wired into the demo flow

A worker that is never constructed is not a feature, so `investigate()` can now be given one:

```python
investigate(raw, port, llm, engineer, memory_specialist=MemorySpecialist(port))
```

The delegation is **optional and additive**. With no worker — the Phase 1 default — `to_dict()` emits
exactly the keys it always did, the rendered sections are byte-identical, and the trace is unchanged.
Supplying a worker adds one step, one trace line, one `memory_delegation` key, and one rendered
section, and changes nothing else.

| Concern | Behaviour |
|---|---|
| **Reachability** | `session.memory_delegation` records the worker's report, and it is rendered by `render_memory_delegation()` as its own MEMORY section. |
| **Authorisation** | The task is built with `build_task_spec` and passed through `authorize()` before execution — the same seam every other delegation uses. |
| **Side channel, not replacement** | The ranked `session.memory` the proposal is built from is *still* produced by the existing `recall()`. The worker's observations are a separate report. |
| **Trust boundary** | The worker is constructed with a `MemoryPort` only. Its observations are labelled `not current-system evidence` and never enter `build_evidence`. |
| **Failure isolation** | A backend outage, an empty recall, and even a worker that raises are all reported and the session continues. The engineer's investigation never depends on a worker being available. |
| **P4 scope held** | One task per session, serial. No scheduler, no pool, no fan-out, no concurrency. |

`tests/test_p4_wiring_integration.py` drives 26 full sessions through the real flow and asserts each
row above. Two of those tests are worth calling out, because they encode the rule the wiring exists to
protect:

- `test_worker_observations_cannot_fill_an_evidence_gap` builds a case whose current evidence is
  **missing** the service and runtime that memory knows. `verify()` must return
  `insufficient_evidence` even though the worker reported that environment in full. Memory informs;
  it never fills a gap.
- `test_worker_observations_flag_a_mismatch_without_upgrading` covers the neighbouring case: the
  environment is stated but **differs**. The verdict still stands, and the disagreement is surfaced in
  `mismatched_environment_fields` rather than resolved in memory's favour.

Each of these was checked by mutation: breaking the dispatch, the disclaimer, or the
absent-worker key made the relevant test fail, so none of them pass vacuously.

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
