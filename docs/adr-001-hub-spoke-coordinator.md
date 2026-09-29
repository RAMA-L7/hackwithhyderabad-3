# ADR 001: Hub-and-Spoke Coordinator Pattern & Tool-Calling Prerequisites

## Status

Accepted (Design Phase — P0)

Documentation only. No code in `src/`, `tests/`, or configuration is changed by this ADR. It records a
decision and the prerequisites any implementation must satisfy; it does not authorise implementation
beyond P0.

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
- 254 tests across 17 files encode current behaviour, including trust-boundary assertions.
- `Session.to_dict()` is the `inspect` output format read by the demo.

## Migration Phases

Each phase is independently shippable and revertible; each has an explicit gate.

| Phase | Scope | Gate | Status |
|---|---|---|---|
| **P0** | This ADR. Documentation only. | review | done |
| **P1** | Delegation **seam** only: `TaskSpec`, `WorkerContext`, `SubAgentResult`, closed worker registry, tool authorization, anti-recursion enforcement, context-isolation structures. No LLM changes, no tool calling, no worker execution. | unit tests for the anti-recursion rule | done — 37 tests |
| **P2** | Tool calling: `tools`/`tool_choice` in `LLMRouter`, assistant `tool_calls` turn, `role: "tool"` results, bounded turn loop, `task` execution. Default path byte-identical. | all prior tests green; new tool-loop tests | not started |
| **P3** | Concurrency safety for `HindsightMemoryStore` (lock + single writer). | concurrent recall/retain test | not started |
| **P4** | Memory Specialist only, single task, serial. | Acts 1–4 rehearsal on fresh banks, output unchanged | not started |
| **P5** | Parallel fan-out with join barrier and partial failure. | latency and failure-injection tests | not started |
| **P6** | Code/Log Verifier, then Patch Generator. | trust-rule regression suite green | not started |

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
