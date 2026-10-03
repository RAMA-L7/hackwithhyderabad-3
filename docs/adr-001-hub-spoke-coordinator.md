# ADR 001: Hub-and-Spoke Coordinator Pattern & Tool-Calling Prerequisites

## Status

Accepted (Design Phase — P0). Implementation progress: **P0, P1, P2, P3-0, P3-1, P3-2, P3-B and P4
are implemented**, and the P4 worker is wired into the demo flow. P5 and P6 are not started.

This ADR was itself documentation only, and it did not authorise implementation beyond P0. Subsequent
phases were implemented on their own gates, recorded in [Migration Phases](#migration-phases) below;
the decision and prerequisites recorded here are unchanged by that implementation.

The architecture this ADR describes is still **partly aspirational**. No worker executes, and there is
no Coordinator runtime or parallel fan-out; what exists is the delegation seam (P1), the tool-calling
transport (P2), and local concurrency hardening (P3-1).

## Context

Phase 1 is a single-shot debugging agent. `investigate()`
(`src/debugagent/pipeline/investigate.py:90-141`) runs one linear, synchronous pass:

```
normalize -> recall -> EVIDENCE -> hypotheses -> engineer verifies -> engineer resolves -> retain
```

Exactly one LLM call is made per investigation, through
`LLMRouter.complete_structured()` (`src/debugagent/llm/router.py:216`). The whole design
deliberately minimises LLM calls and keeps the trust boundary structural rather than instructional.

To scale diagnostic capability — logs, code inspection, patch drafting — we evaluated a Hub-and-Spoke
topology: one Coordinator that decomposes and delegates, plus specialised workers that do not
delegate further.

## Architectural Findings

### 1. Current LLM layer: single-shot, no tool dispatch

`complete_structured()` builds a fixed request payload (`router.py:247-254`): `model`, one user
`messages` entry, `max_tokens`, and `response_format`. There is no `tools` key, no `tool_choice`, and
no multi-turn message history. A `check()` callback lets a caller reject invalid output, but output is
terminal — there is no loop to feed a tool result back.

A repository-wide search for `tool_call`, `tools=`, `function_call`, `parallel`, `ThreadPool`,
`concurrent`, `asyncio` and `spawn` returns **no production hits**. There is no tool registry, no
dispatch table, and no agent abstraction.

**Consequence:** the `task` tool has no host to live in. This is new infrastructure, not
configuration.

### 2. Parallel opportunities are narrower than they first appear

| Opportunity | Viable? | Reason |
|---|---|---|
| Overlap recall with hypothesis generation | **No** | `build_prompt()` (`hypothesize.py:52-65`) embeds `memory.citable()`; recall is a hard dependency |
| Overlap evidence collection with recall | Partial | Independent, but the engineer is one human at one terminal |
| Log/AST inspection ∥ Hindsight recall | **Yes** | The clearest win; log parsing is also new capability |
| Parallel per-hypothesis verification | **No** | `verify()` requires one engineer decision per hypothesis |
| Parallel retention | **No** | Last stage, and idempotency-sensitive |

The existing loop is largely inherently serial. Real latency reduction comes from *adding*
capabilities, and costs more LLM calls, not fewer. That trade must be an explicit decision.

### 3. Memory layer is not concurrency-safe as written

`HindsightMemoryStore` (`src/debugagent/memory/hindsight_store.py`) holds:

- `self._client` (line 182) — a shared client wrapping an aiohttp `ClientSession`/`TCPConnector`
- `self._ledger` (line 183) — an in-memory dict persisted by read-modify-write to `ledger.json`
  with **no file locking**
- `retain()` (lines 310-333) — a check-then-act sequence: `ledger.has()` → `client.retain()` →
  `ledger.record()`

Concurrent retains of the same case can race and double-write. Any parallel worker design must
serialise memory access or isolate stores per worker.

### 4. Target layout

`origin/mukul-loop` already carries a layered architecture — `controllers/`, `services/`, `domain/`,
`ports/`, `adapters/`, `views/` (34 files) with `tests/test_loop_architecture.py` enforcing import
rules. Its `LLMPort` is the same single-shot `complete_structured`, so it does not solve
tool-calling, but it provides the natural home for a Coordinator and workers, with a layer test
already in place.

**The flat `pipeline/` is the wrong host.** `memory_port.py:1-7` documents itself as *"the only seam
between the pipeline and Rama's memory layer"*. Placing an agent layer inside that package would
erode a boundary the codebase is built on. Topology work should land on `mukul-loop`.

## Decision & Guiding Rules

### P0 Isolation

Documentation only. No changes to `investigate()`, `verify()`, `evidence.py`, `hindsight_store.py`,
`LLMRouter`, thresholds, or the engineer interaction contract. `semantic_floor` remains `0.75`.

### Trust Hierarchy

The Phase 1 rule is unchanged and remains the governing constraint:

```
MEMORY informs · EVIDENCE verifies · AGENT proposes · ENGINEER decides
```

Mapping for the multi-agent topology:

| Source | Role | Never becomes |
|---|---|---|
| Hindsight memory (Memory Specialist) | Prior experience | Evidence about the current system |
| System logs / AST (Code-Log Verifier) | Current-system observation | Verification without engineer review |
| Coordinator + workers | Proposal | Decision |
| Human engineer | Approval / decision | — |

**Refinement on the "logs/AST → Current Evidence" row.** `EvidenceItem` already carries a `source`
field, and `build_evidence()` / `add_facts()` default it to `"engineer"` (`evidence.py:32,40`).
Tool-derived observations **may** enter EVIDENCE — they are genuine current-system facts — but they
**must** carry a distinct, non-`engineer` `source` (e.g. `log:nginx-error`, `code:src/app.py`).

This is deliberate. `evidence.py:3-4` states there is *"no parameter through which recalled memory
could enter"*. Applying that same discipline to tool output prevents a subtler breach: an
LLM-authored log summary entering EVIDENCE under an `engineer` label would let a hypothesis be
"verified" by something the engineer never actually saw or approved. Provenance must stay visible
through to the DECISION section.

### Tool Spawning

**Tool-calling is P2 work, not P1.** P1 delivered the delegation *seam* only. Nothing in P1 emits a
`task` tool call, and no worker is executed by P1.

| Delivered in P1 (seam only) | Introduced in P2 |
|---|---|
| `TaskSpec` — validated delegation request | `tools` / `tool_choice` in the `LLMRouter` request payload |
| `WorkerContext` — immutable, explicitly supplied worker context | An assistant turn carrying `tool_calls` |
| `SubAgentResult` — validated worker result, untrusted input | `role: "tool"` messages carrying results |
| Closed worker registry (three agents, four-field payloads) | A bounded tool-call turn loop |
| Tool authorization (`task` is coordinator-only) | `task` tool **execution** and actual delegation |
| Anti-recursion enforcement (`max_spawn_depth = 1`) | A join barrier with per-task timeout and partial-failure semantics |
| Context-isolation structures | Concurrency-safe access to the memory layer (P3) |

Governance, already enforced structurally in P1 and carried into P2:

- `task` is granted **only** to the Coordinator. Sub-agent `allowed_tools` structurally exclude it,
  and `TaskSpec.from_dict` independently refuses the `task` tool, so the rule holds even if the
  registry is bypassed.
- The dispatcher resolves tools from a registry keyed by calling-agent identity, so a worker cannot
  request `task`. A `max_spawn_depth = 1` counter is defence in depth.
- Enforcement must be structural, not advisory — the same "absent channel, not a prompt" discipline
  the codebase already applies in `evidence.py`.

#### Known limitation (P2): no resource limits on tool payloads

Recorded from the P2 read-only boundary review. In P2 the following are **currently unbounded**:

- the number of tool calls the provider may return in a single assistant turn;
- the size of a tool call's `arguments`;
- the size of a tool result's `content`;
- the accumulated conversation history, which re-sends every prior turn on each request.

`max_tool_turns` (default 4) bounds **turns only**. It does not bound bytes, and it does not bound the
tool-call count within a turn. A single P2 tool loop has therefore been observed to build a
multi-hundred-kilobyte request without raising, and an oversized request would surface as an opaque
provider error rather than a typed failure.

**This is intentionally not solved in P2.** P2 is transport shape and validation only; it has no
knowledge of what a tool returns, so any limit placed here would be a guess. No numeric ceiling is
recorded in this ADR on purpose.

The real host and tool-execution layer must establish resource limits before any worker is executed —
at minimum a cap on tool calls per turn, a cap on tool-result content size, and a cap on total
conversation size. **Resolved by P3-B**: `MAX_TOOL_CALLS_PER_TURN` (8),
`MAX_TOOL_ARGUMENT_CHARS` (16k), `MAX_TOOL_RESULT_CHARS` (32k), `MAX_TOOL_DEFINITION_CHARS` (8k) and
`MAX_CONVERSATION_CHARS` (256k) are enforced at the boundary each protects, so the prerequisite before
**P4** and **P5** is met. **Every limit refuses rather than truncates**: a clipped tool result is
indistinguishable from a complete one, and a clipped log is engineering evidence that looks whole,
which the trust hierarchy forbids. Values are sized from measured real payloads, not invented.

### Agent Definition Payloads

Each approved worker carries exactly four architectural fields and no others:
`description`, `prompt`, `allowed_tools`, `model`. The registry is closed; an unknown agent identity
is rejected rather than created. All three use `model: "primary"`.

**Memory Specialist** — produces MEMORY information (prior experience).

```json
{
  "description": "Recall and characterize past debugging cases from Hindsight for the current issue. Return case IDs, relevance, environment, and failed approaches. Never verify the current system.",
  "prompt": "Work only on the current issue and supplied bank context. Report recalled cases and their relevance. Past cases are historical memory, never current-system evidence. Never use a past environment to describe the current system. Abstain when relevance is below the usable floor. Do not delegate.",
  "allowed_tools": ["hindsight_recall", "hindsight_get_facts"],
  "model": "primary"
}
```

**Code/Log Verifier** — produces current-system observations.

```json
{
  "description": "Inspect repository code and logs for the current issue and report observed facts with file paths, line references, or log references. Do not infer root cause and do not delegate.",
  "prompt": "Inspect only the supplied current-system artifacts. Report directly observed facts and their provenance. Do not convert observations into hypotheses, conclusions, or engineer decisions. Do not delegate.",
  "allowed_tools": ["read", "grep", "glob"],
  "model": "primary"
}
```

**Patch Generator** — produces a proposal.

```json
{
  "description": "Draft a unified diff as a proposal after a hypothesis exists. Never apply or execute the patch and never make the engineering decision.",
  "prompt": "Draft a minimal unified diff only when the supplied hypothesis and facts are sufficient. Do not apply the patch, execute commands, or run tests. Do not repeat a previously failed approach unless the difference is explicitly explained. If a required fact is missing, report insufficient information. Do not delegate.",
  "allowed_tools": ["read", "diff"],
  "model": "primary"
}
```

**What no worker may do.** None of the three can make the ENGINEER decision. None can write
engineer-approved EVIDENCE — per the refinement above, tool-derived observations must keep a
non-`engineer` source all the way to the DECISION section. None can invoke `task` delegation. None
can retain memory or apply a patch. The governing rule is unchanged:

```
MEMORY informs · EVIDENCE verifies · AGENT proposes · ENGINEER decides
```

> **Prompt-text reconciliation (P1 follow-up).** The prompts above are the canonical statement of
> intent. The payloads shipped in P1 (`src/debugagent/agents/registry.py`) carry longer, equivalent
> phrasing with **identical** `allowed_tools` and `model` for all three workers. Tool permissions and
> model selection are therefore already in agreement; only the prompt wording differs, and
> reconciling it is a documentation-vs-code follow-up, not an architecture change.


### Aggregation is untrusted input

Worker output is re-validated against per-worker schemas before use. If the Coordinator aggregates
Memory Specialist output into a candidate set, that set **must** pass `check_view()`
(`memory_port.py:66`) before any hypothesis may cite it, so that `citation_check()`
(`hypothesize.py:68-74`) can still reject invented ids.

### Failure is never "nothing found"

A timed-out or failed sub-agent must surface as `MemoryFailure(kind="unavailable")`, not an empty
candidate list. `MemoryPort` already enforces this distinction; workers must preserve it. Otherwise
the system abstains quietly and appears correct while having looked in the wrong place.

### SubAgentResult status vocabulary (P1)

A worker result carries exactly one status, so incomplete execution can never be read as a clean one:

| Status | Meaning | Shape enforced |
|---|---|---|
| `success` | The worker completed and produced findings | at least one observation, no `failure_kind` |
| `partial` | The worker produced some findings and was degraded by a failure | at least one observation **and** a `failure_kind` |
| `failed` | The worker produced nothing usable | a `failure_kind`, **no** observations |

Failure information stays **structured and distinguishable**. A failure is never silently converted
into an empty result: `failed` without a `failure_kind` is rejected at validation, so a timed-out or
unavailable worker can never be flattened into "nothing found" and mistaken for a clean abstention.

`failure_kind` uses the vocabulary the codebase already raises, so a worker failure maps onto an
existing boundary rather than inventing a new one:

| `failure_kind` | Maps to |
|---|---|
| `unavailable` | `MemoryFailure(kind="unavailable")` — `memory_port.py:18` |
| `auth` | `MemoryFailure(kind="auth")`; also `LLMError.error_class == "AUTH"` — `router.py:33` |
| `schema` | `MemoryFailure(kind="schema")` — `memory_port.py:18` |
| `timeout` | `LLMError.error_class == "TIMEOUT"` — `router.py:32` |
| `invalid_output` | `LLMError.error_class == "INVALID_OUTPUT"` — `router.py:240` |

This is recorded as implemented, not redesigned. A `SubAgentResult` is **untrusted input**: per the
aggregation rule above it must be re-validated, and it is never a hypothesis, an evidence item, or an
engineer decision.

## Consequences

**Positive**

- The topology becomes expressible without weakening the trust boundary.
- Prerequisites (tool loop, concurrency safety, schemas) are identified before any code is written.
- Hosting on `mukul-loop` avoids eroding the `pipeline/` seam.

**Negative / accepted costs**

- A Coordinator plus three workers is four LLM calls, not one. Cost and latency determinism both
  worsen; the gain is breadth of diagnostic capability.
- `HindsightMemoryStore` must be made concurrency-safe before any fan-out touches it.
- **This topology is incompatible with the frozen Phase 1 demo.** Per
  `docs/two-phase-implementation-plan.md` §14–15, no multi-agent item is pre-approved and "anything
  touching the loop needs a re-rehearsal budget." This is post-submission work.

**Risks**

- `MemoryPort` signature changes ripple to `HindsightMemoryPort`, `OfflineMemoryPort` and
  `FakeMemoryPort` (`tests/loop_support.py`).
- 356 tests across 20 files encode current behaviour, including trust-boundary assertions.
- `Session.to_dict()` is the `inspect` output format read by the demo.

## Migration Phases

Each phase is independently shippable and revertible; each has an explicit gate.

| Phase | Scope | Gate | Status |
|---|---|---|---|
| **P0** | This ADR. Documentation only. | review | done |
| **P1** | Delegation **seam** only: `TaskSpec`, `WorkerContext`, `SubAgentResult`, closed worker registry, tool authorization, anti-recursion enforcement, context-isolation structures. No LLM changes, no tool calling, no worker execution. | unit tests for the anti-recursion rule | done — 37 tests |
| **P2** | Tool calling: `tools`/`tool_choice` in `LLMRouter`, assistant `tool_calls` turn, `role: "tool"` results, bounded turn loop, `task` execution. Default path byte-identical. | all prior tests green; new tool-loop tests | done — 46 tests |
| **P3-0/P3-1** | Deterministic concurrency regression coverage, then process-local concurrency safety for `HindsightMemoryStore` (path-keyed lock + single writer). | concurrent recall/retain test | done — 12 concurrency tests |
| **P3-B** | Resource limits on tool payloads and the conversation, enforced at the data boundary and the loop. Refuses, never truncates. | limit tests incl. at-the-boundary acceptance | done — 29 tests |
| **P3-2** | Cross-system retention: local ledger persistence vs remote Hindsight write. Phase A case identity; P3-2B state-idempotent remote replacement (`document_id=case_key`, `update_mode="replace"`); P3-2C failure legibility (typed `persist` / `ambiguous` / `unavailable`, `success=False` handling, corrupt-ledger surfacing); P3-2D cross-phase integration coverage. Local/remote atomicity still outstanding. | identity-contract, failure-classification and cross-phase integration tests | done — A, B, C, D |
| **P4** | Memory Specialist only, single task, serial, wired into the demo flow. | Acts 1–4 rehearsal on fresh banks, output unchanged | done — 28 rehearsal + 26 wiring tests |
| **P5** | Serial memory lane, composition root, then parallel fan-out with join barrier and partial failure - non-client work only. | client-serialisation, composition and fan-out tests | lane, composition root and fan-out done - 29 fan-out tests; no P6 worker exists |
| **P6** | Code/Log Verifier, then Patch Generator. | authority-boundary and fan-out concurrency tests | done - 42 + 41 tests |

Suite at the P3-1 checkpoint: 349 tests, 9 skipped. **P3-2 Phases A–D, P3-B and P4 have since
landed**, taking the suite to **583 tests, 9 skipped** (529 before the P4 demo-flow wiring).

**P3-2, in brief.** A live capability experiment established that a fixed Hindsight `document_id` with
`update_mode="replace"` gives *state-idempotent* remote replacement, including after an ambiguous
outcome. `replace` is **destructive**, so it is safe here only because the case identity is
content-addressed: Phase A made `compute_case_key()` incorporate an outcome digest, and fixed
`MemoryCase.to_dict()` silently dropping `session_id` — both pinned by tests. Retain now sends
`document_id=case_key` with `update_mode="replace"`.

**Not claimed, and not achievable:** exactly-once semantics. A retry still issues another remote
request and pays another extraction/token cost; only the resulting remote *state* is the same.

P3-2C makes failures legible rather than atomic. Retention now distinguishes `persist` (the remote
write may have succeeded, the local ledger did not — nothing was rolled back), `ambiguous` (transport
broke with no answer; the remote outcome is unknown) and `unavailable` (the service answered and
failed, so a retry is clean). A pre-transmission failure stays clean, and a corrupt ledger is
surfaced rather than silently read as empty.

Phase D adds cross-phase integration coverage: the computed identity reaching `document_id`, one
identity used consistently across ledger/metadata/remote document, the key surviving into recall and
`dedupe_by_case`, and P3-1's serialized retain composing with P3-2B's document identity under
concurrency.

**P4** lands the first real worker: `MemorySpecialist`, one task at a time, serial, through the
existing `MemoryPort` seam. Three properties are structural rather than instructional. It is
constructed with a `MemoryPort` and never an evidence builder, so recalled content has no path into
evidence construction. A clean empty recall is a SUCCESS carrying the abstention reason while an
unreachable backend is a FAILURE with a `failure_kind`, so "failure is never nothing found" holds.
And retention is refused by the worker itself, because recording an engineering outcome is the
engineer's decision. Failures are matched structurally on `kind` rather than by `isinstance`, which
keeps the agents package free of any Phase 1 pipeline import while still handling the real
`HindsightMemoryPort` exception. The Coordinator, the Code/Log Verifier, the Patch Generator, and
parallel fan-out remain unimplemented.

The **P4 gate rehearsal** has passed. Each act fixture in `demo/inputs/` is normalised through the
real `load_debug_input` / `normalize` path, and the resulting signature is dispatched through
`build_task_spec` -> `authorize()` -> `MemorySpecialist` against a **fresh bank directory per act**,
seeded with the real seed cases, so the real `classify_candidates` scoring runs on genuine stored
memory. All four acts recall; the abstention path, the backend-failure path and the retention refusal
are each exercised. The rehearsal found and fixed a real defect: the worker read `view["candidates"]`
while `HindsightMemoryPort` nests the rows under `view["report"]["candidates"]`, so **every** recall
would have reported "no relevant past case" against a live port. The 45 unit tests did not catch it
because they supplied a flat view.

Output contract unchanged: the result still round-trips through `SubAgentResult.from_dict`, carries
only the six defined P1 fields, and a recall adds no ledger entry. `verify()` remains the authority —
with a recorded engineer decision, a hypothesis citing a recalled case whose `region` the engineer
never stated still resolves to `insufficient_evidence`, with the unknown field reported as a gap.

**The worker is now wired into the demo flow.** `investigate()` accepts an optional
`memory_specialist=`. The delegation goes through `build_task_spec` -> `authorize()`, and the report
is recorded on `session.memory_delegation` and rendered as its own MEMORY section by
`render_memory_delegation()`. Two properties were treated as non-negotiable:

- **The default path is untouched.** With no worker — the Phase 1 default — `to_dict()` emits exactly
  the keys it always did, rendering is byte-identical, and the trace is unchanged. The key is
  conditional on the delegation having happened, so adding memory cannot alter existing demo output.
- **The worker is a side channel, not a replacement.** The ranked `session.memory` the proposal is
  built from is still produced by the existing `recall()`. The worker's observations are reported
  separately and are never passed to `build_evidence`; retention still follows from the engineer's
  resolution, never from the worker.

A worker fault — an unreachable bank, an empty recall, or a worker that raises — is reported on the
session and the investigation continues, because the engineer's session must not depend on a worker
being available. `tests/test_p4_wiring_integration.py` drives 26 full sessions through the real flow
to hold each of these. The trust rule is asserted in both of its directions: memory that would **fill**
a gap still yields `insufficient_evidence`, and memory that would **differ** from stated evidence is
surfaced in `mismatched_environment_fields` without downgrading a verdict the engineer reached. Each
was checked by mutation — breaking the dispatch, the disclaimer, or the absent-worker key fails the
corresponding test.

**Still outstanding:** local/remote atomicity, automatic retry (there is none by design),
reconciliation of an unknown remote outcome, and cross-process locking. The synchronous Hindsight
client is **not thread-safe** for concurrent access of any kind, reads included.

**The P5 serial memory lane.** The audit that closed the thread-safety question found the store's
`_retain_lock` cannot make the client safe: it is keyed on the **ledger path**, covers `retain` only,
and does not exist during `__init__`. So serialisation was added one level up, on the Coordinator, as a
`MemoryLane` that every Hindsight access passes through - the delegated worker, the flow's own direct
`recall()`, and the retention write. `hindsight_store.py` is unchanged; its lock comment now states
plainly what it does not protect. Three consequences are worth recording:

- **The lane is per Coordinator, so P5 must use one Coordinator per client.** A Coordinator per session
  would make the boundary per-session while the hazard is per-client. A `MemoryLane` may be shared
  between Coordinators, and both behaviours are pinned by tests rather than left to a comment.
- **The lane grants no authority.** Retention still follows only the engineer's resolution.
- **P3-1's recall-may-overlap-retain guarantee holds for the fake client only.** Against the real
  backend that overlap fails, and it is now prevented by the lane. The P3-1 test and its scope note say

**The composition root.** A lane owned by a `Coordinator` is only as good as that Coordinator's reach,
so `debugagent.composition` enforces the invariant where objects are assembled: one Hindsight client
has exactly one `Coordinator` and therefore one `MemoryLane`. `cli.main()` is the real composition root
and injects it; `investigate()` binds as well, so the rule holds for library callers. A second,
different Coordinator over one client raises `CompositionError` rather than being discouraged - the
failure it prevents (one lane per worker) would silently serialise nothing. The registry keys on the
client rather than the port, so two ports over one client collide. `unbind` and `Runtime.close()` are
the release paths; the value is held strongly on purpose so that sharing does not depend on a host
retaining its own `Runtime`.

**Fan-out.** `Coordinator.fan_out()` dispatches several authorised tasks in parallel and joins them. The
client is the constraint rather than the CPU, so only work that never enters it is parallelised: the lane
is taken per task, decided from the spec's own `allowed_tools` rather than from a per-worker flag, because a
routed worker handling both memory and compute work would otherwise serialise its own computation. Memory
tasks still enter the client one thread at a time. Authorisation runs first, in input order, outside the lane,
so a refused task never starts a thread and does not abort its siblings; every task keeps its own outcome in
input order, so no failure is hidden by a sibling's success and `refused` stays distinct from `failed`.
Ordering is positional rather than completion-ordered, and a broken join barrier is reported as
`joined=False` instead of a silently short result set. No retries, no backoff, and no exactly-once claim.

**Client access is a structural capability.** Whether a task may skip the lane is decided by the
registered `AgentDefinition's `client_access` field - part of the P1 capability model, not a second
system - together with the task's own `allowed_tools`. Only `memory_specialist` is `client_access=True`;
`code_log_verifier` and `patch_generator` are `False`. The field is REQUIRED rather than defaulted, because
defaulting it to `True` would silently serialise compute work and defaulting it to `False` would let a
client-touching agent escape the lane. Nothing on the worker object is consulted, so a worker cannot opt
out of the shared lane by declaring itself non-client; a task cannot opt out by omitting memory tools, since
a client-access agent is serialised for every task and a memory tool requested by a non-client agent is
refused by `authorize()`.

**The Code/Log Verifier (P6).** The second real worker, registered `client_access=False`, so its tasks run
outside the shared lane and overlap freely while memory work stays serialised. Its only seam is a read-only
`SourcePort`; there is no Hindsight import and a recalled case injected into the task context is refused
rather than read, because a past case must not be laundered into an observation about the current system.
Each step checks its tool against the task's `allowed_tools`, so a task granting `grep` alone cannot make the
worker open a file. Insufficient input is explicit: no targets is a `schema` failure, a missing file is an
observation saying so with contents never inferred, a batch where nothing could be read is `partial` rather
than `success`, and a search that genuinely ran and matched nothing is a clean success that says exactly that.
Observations carry `code:` / `log:` provenance and an in-content disclaimer, so they can be read as evidence
The Patch Generator follows the same shape: registered `client_access=False`, tools `read` and `diff`, and a read-only `PatchSourcePort` with no mutating method, so there is no path from the worker to a modified working tree. Every artifact is prefixed `PROPOSED PATCH` and states it is unapplied and unverified. One file per proposal, because a multi-file change has no single correct diff base. Refusal, insufficient input (`schema`), repository fault (`unavailable`) and a successful proposal are four distinct outcomes, and missing input is never reported as `no patch needed` - that belongs only to a real comparison that found the proposal byte-identical.

**Production adapters.** Both P6 workers are driven by real filesystem adapters, and the repository
boundary is enforced in the adapter rather than in the worker, so the workers stay independent of how
sources are stored. `RepositoryScope` resolves a candidate to a real path and refuses anything landing outside
the root - `..` traversal, absolute paths elsewhere, and symlinks pointing out - and the check runs on the
RESOLVED path, so a symlink cannot step outside. Glob patterns have their literal prefix resolved before the
filesystem is consulted, and every match is re-checked; each is a net the other does not cover. Both ports are
read-only: no write, apply, subprocess or VCS call exists in the module, proven by shape and by the repository
being byte-identical after a full workflow. Every returned reference is repository-relative, so an artifact never
discloses the checkout layout. `build_repository_runtime` composes both through the same root that composes
memory, and a rejected path is reported as the worker's `schema` refusal rather than as a missing file.

**Evaluation: Google ADK as the orchestration layer.** A prototype in `debugagent.adk_bridge` maps the
three workers onto an ADK `Workflow` graph, `("START", (memory, verifier, patcher), join)`, and hands the
work inside each node to the existing `Coordinator`. The verdict is that ADK should WRAP the Coordinator
rather than replace it. The decisive finding is mechanical: a node body that blocks serialises the graph.
Two blocking nodes under a fan-out measured 0.63s wall at a concurrency of 1, against 0.37s and 2 once
offloaded with `asyncio.to_thread`. The workers are synchronous, so wiring `fan_out` directly into a node
would have produced a graph that appears parallel and is not. ADK's `max_concurrency` is graph-wide and so
cannot express the one thing that matters here - serialise the nodes that touch the non-thread-safe client,
overlap the rest - and authorisation ordering, the refusal/failure split, `client_access` and the port
boundaries are all better expressed where they already are. `google-adk` remains an optional dependency, not
declared in `pyproject.toml`, and the bridge tests skip when it is absent.
  so explicitly, so it is not read as clearance to run the real client concurrently.

**P6 decision: the workers join the flow, and the boundary holds where results land.**
`investigate()` accepts an optional `repository` plus the caller's intent (`verifier_targets`,
`patch_requests`) and records all three workers' contributions as one `session.workers` record. Two
sub-decisions are worth recording as decisions rather than as implementation detail.

First, verifier findings are EVIDENCE-SIDE but not `Evidence`. They describe the current system, unlike
a recalled case, so they belong on that side of the line; but they are a worker's read of a file, and an
unconfirmed observation must stay an observation. `evidence.py` and `verify.py` are therefore untouched
and the findings are reported beside the evidence set rather than inside it. The alternative - folding
them in - was rejected because it would let a worker's reading stand as a verified fact about the
system, which is the exact failure mode the memory boundary exists to prevent, reached by the other door.

Second, patch output is a PROPOSAL and stays inert. `patch_requests` must supply both a target and a
proposed body, because a worker that picks its own target is a worker deciding what to change, and
nothing in the flow writes, applies, commits or verifies a proposal. The Patch Generator's output is
retained as a diff for the engineer to review, not executed.

Consequences that are now enforced rather than merely intended: `authorize()` runs before every worker
dispatch, because the stage reaches the workers only through `Coordinator.fan_out`; the stage holds no
authoriser, lane or Coordinator of its own - it does not even import `Coordinator` - so it cannot become
a second path to the client; and `investigate()` rejects a `repository` carrying a different Coordinator,
because two Coordinators mean two lanes and the P5 hazard with them. Ranking is a total order over
`(-score, kind, ref, task_id, content)` so the report cannot depend on completion order. The score itself
counts the issue's own terms found in what the worker quoted, compared on a canonical form so that
equivalent spellings agree - `2 MB` with `2m`, `413` with `Request Entity Too Large`. That widens what
counts as a match and deliberately does not widen what a finding may become: a better relevance signal is
still a hint, and still neither evidence nor a verdict.

Explicitly deferred: no change to the EVIDENCE construction path, no threshold changes, no change to
`verify()` or to engineer interaction.

## Protected Artifacts

Unmodified by this ADR and by all P1+ work touching the demo story:

- `docs/phase1-demo-scenario.md`
- `docs/phase1-improvement-plan.md`
- `tests/test_integration_hindsight.py`

## Open Questions

1. Should sub-agents be allowed a fallback model, or Coordinator-only? Affects cost and the
   `fallback_used` disclosure the engineer currently sees in the PROPOSAL header.
2. Is the Patch Generator's diff an artifact to display, or does it need a review/apply boundary
   before it can be shown alongside hypotheses?
3. What is the acceptable cost ceiling for a single investigation once the Coordinator plus three
   workers are in the path?

---

## Amendment: the fourth worker (`sdc_analyzer`), VLSI-1C

Appended rather than merged. Everything above this line was written when the roster was three workers,
and the reasons it was three are still the reasons. This records the one addition, why it was made, and
what deliberately did not change.

### What was added

One roster entry, `sdc_analyzer`, with `allowed_tools=("read",)`, `model="primary"` and
`client_access=False`. `WORKER_ROSTER` now holds four.

### Why it qualified

The VLSI roadmap's justification test requires all four of: deterministic and reproducible; not
expressible as file inspection; needs domain structure rather than text excerpts; and an engineer would
act differently on the result. Three of the four were settled before any code was written. The second is
the one that needed arguing, so it is argued here:

*Reading a `.sdc` file and quoting lines is not a new worker's job* - `CodeLogVerifier` already does it,
under a `RepositoryScope`, with provenance. *Parsing that file into typed constraints and checking those
constraints against each other* is a different thing: the output is a judgement about internal
consistency that no amount of grepping produces, and it is the same judgement on every run.

### The part that is genuinely new: the analysis is injected, not imported

Every earlier worker hard-wires its capability. `SdcAnalyzerWorker` takes it as a callable:

    agents/sdc_analyzer_worker.py   generic: task shape, authorisation, refusals, the
                                    success/partial/failed decision, the Artifact mapping.
                                    Imports no domain module.
              |
              | injected in composition.py - the only place a domain is named
              v
    domains/vlsi/sdc_worker.py      VLSI: text in, typed findings out.

This keeps `agents/` domain-blind, which is the property the whole hub-and-spoke arrangement rests on
and which nothing else in the suite would notice breaking. VLSI-2's STA analysis then arrives as a
second callable rather than a fifth roster entry - the "small number of workers with wide capability"
the roadmap asks for, achieved rather than asserted.

### What deliberately did not change

1. **No third worker channel.** SDC findings land in `session.workers["verifier"]`, alongside file
   excerpts, because both are observations about the CURRENT system at the same authority and the
   engineer confirms them the same way. `session.workers` remains exactly `{verifier, patches}`.

   A third channel would have been worse twice: the generic layer becomes a catalogue of domains, and
   rendering it needs a per-domain branch in the view model - at which point the UI knows what an SDC
   finding is, which is the one thing the domain-neutral UI must not know.

2. **No new field on `AgentDefinition`.** `AGENT_DEFINITION_FIELDS` is still the ADR's four plus
   `client_access`, and is still closed.

3. **No change to authorisation, depth, or the `task` tool.** `sdc_analyzer` cannot delegate, cannot
   reach memory, and is refused a `memory` artifact in its context outright.

4. **One generic change to `_agent_of`.** A section used to report the FIRST reporting agent. With two
   worker kinds in one fan-out that misattributed provenance, so it now reports every reporting agent,
   sorted. Nothing consumed the old singular key.

### Deliberate decision: zero recognised constraints is NOT a refusal

A constraint file that parsed to nothing might be a valid file using constructs the parser does not yet
support, or it might not be a constraint file at all. Those are indistinguishable from the parse result
alone, and guessing would silently drop real findings while presenting as a missing feature rather than
as a defect.

So neither is refused. The run reports what it found, `partial` when the parser complained and `success`
when the file was genuinely empty, and the analyser leads with an explicit `analysis_unavailable`
observation stating that nothing was recognised. The uncertainty is preserved instead of resolved into a
file-intent claim the evidence does not support.

### Status mapping

| Condition | Result |
|---|---|
| complete, with findings | `success` |
| complete, no findings | `success`, no observations - a clean run is not a failure |
| parser reported an issue | `partial` - negative conclusions are provisional |
| target absent | `failed` / `unavailable` |
| the analyser was miswired or raised | `failed` - a finding about the tool run, not the design |

### Still true

Everything in "What no worker may do" above applies unchanged. An SDC finding is an observation, not an
`Evidence` item, not a verification, and not a verdict. `build_evidence`, `verify()` and engineer
interaction are untouched.
