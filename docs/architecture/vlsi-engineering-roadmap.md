# VLSI Engineering Roadmap

**Status:** PLANNED. Nothing in Parts II–VIII is implemented. The current system is a
domain-neutral engineering-debugging agent; this document is the path from there to an
evidence-grounded VLSI engineering agent.

**Canonical for:** the VLSI evolution of this architecture. Where this document and any other
file disagree about what is *planned*, this document wins. Where they disagree about what
*exists*, the code wins and the other file is wrong.

**Checkpoint this document describes:** `3b6fd33` on `architecture/hub-spoke-audit`
(1038 tests, 13 skipped).

---

## 0. How to read this document

Two labels are used throughout, and they are not decoration:

| Label | Meaning |
|---|---|
| **CURRENT** | Exists in the repository at the checkpoint above. Cited to a file. |
| **PLANNED** | Does not exist. A proposal for future work. No file implements it. |

Any sentence about a VLSI tool, a VLSI worker, a VLSI artifact type or a VLSI result is
**PLANNED** unless it is explicitly marked CURRENT. In particular: **no EDA tool is
integrated**, no VLSI worker exists, and no VLSI execution has been performed.

Arrows in the diagrams are ASCII `-->` on purpose. Earlier revisions of this repository's
documentation contain non-ASCII diagram characters that were corrupted by a text round-trip;
this document avoids the character class entirely.

---

# Part I — CURRENT: the foundation that already exists

## 1.1 The trust model

Four authorities, kept separate. This is not a slogan; each clause is enforced by a specific
mechanism, and the mechanism is named.

```
MEMORY     informs       knowledge of PAST cases
EVIDENCE   verifies       properties of the CURRENT system
AGENT      proposes       candidate explanations and candidate changes
ENGINEER   decides        the only authority that concludes
```

CURRENT enforcement points:

| Clause | Enforced by | Where |
|---|---|---|
| MEMORY informs, never verifies | recalled cases are never added to `Evidence`; the ranked memory context is a separate `MemoryContext` object | `src/debugagent/pipeline/recall_match.py`, `investigate.py` |
| EVIDENCE verifies | `build_evidence(case)` and `add_facts(evidence, facts)` are the only constructors, and they take only the normalized case and engineer-supplied facts | `src/debugagent/pipeline/evidence.py` |
| AGENT proposes | `generate_hypotheses` returns a `Proposal`; the agent never calls `verify()` and never sets a verification status | `src/debugagent/pipeline/hypothesize.py`, `verify.py` |
| ENGINEER decides | `verify()` raises if the engineer did not record a decision for every hypothesis | `src/debugagent/pipeline/verify.py` |
| Authorisation precedes dispatch | `Coordinator.delegate` calls `authorize(spec)` before resolving the worker; `fan_out` authorises in input order before any thread starts | `src/debugagent/agents/coordinator.py` |

The worker boundary is enforced by *where a result is allowed to go*, not by a warning
attached to it:

| Result | Stands for | May become | Must never |
|---|---|---|---|
| `session.memory` / `memory_delegation` | knowledge of past cases | context for the engineer | evidence, a decision, a verdict |
| `session.workers["verifier"]` | observations of the current system | an EVIDENCE-labelled report section | an `Evidence` item, a verification status, a verdict |
| `session.workers["patches"]` | a candidate change | a PROPOSAL section | an applied change, a verified fix |

CURRENT: verifier findings sit on the *evidence side* of the boundary and are deliberately
**not** `Evidence` items. `evidence.py` and `verify.py` are untouched by worker output; the
boundary is asserted by tests in `tests/test_p6_integration_flow.py` and
`tests/test_p7_production_audit.py`.

## 1.2 Proposal ≠ Evidence ≠ Knowledge ≠ Authorization

These four are routinely conflated, and each confusion has a specific failure mode. The
distinction is the single most important thing this architecture preserves.

| | What it is | Who produces it | Can it be wrong? | Failure mode if conflated |
|---|---|---|---|---|
| **Knowledge** | A record of a past case, with the environment it was observed in | the memory bank, via `retain()` | yes — it describes a *different* system state | treating a past fix as a current fact; applying it blindly |
| **Proposal** | A candidate explanation or change | the agent | yes — it is a hypothesis | a proposal is reported as a finding |
| **Evidence** | A property of the current system, with a source and a capture time | `build_evidence` / `add_facts`, from the case and the engineer | only if the engineer supplied it wrongly | an unverified claim is treated as verified |
| **Authorization** | Permission for a specific task to run | `authorize(spec)` against the closed roster | no — it is a decision, not a measurement | a task runs that was never permitted |

Two asymmetries are worth stating explicitly, because they are the ones the architecture is
built around:

- **Evidence cannot be produced by the agent.** There is no code path from a worker
  observation or an LLM statement into `Evidence`. The absence of that path is the boundary.
- **Authorization is not evidence that something is true.** `authorize()` answers "may this
  run?", never "is this correct?". A refusal is a permission decision; a failure is an outage;
  neither is a statement about the engineering problem.

## 1.3 Component inventory (CURRENT)

Every row is a file in the repository.

| Component | File | Role | Domain |
|---|---|---|---|
| **Coordinator** | `agents/coordinator.py` | Closed dispatch seam: `authorize` before every execution, `fan_out` behind a join barrier, positional results, refusal kept distinct from failure | **Generic** |
| **MemoryLane** | `agents/coordinator.py` (`class MemoryLane`) | The single serial lane every Hindsight access must pass through; the client caches one loop-bound `aiohttp` session and is not thread-safe | **Generic** |
| **Roster / registry** | `agents/registry.py` | Closed set of `AgentDefinition`s, `authorize()`, `build_task_spec()`, the `client_access` capability | **Generic** |
| **Task contracts** | `agents/tasks.py` | `TaskSpec`, `WorkerContext`, `Artifact`, `SubAgentResult`, the `SubAgentResult` status vocabulary | **Generic** |
| **Memory Specialist** | `agents/memory_specialist.py` | One serial recall task through the authorisation seam; cannot retain; given a `MemoryPort`, never an evidence builder | **Generic** |
| **Code/Log Verifier** | `agents/code_log_verifier.py` | Read-only inspection of files through a `SourcePort`; registered `client_access=False` | **Generic** |
| **Patch Generator** | `agents/patch_generator.py` | Produces a unified diff as a **proposal**; has no write path at all | **Generic** |
| **Repository adapters** | `agents/source_files.py` | `RepositoryScope` (the path boundary), `FileSourcePort` (read/glob/grep), `RepoPatchSourcePort` (read/diff — read-only by construction) | **Generic** |
| **Worker stage** | `pipeline/worker_stage.py` | Turns caller intent into authorised tasks, runs the fan-out, structures one `session.workers` record, ranks findings | **Generic** |
| **Relevance matching** | `pipeline/worker_stage.py` | Deterministic canonical-form matching (units, HTTP statuses, named errors) with a total ordering key `(-score, kind, ref, task_id, content)` | **Generic** |
| **Composition root** | `composition.py` | One port -> one Coordinator -> one lane; `bind()` refuses a second Coordinator over one client | **Generic** |
| **LLMRouter** | `llm/router.py`, `llm/tools.py` | Provider abstraction from environment, schema-validated structured output, tool loop with bounded turns/conversation/arguments/results | **Generic** |
| **Evidence / verification** | `pipeline/evidence.py`, `pipeline/verify.py` | The authoritative evidence and verification boundary | **Generic** |
| **Memory store** | `memory/hindsight_store.py` | Recall + state-idempotent retention via a local ledger; no retries, no reconciliation, no cross-process locking | **Generic** |
| **Investigate flow** | `pipeline/investigate.py` | The one session: normalise -> recall -> evidence -> worker stage -> hypotheses -> decisions -> resolution -> retention | **Generic** |
| **ADK bridge** | `adk_bridge.py` | Optional prototype wrapping the Coordinator in a `Workflow` graph. Verdict: **wrap, do not replace**. `google-adk` is not a declared dependency | **Generic** |

**No VLSI-specific code exists.** There is no SDC parser, no timing engine, no netlist reader,
no `.lib` reader, no PVT/corner model, and no EDA tool adapter.

## 1.4 Generic infrastructure vs VLSI-specific components

The split that makes the evolution possible: the CURRENT system is almost entirely generic, and
the VLSI work is additive.

**Generic infrastructure (exists; reused unchanged):** everything in 1.3. The Coordinator, the
lane, authorisation, task contracts, worker stage, relevance matching, composition root,
LLMRouter, memory store, evidence/verification boundary.

**Generic infrastructure (exists in pattern, will be instantiated per domain):** the `SourcePort`
/ `PatchSourcePort` protocol shape and `RepositoryScope` are how a domain supplies its inputs
without the core knowing the domain. A VLSI artifact source would be a *new implementation of
an existing shape*, not a change to the core.

**VLSI-specific (does not exist; PLANNED):**

| VLSI-specific component | Why it cannot be generic |
|---|---|
| SDC constraint model and parser | the constraint language and its semantics are domain vocabulary |
| Timing graph / path analysis | setup/hold, slack, required vs arrival, are domain definitions |
| Netlist and `.lib` (technology library) readers | cell timing arcs, libertables, NLDM/CCS lookup are domain-specific |
| PVT corner and analysis-condition model | corner naming, derates, OCV, AOCV/POCV are domain-specific |
| Clock-tree, congestion, DRC, LVS/STA signoff checks | each is a distinct deterministic algorithm family |
| Tool adapters (OpenSTA, PrimeTime-like, Tempus-like, RTA, synthesis, PnR) | tool-specific invocation, log formats, exit semantics |

The architectural consequence: **the VLSI domain plugs in at the port, not into the core.** A
VLSI worker is a worker; a VLSI tool adapter is a `SourcePort` implementation. Neither requires
the Coordinator, the lane, the roster's authorisation model, or the evidence boundary to change.

## 1.5 The current request lifecycle (CURRENT)

```
DebugInput
  -> normalize            -> NormalizedDebugCase
  -> memory delegation    -> memory_specialist (authorised, serial, inside the lane)
  -> recall + classify    -> MemoryContext                      [KNOWLEDGE]
  -> build_evidence       -> Evidence  (+ engineer facts)      [EVIDENCE]
  -> worker stage         -> workers.verifier, workers.patches [OBSERVATIONS / PROPOSALS]
       (authorize -> fan_out -> join barrier -> deterministic ranking)
  -> generate_hypotheses  -> Proposal                          [PROPOSAL]
  -> verify               -> VerificationResult                (engineer decision required)
  -> resolve + retain     -> state-idempotent retention        [KNOWLEDGE, next time]
```

Ordering note, because it is load-bearing: the worker stage runs *after* the evidence set is
built and *before* hypotheses are generated, which serves the **engineer** — by the time they
are asked to decide, the observations and any proposal have been shown to them. It does not
inform the model: `build_prompt` receives only the case and the memory context, so worker
output never enters the proposal prompt. Moving it there would be a trust-boundary change.

## 1.6 What the system deliberately does not do (CURRENT)

- It does not modify `evidence.py` or `verify.py`. Worker output never enters them.
- It does not apply, write, commit or verify a patch. `patch_requests` must supply both a target
  and a proposed body, because a worker picking its own target would be a worker deciding what
  to change.
- It does not retry a failed retention, reconcile an unknown remote outcome, or lock across
  processes. Retention is state-idempotent, not exactly-once.
- It does not infer a second lane, a second Coordinator, or a second route to the Hindsight
  client: `investigate()` refuses a `repository` runtime whose Coordinator is not the session's
  own.
- It does not run an LLM to rank findings. Relevance matching is table-driven string
  canonicalisation; no model, no network, no clock, no randomness.

## 1.7 Consistency with recorded project decisions

The project has already decided, in `docs/decision-log.md` (2026-09-27), to make the MVP
independent of live EDA tool integration, with "live EDA/observability tool integrations"
listed as out of scope in `docs/final-project-definition.md`.

This roadmap is consistent with that decision rather than a reversal of it:

- **VLSI-Foundation and VLSI-1 are designed to need no EDA tool.** The deterministic analysis
  in VLSI-1 is our own constraint analysis, not an invocation of PrimeTime.
- **EDA tool invocation enters later, as optional adapters**, and only if a milestone
  explicitly requires it.
- If VLSI-1 begins to require live tool invocation, that **supersedes the 2026-09-27 decision**
  and requires a new `decision-log.md` entry before implementation. That is a decision to make
  deliberately, not a detail to slide past.

---

# Part II — PLANNED: the target VLSI architecture

## 2.1 Target structure

```
                    VLSI Coordinator  (domain orchestration)
                              |
        +---------------------+----------------------+
        |                     |                      |
  Memory Specialist    VLSI deterministic     Code/Log investigation
                       analysis workers            |
        |                     |                      |
        |              +------+------+               |
        |              |             |               |
        |        deterministic   deterministic      |
        |          validation      validation        |
        |                     |                      |
        +---------------------+----------------------+
                              |
                     Patch Generator
                              |
                              |
                    Engineer decision
```

Read this as an extension of the CURRENT `Coordinator`, not a replacement. The boxes that
already exist — Coordinator, Memory Specialist, Code/Log Verifier, Patch Generator, worker
stage, deterministic validation, engineer decision — are the components in 1.3. The genuinely
new boxes are *VLSI deterministic analysis workers*, and the reason they are separate rather
than folded into the Code/Log Verifier is in 2.3.

## 2.2 What must not change (invariants)

The VLSI evolution is additive. These invariants are the reason it is safe to attempt:

1. `authorize()` runs before every worker execution, for every new worker, without exception.
2. One client -> one Coordinator -> one `MemoryLane`. VLSI workers that do not touch the client
   take no lane; a VLSI worker that does take the lane through the same mechanism.
3. `Evidence` is still constructed only from the normalized case and engineer-supplied facts.
   Tool output is **reported beside** the evidence set, never inside it.
4. Findings are observations; proposals are proposals; neither is a verdict.
5. A patch is never applied automatically. There is no path from a proposal to a write.
6. Refusal, failure and empty-success remain three distinguishable outcomes.
7. Result ordering stays deterministic and total.
8. An engineer's decision is required for every hypothesis.

A milestone that cannot preserve all eight does not merge.

## 2.3 Why dedicated VLSI workers, and when

**Specialised workers are introduced only where domain-specific deterministic analysis
justifies them.** Not every milestone gets a new agent, and this is a deliberate constraint
rather than an omission.

The justification test, all four required:

1. The analysis is **deterministic and reproducible** — same inputs, same outputs, no model in
   the loop.
2. It is **not expressible** as file inspection, so the existing Code/Log Verifier cannot do it.
3. Its result needs **domain structure** (typed numeric results, units, corners, path
   identities) rather than text excerpts, so it deserves a typed result rather than an
   `Artifact` carrying quoted text.
4. An engineer would **act differently** depending on its output.

By that test:

| Candidate | New worker? | Why |
|---|---|---|
| Reading a `.sdc` file and quoting the relevant lines | **No** | the Code/Log Verifier already reads files under a `RepositoryScope` |
| Diffing two constraint files | **No** | `RepoPatchSourcePort.diff` already produces a diff |
| Parsing SDC into typed constraints and checking them for contradictions | **Yes** | typed structure, deterministic rules, changes what the engineer does next |
| Computing setup slack from a timing report | **Yes** | numeric, unit-bearing, path-identified; not a text excerpt |
| Checking clock definitions for missing or unreachable relationship constraints | **Yes** | same reasons |

The expected outcome is **a small number of workers with wide capability**, not one worker per
milestone. VLSI-2 through VLSI-8 mostly extend analysis capability behind the same workers;
only a genuinely new analysis family earns a new roster entry, and every roster addition is a
deliberate act because the roster is closed.

## 2.4 The deterministic tool boundary

The core rule of the entire roadmap:

```
  LLM / Agent
      proposes
          |
          v
  VLSI deterministic tool          <- our own analysis, or an adapter to a licensed tool
      analyzes / verifies
          |
          v
  structured engineering evidence  <- typed, provenance-bearing, reproducible
          |
          v
  agent reasoning
```

**The agent must never treat its own statement as deterministic evidence.** Its own output is a
proposal, on the same side of the line as any other proposal. This is the VLSI-specific
expression of 1.2's asymmetry, and it is where an LLM-based engineering agent most easily
degrades into something that sounds authoritative and is not.

Practical consequences:

- A tool result is evidence *material*; it enters the report labelled with what produced it,
  under what conditions, at what time. It does not enter `Evidence` unconfirmed.
- Tool output that fails to parse, times out, or disagrees with another tool is a **finding
  about the tool run**, not about the design, and must be representable as such.
- Re-running the same analysis must reproduce the same result. A tool whose output varies
  between runs on identical inputs is not usable as evidence, and that must be detectable.

### Candidate tool backends — PLANNED, NOT INTEGRATED

None of the following is installed, invoked, licensed, or integrated. They are examples of what
an adapter *could* wrap once a milestone requires it, listed to show the boundary is real and
narrow rather than aspirational:

| Backend kind | Examples (not integrated) | Would be used for |
|---|---|---|
| Open-source STA | OpenSTA | STA / VLSI-2, VLSI-8 |
| Commercial STA | PrimeTime-like, Tempus-like timing engines | STA / VLSI-2, signoff / VLSI-8 |
| Fast timing estimation | Rta-like | early estimation, VLSI-3/VLSI-4 |
| Synthesis | synthesis tools | VLSI-3 |
| Floorplan / power | PnR tools with power estimation | VLSI-4 |
| Place, CTS, route, signoff | PnR and signoff tools | VLSI-5 … VLSI-8 |

Until a milestone says otherwise, the deterministic analysis is implemented in this
repository and runs locally and offline. That keeps the EDA-agnostic MVP decision intact and
keeps the test suite runnable without vendor licences.

---

# Part III — PLANNED: VLSI-Foundation

**Nothing in this part is implemented.** It is the set of things that must exist *before*
VLSI-1 implementation begins. If VLSI-1 starts without these, the first real constraint bug will
be spent on inventing them under pressure.

## 3.1 VLSI domain abstractions

The abstractions that let VLSI live beside the generic core rather than inside it:

- **A VLSI vocabulary** — the domain's identifiers (design, corner, scenario, tool, PDK,
  library set), carried in the same `environment` map a software case uses for service/version.
  `docs/domain-neutral-system-design.md` already commits to this shape: domain detail lives in
  `environment` values and tag vocabularies while the abstraction, lifecycle and interfaces stay
  identical across domains.
- **A domain artifact vocabulary** — the kinds of thing a VLSI session can be handed: RTL or
  netlist, `.lib`, `.sdc`, timing report, synthesis log, tool version and command line.
- **A VLSI case signature** — how an issue is normalised. It must be comparable across sessions
  so memory works; that is what makes the memory loop meaningful for this domain.
- **A domain root and boundary** — the equivalent of `RepositoryScope`: which directory, which
  cell, which corner set is in bounds for this session. The path-containment guarantees in 1.3
  apply unchanged.

## 3.2 Artifact types

PLANNED. The existing `Artifact(kind, ref, content)` is text-only, which is right for a file
excerpt and wrong for a typed timing number. VLSI needs an extension that can carry structure
without weakening the existing text path:

- **Text artifacts** — as today: an `.sdc` excerpt, a report line, a log excerpt. Continue to
  use `Artifact`.
- **Structured artifacts** — a parsed constraint, a path group with numeric slack, a corner
  summary. These need typed fields with units and provenance, not serialised-into-a-string
  fields, because a number hidden in prose cannot be validated or compared deterministically.

The `SubAgentResult` status vocabulary (`success` / partial / failed, with `failure_kind` and
`failure_detail`) extends unchanged; a structured artifact does not change what "failed" means.

## 3.3 Repository and project boundaries

PLANNED, reusing `RepositoryScope`:

- An authorised root per session, and containment resolved through symlinks **before** checking,
  exactly as `RepositoryScope.resolve` does today.
- Read budgets per artifact (the adapter already enforces byte budgets) extended to report-size
  budgets, because a real timing report is far larger than a source file.
- A **write-free** invariant for every VLSI input adapter: the design under analysis is read-only
  to this system. Today that is guaranteed structurally by `RepoPatchSourcePort` exposing only
  `read` and `diff`. The VLSI equivalent must be equally structural, not a convention.

## 3.4 Technology / library inputs — PLANNED

- `.lib` / `.liberty` parsing: cell timing arcs, units, operating conditions, and the unit
  convention that makes a bare number meaningless without its unit (a delay in ns versus ps).
- PDK and technology identity as environment values, so two cases from different nodes are
  visibly different to memory matching.
- The **unit discipline must be explicit in the data model.** This is the same class of problem
  the relevance matcher just solved for text (`2 MB` versus `2m`): a numeric result without a
  unit is not comparable, and comparing it anyway produces confidently wrong analysis.

## 3.5 Netlist inputs — PLANNED

- Netlist reading as a bounded, read-only artifact source.
- Instance/port identity handling, so a path in a timing report can be matched to a net in the
  netlist. This join is where a large fraction of real "why is this path failing" time goes.
- No netlist *writing*. The system analyses a design; it does not edit one.

## 3.6 SDC inputs — PLANNED

- Reading `.sdc` from the repository (already possible today via `FileSourcePort.read`).
- Parsing into typed constraints: command, arguments, scope, and the objects it applies to.
- Cross-file awareness: constraints may be split across several `.sdc` files included by others.
- Explicit handling of **unconstrained and partially constrained objects** — their absence is
  itself the most common class of setup-timing root cause, so "this object has no clock" must
  be representable as a finding rather than as silence.

## 3.7 Reports — PLANNED

- Report parsing as a bounded, read-only adapter, with per-report size budgets.
- Report/tool provenance recorded with every derived result: which report, which tool version,
  which run.
- Normalised units, and a **time-corner pairing recorded explicitly** so results from different
  runs are never silently compared.

## 3.8 Tool adapters — PLANNED

The adapter contract, not the tools:

- A tool adapter is a **read-only** boundary: it may run an analysis and return results, and it
  may never mutate the design, the reports, or the constraints.
- Adapters are **opt-in and absent by default**, so the whole system still runs offline with none
  of them installed. This is what preserves the EDA-agnostic decision by default.
- Every adapter declares a **determinism contract**: given identical inputs, it must produce
  identical outputs. An adapter that cannot make that promise is not eligible to produce
  evidence, and a check that runs an analysis twice and compares exists to enforce it.
- Every adapter result carries provenance (tool, version, command, environment, timestamp).
- Licensing and availability are treated as a first-class failure mode: a missing or unlicensed
  tool is a *refusal-like* condition ("cannot produce this evidence here"), never a silent
  fallback to the model's own reasoning. **The agent must not fill the gap by guessing what the
  tool would have said.**

## 3.9 Deterministic result representation — PLANNED

One result type, with these properties:

- **Typed and unit-bearing** — a number carries its unit; a quantity is never a bare float.
- **Provenance-bearing** — produced by (tool or analysis, version), from (inputs), at (time).
- **Comparable** — two results can be compared only when their unit, corner and tool context
  agree; disagreement is an explicit outcome.
- **Reproducible** — re-running the analysis yields the same result, and this is testable.
- **Diffable** — a proposal is a change to a result set, so it can be evaluated the same way the
  original was.

## 3.10 Provenance — PLANNED

Every VLSI observation carries: the source file or tool run, the tool and version, the analysis
conditions (corner, scenario), and the position in the source when applicable. This mirrors what
`Artifact.source` and repository-relative refs already do for file excerpts, and extends it to
numeric results.

Provenance is what makes a finding checkable rather than believable. An observation without it
is an assertion.

## 3.11 Failure representation — PLANNED

The existing vocabulary is kept and extended, not replaced:

| Condition | Representation |
|---|---|
| Authorisation refused | **refusal** — a rule said no (existing) |
| Tool absent / unlicensed | **unavailable** — evidence cannot be produced here (new `failure_kind`) |
| Input malformed or unparseable | **invalid input** — with the location (new `failure_kind`) |
| Analysis ran and found nothing | **success with zero findings** — never a failure (existing) |
| Analysis ran and reported an error | **failed**, with the tool's own message (existing) |
| Tool output varies between identical runs | **non-deterministic** — a hard failure of the evidence contract (new) |

The distinctions in the last three rows are the ones that get collapsed by accident, and each
collapse has a cost: reporting "nothing found" as a failure trains engineers to ignore the tool;
accepting non-deterministic output as evidence makes the report unreproducible.

## 3.12 Authorization boundaries — PLANNED

- VLSI workers register in the **same closed roster**, with `allowed_tools` and a
  `client_access` capability decided by the roster, not by the worker.
- A VLSI worker that only reads the design is `client_access=False` and takes no lane.
- A VLSI worker that needs the memory bank takes the lane through the existing mechanism.
- **Running an external tool is a capability that must be authorised, not implied.** The roster
  entry for a tool-using worker should make tool invocation explicit in its `allowed_tools`, so
  authorisation can distinguish "analyse this SDC file" from "invoke this licensed tool".
- No VLSI worker may retain. Retention stays the flow's responsibility after an engineer
  resolution.

---

# Part IV — PLANNED: VLSI-1, Evidence-Grounded SDC Debugging

VLSI-1 is the **first implementation target**. It is chosen deliberately over STA: setup failures
are frequently caused by constraints, constraints are human-authored text, and constraint
analysis is fully deterministic and testable offline. It therefore delivers the whole loop —
including the deterministic tool boundary — without requiring a licensed EDA tool.

## 4.1 Intended workflow

```
Problem
  |
  v
Memory recall                       [KNOWLEDGE]      past cases, never current facts
  |
  v
SDC inspection                      [OBSERVATION]    what the constraints actually say
  |
  v
Deterministic analysis              [EVIDENCE-SIDE]  typed, reproducible results
  |
  v
Engineering observations            [OBSERVATION]    reported with provenance, not conclusions
  |
  v
Hypotheses                          [PROPOSAL]       agent reasoning
  |
  v
Proposed constraint repair          [PROPOSAL]       agent reasoning, never applied
  |
  v
Deterministic re-analysis           [EVIDENCE-SIDE]  the proposal is measured, not trusted
  |
  v
Engineer decision                   [DECISION]       the only conclusion
  |
  v
Memory retention                    [KNOWLEDGE]      only after an engineer resolution
```

The step that distinguishes this from a chat assistant is **deterministic re-analysis**: a
proposed constraint change is evaluated by the same deterministic analysis that produced the
original observations, and the before/after is reported. The agent's belief that a change will
help is never the reason to accept it.

## 4.2 Initial SDC scope

The constraint families to cover, in **incremental order**. This is a proposed sequence, not a
commitment that all of them land in the first iteration:

| # | Family | Why it is early | What deterministic analysis can say |
|---|---|---|---|
| 1 | `create_clock` | every other constraint references clocks; without correct clock definitions nothing else is interpretable | a clock is defined, is defined once, has a valid waveform, and is reachable |
| 2 | `set_input_delay` / `set_output_delay` | the most common source of over-constrained or under-constrained paths | an object has input/output delay where it should, and none where it should not |
| 3 | `set_clock_uncertainty` | interacts with skew and jitter; a missing entry silently changes every path | uncertainty is defined for every clock pair that needs it |
| 4 | `set_false_path` | high risk: an over-broad false path hides real failures | a false path's scope matches its stated intent and does not cover more than intended |
| 5 | `set_multicycle_path` | a classic source of confusing setup results | the multiplier is consistent with the capture relationship |
| 6 | Clock relationships | generated clocks, master/slave relationships, latency | every clock domain has a defined relationship to its source |

**First iteration scope: items 1 and 2.** They are self-contained, heavily used, and testable
without a timing engine. Items 3–6 follow, each as its own increment with its own tests.

## 4.3 A realistic problem, end to end

**Reported problem:** "Why is this path failing setup timing?"

**Inputs the session is given:**

| Input | Kind | Notes |
|---|---|---|
| `design_top.v` / netlist | netlist | read-only |
| `ssg0p72v125c.lib` | technology library | read-only, carries units and operating conditions |
| `top.sdc` | constraints | read-only, contains a `create_clock` and per-port delays |
| `top_setup_ss.rpt` | timing report | worst-slack path listing, corner and tool version in the header |
| `sta_run.log` | tool log | the exact command line and tool version |

**How each step behaves (PLANNED):**

1. **Understand the violation.** The normalised case carries the reported slack, the path, the
   corner and the tool version. These become environment values, so memory can compare like
   with like.
2. **Memory recall.** The Memory Specialist returns prior cases from the same design family,
   corner and tool version. These are **knowledge of past investigations** and are shown as
   MEMORY — never added to evidence, never treated as a current fact. A recalled case may say
   "we fixed this by adding `set_clock_uncertainty` last time", which is a *hypothesis source*,
   not a result.
3. **Deterministic SDC/timing analysis.** The analysis reads `top.sdc`, parses it into typed
   constraints, and checks the path's endpoints and capture clock against them. It reports, for
   example, that the launch clock has a defined period but the capture clock's uncertainty is
   unspecified, or that the endpoint has no output delay. Each result is typed, unit-bearing and
   carries the tool/corner context.
4. **Worker produces observations with provenance.** Each finding states what was observed, in
   which file, under which conditions — e.g. "`app_clk` has `create_clock` at 500ps in
   `top.sdc:14`; no `set_clock_uncertainty` applies to `app_clk` in any file under the
   authorised root". **This is an observation, not a conclusion.** The system does not yet claim
   *why* the path fails.
5. **Agent forms hypotheses.** Using the observations plus recalled knowledge, the agent proposes
   candidate explanations: "the capture clock's missing uncertainty is being absorbed as
   pessimism", "the endpoint lacks an output delay so the path is under-constrained". These are
   **proposals**.
6. **Agent proposes a constraint change.** A candidate edit to `top.sdc`, expressed as a diff —
   proposed, never applied.
7. **Deterministic analysis evaluates the proposal.** The proposed change is analysed with the
   same deterministic analysis, and the before/after constraint state is reported as a
   comparison. The agent's expectation is not evidence; the re-analysis result is. If the tool is
   unavailable, the outcome is **unavailable**, and the session says so rather than falling back
   to the model's opinion.
8. **Engineer decides.** The engineer sees the observations, the hypotheses, the proposed change,
   and the deterministic before/after, and decides: accept, modify or reject. The decision is
   recorded. No change is applied by the system either way.
9. **Retention.** Only after an engineer resolution does the case become memory — with its
   corner, tool version, failed approaches and outcome, so a future similar case can recall it.

**Observations versus conclusions, stated as a rule the implementation must enforce:**

| Observation (allowed to be reported) | Conclusion (belongs to the engineer) |
|---|---|
| "no `set_clock_uncertainty` for `app_clk` in the authorised root" | "that is why the path fails" |
| "reported slack is -12ps at SS 0.72V" | "the design is timing-closed" |
| "re-analysis after the proposed change shows uncertainty defined" | "the change fixes the problem" |
| "two prior cases with this signature were resolved by clock definition" | "the same fix applies here" |

Every cell in the right column is a claim the system must not make on its own.

---

# Part V — PLANNED: milestones VLSI-2 to VLSI-8

Each milestone below is high-level by intent: the engineering problem, the evidence, the
boundary, the dependencies and the non-goals. Implementation detail is deliberately deferred
until the milestone starts, because designing VLSI-4 in advance of VLSI-2 tends to produce a
design that cannot be built on what VLSI-2 actually produced.

A note that applies to all of them: **most milestones extend analysis capability behind the
existing workers rather than adding a new roster entry.** New workers only where section 2.3's
four-part test is met.

## VLSI-2 — STA Debugging

- **Engineering problems:** a path fails setup or hold; slack is worse than expected; a path
  behaves differently between corners or modes.
- **Expected inputs:** netlist, `.lib`, `.sdc`, timing report, analysis conditions.
- **Deterministic evidence sources:** the timing report itself (a tool result, labelled as such),
  plus deterministic path and endpoint analysis.
- **Likely proposals:** constraint corrections, with a before/after slack comparison.
- **Validation requirements:** every proposal evaluated by re-analysis; before/after reported
  numerically with units and corner stated.
- **Safety / trust boundary:** a report is evidence material, not a verdict. A better slack
  number is not a sign-off, and the system does not certify timing closure.
- **Dependencies:** VLSI-1 (constraints), netlist handling, unit discipline.
- **Non-goals:** replacing the signoff tool; certifying timing across all corners; automatic
  constraint repair.

## VLSI-3 — Synthesis Debugging

- **Engineering problems:** unexpected QoR results; the design does not meet timing/area/power
  targets after synthesis; constraints were pruned or overridden.
- **Expected inputs:** RTL, constraints, synthesis log, QoR report.
- **Deterministic evidence sources:** synthesis log and QoR report; deterministic comparison of
  requested versus achieved constraints.
- **Likely proposals:** constraint or RTL-level changes, proposed as diffs.
- **Validation requirements:** re-synthesis is a heavy operation; the requirement is that its
  **absence is representable** ("cannot validate: re-synthesis not run"), never that a guess
  stands in for it.
- **Safety / trust boundary:** a QoR delta is an observation. "This will improve timing" is a
  hypothesis.
- **Dependencies:** VLSI-2; log parsing at scale; explicit cost/latency accounting for analysis.
- **Non-goals:** driving synthesis; automatic RTL rewriting; predicting QoR without running the
  tool.

## VLSI-4 — Floorplanning and Power Planning

- **Engineering problems:** congestion in a region; power above budget; area blow-up; placement
  difficulty caused by floorplan structure.
- **Expected inputs:** floorplan, placement data, power report, constraints.
- **Deterministic evidence sources:** power and congestion reports; deterministic spatial
  analysis (density, halo, blockage adjacency).
- **Likely proposals:** floorplan or constraint adjustments.
- **Validation requirements:** re-run dependent on tool availability; unavailable is an
  explicit outcome.
- **Safety / trust boundary:** power numbers are tool output under stated conditions; different
  activity assumptions give different answers, and the assumption set must travel with the
  number.
- **Dependencies:** VLSI-3; spatial reasoning in the adapter layer.
- **Non-goals:** autonomous floorplan optimisation; power sign-off.

## VLSI-5 — Placement

- **Engineering problems:** placement quality is poor; cells clustered; hold violations after
  placement; detailed-placement congestion.
- **Expected inputs:** placed netlist, timing/congestion reports, placement constraints.
- **Deterministic evidence sources:** placement and congestion reports; deterministic density
  and clustering analysis.
- **Likely proposals:** placement-constraint changes.
- **Validation requirements:** before/after via re-placement or re-analysis of the report.
- **Safety / trust boundary:** as above — measurement plus agent reasoning, never measurement
  replaced by reasoning.
- **Dependencies:** VLSI-4.
- **Non-goals:** developing placement algorithms; autonomous placement tuning loops.

## VLSI-6 — Clock Tree Synthesis

- **Engineering problems:** post-CTS timing shifted unexpectedly; skew or insertion delay
  changed a previously closing path; clock structures that CTS handles poorly.
- **Expected inputs:** pre-CTS and post-CTS reports, clock tree structure, CTS constraints.
- **Deterministic evidence sources:** clock structure analysis (skew, latency, balance) and
  before/after CTS timing comparison — the CTS stage is precisely where measurement-based
  reasoning pays off most.
- **Likely proposals:** CTS constraints, buffer or topology changes.
- **Validation requirements:** the comparison is mandatory and must be reported even when it is
  unfavourable to the proposal.
- **Safety / trust boundary:** a proposal that worsens a metric must be reportable as such, and
  the agent must not present an improvement it did not measure.
- **Dependencies:** VLSI-5; clock tree modelling.
- **Non-goals:** implementing CTS; automatic topology search.

## VLSI-7 — Routing

- **Engineering problems:** routing congestion and detours; DRC violations after routing;
  signal integrity problems (SI/crosstalk) on long nets.
- **Expected inputs:** routed design, DRC/SI reports, routing constraints.
- **Deterministic evidence sources:** DRC and SI reports; deterministic via-count and topology
  analysis.
- **Likely proposals:** routing-layer, via and constraint changes.
- **Validation requirements:** post-route re-analysis; violations listed explicitly.
- **Safety / trust boundary:** a DRC report is a measurement, and the system does not decide
  whether a violation is acceptable in the user's flow.
- **Dependencies:** VLSI-6; large-report handling.
- **Non-goals:** routing algorithms; autonomous ECO generation beyond proposals.

## VLSI-8 — Signoff

- **Engineering problems:** "is this design ready to tape out?"; convergence across all checks.
- **Expected inputs:** final netlist, all signoff reports, corner set.
- **Deterministic evidence sources:** signoff reports (timing, DRV, DRC, LVS, SI, power) and the
  deterministic cross-report consistency analysis.
- **Likely proposals:** none by default. This milestone is deliberately about **summarising and
  cross-checking** evidence, not about proposing changes. A signoff proposal would need an
  explicit justification and an engineer who asked for it.
- **Validation requirements:** a complete, corner-complete evidence set, or an explicit statement
  of what is missing. **Partial signoff evidence must never be presented as signoff.**
- **Safety / trust boundary:** the strongest in the roadmap. The system reports what the tools
  reported, under what conditions, and stops. It does not declare a design signoff-ready, and
  the transcript must say so in those words.
- **Dependencies:** VLSI-2 through VLSI-7, plus cross-tool provenance normalisation.
- **Non-goals:** automated signoff; substituting agent judgement for a missing signoff check.

## VLSI-Research-Evaluation — PLANNED

Planned **after** VLSI-1 delivers a working loop, and see Part VI.

## Production / Demo Hardening — PLANNED

Planned **after** the first milestone that produces a demonstrable investigation. Covers
deterministic replay, seeded corpora, reproducible demos, and the operational limits the current
audit identified as out of scope.

---

# Part VI — PLANNED: research evaluation

**No experimental results exist. This is a plan.** The current repository contains no evaluation
harness, no benchmark corpus and no measured comparison. Anything resembling a result would be
fabricated.

## 6.1 Purpose

The architecture makes a specific claim worth testing: that grounding a debugging agent in
memory and in deterministic evidence improves it, and that the improvement is attributable to
those components rather than to prompt wording.

That claim needs a controlled comparison, and the harness must not be able to flatter itself.

## 6.2 Comparison structure

| Arm | Configuration | Isolates |
|---|---|---|
| A | Baseline agent, no memory, no deterministic workers | the floor |
| B | A + memory enabled | the contribution of memory |
| C | B + deterministic evidence workers | the contribution of grounding |
| D | C + domain-specialist workers | the contribution of specialisation |

Arms differ **only** in the components under test. Same case corpus, same model, same prompts
apart from the disabled components, same decoding configuration. Any other difference confounds
the comparison and must be held fixed.

## 6.3 Evaluation dimensions

| Dimension | What it measures | Measurement sketch |
|---|---|---|
| Memory usefulness | does recall change the investigation for the better | did the path avoid approaches already recorded as failed? was a recalled root cause confirmed? |
| Evidence relevance | are the observations the useful ones | engineer or rubric judgement of which observations were decision-relevant; plus coverage of the known root cause |
| Hypothesis quality | are the proposed explanations correct | root-cause identification against a labelled answer |
| Proposal quality | are the proposed changes the ones an expert would make | expert comparison against the accepted historical fix |
| Deterministic validation success | does re-analysis confirm or refute the proposal | counted, and **refutations are recorded, not discarded** — a high rate here is a finding about the agent, not noise to hide |
| Tool/evidence confusion | does the agent ever present its own statement as evidence | count of claims lacking provenance; should be zero, and any non-zero value is a boundary failure |
| Failure handling | does it stay correct when the tool is absent, the input malformed, or a path outside the root | behaviour under injected failures |
| Provenance preservation | is every claim traceable to a source | fraction of observations carrying complete provenance |
| Engineer decision quality | does a human decide better with it | engineer judgement, or time-to-decision |

The zero-tolerance dimension is **tool/evidence confusion**. It is listed separately because it is
a boundary property rather than a quality metric: any non-zero count means the architecture's
central guarantee failed, and it should be treated as a defect rather than averaged into a score.

## 6.4 Threats to validity — design them in now

An evaluation that cannot fail is not an evaluation:

- **Leakage:** the corpus must be disjoint from any case used to seed memory for the same query.
- **Model variance:** repeated trials per case, with variance reported. A single run per case
  cannot distinguish an effect from noise.
- **Confounded prompts:** changing the prompt when changing the component under test invalidates
  the arm.
- **Selective reporting:** failed and inconclusive cases are reported, not dropped. An evaluation
  that reports only successes measures curation, not capability.
- **Grader agreement:** if expert judgement is used, agreement between graders should be reported
  alongside the result.
- **Survivorship:** cases the agent could not even attempt must appear in the denominator.

---

# Part VII — Completion criteria

**"The agent produced an answer" is not evidence of completion.** A milestone is complete only
when every criterion below is met and demonstrable. Each criterion is stated so that its absence
is detectable.

| # | Criterion | How it is demonstrated |
|---|---|---|
| 1 | **Implementation complete** | the milestone's capability works end to end through `investigate()`, not only in a unit test |
| 2 | **Focused tests** | tests for the new capability itself, including the boundaries it claims |
| 3 | **Regression tests** | existing suite unchanged and green; no test weakened or deleted to make a milestone pass |
| 4 | **Deterministic behaviour** | identical inputs produce identical outputs across repeated runs, target order, and a fresh interpreter |
| 5 | **Failure tests** | absent tool, malformed input, unreadable file, path outside the root — each produces its own distinct outcome, and none produces a plausible-looking wrong answer |
| 6 | **Provenance tests** | every observation asserted to carry complete provenance |
| 7 | **Trust-boundary tests** | observations are not evidence; proposals are not applied; findings are not verdicts; refusal is not failure; findings do not enter the proposal prompt |
| 8 | **Authorisation tests** | an unauthorised task never reaches its worker; authorisation precedes every execution, proven by ordering rather than by inspection |
| 9 | **Full test suite green** | the whole suite passes, including the ~1038 tests that exist today |
| 10 | **Mutation checks** | the milestone's failure modes are shown to be caught; a mutation that survives is documented as a test gap and fixed |
| 11 | **Documentation** | this roadmap, the ADR and the README reflect what actually exists, with limitations stated |
| 12 | **Reproducible demo** | where applicable, a deterministic re-runnable demonstration — the same evidence the current audit required of worker output |
| 13 | **Honest limitations recorded** | what the milestone does not do, stated rather than left to be discovered |

Additionally, for any milestone that introduces a new worker:

| # | Criterion | How it is demonstrated |
|---|---|---|
| 14 | **Justification recorded** | section 2.3's four-part test satisfied in writing |
| 15 | **Closed roster respected** | the agent is registered with declared `allowed_tools` and a roster-declared `client_access` |

---

# Part VIII — Non-goals

This roadmap explicitly does **not** intend to:

1. **Let the LLM directly modify production designs.** No write path from a model to a design.
   The Patch Generator proposes a diff; nothing applies it.
2. **Treat LLM output as proof.** An agent's statement is a proposal. Deterministic evidence comes
   from a deterministic analysis, with provenance, and is reported as observation until the
   engineer confirms it.
3. **Allow unrestricted tool access.** Every worker, and every tool invocation, is authorised
   through the same closed roster. Capability is declared, not inferred.
4. **Bypass authorization.** `authorize()` runs before every execution. No "trusted" fast path, no
   internal caller exemption.
5. **Automatically apply patches without engineer approval.** Ever. This is invariant (2.5) and
   non-goal (5) — the same rule stated twice because it is the one most likely to be eroded by a
   well-meaning convenience.
6. **Claim EDA-tool capabilities that are not actually integrated.** No tool is installed, invoked
   or licensed today. If a tool is unavailable, the outcome is *unavailable*, and the system does
   not substitute the model's opinion for the tool's answer.
7. **Build every VLSI domain simultaneously.** One domain — constraints first — until it works.
8. **Replace deterministic EDA tools with an LLM.** The tools and analyses are the evidence. The
   agent's job is to decide what to ask, interpret the answer, and propose a change for a human to
   judge — not to be the judge.

Two further constraints, stated so they are not mistaken for oversights:

9. **No silent degradation.** If deterministic evidence is unavailable for any reason, the session
   says so in the transcript. A weaker session that sounds confident is worse than one that
   reports its own gaps.
10. **The generic core is not modified to accommodate a domain.** If a VLSI requirement appears
    to need a change to the Coordinator, the lane, authorisation or the evidence boundary, that
    is a signal to re-examine the requirement — not to edit the boundary. The VLSI work plugs in
    at the port.

---

# Appendix A — Evidence index

Where each CURRENT claim in this document is grounded, so a reader can check rather than trust:

| Claim | File |
|---|---|
| four-authority trust model and its enforcement points | `docs/adr-001-hub-spoke-coordinator.md`, `README.md` |
| closed roster, `client_access` capability, `authorize()` | `src/debugagent/agents/registry.py` |
| serial memory lane, fan-out, refusal semantics, positional results | `src/debugagent/agents/coordinator.py` |
| one client -> one Coordinator -> one lane | `src/debugagent/composition.py` |
| read-only repository adapters, path containment | `src/debugagent/agents/source_files.py` |
| worker stage, canonical relevance matching, total ordering | `src/debugagent/pipeline/worker_stage.py` |
| evidence and verification boundary | `src/debugagent/pipeline/evidence.py`, `verify.py` |
| state-idempotent retention, no retries/reconciliation | `src/debugagent/memory/hindsight_store.py`, `docs/adr-001…` |
| LLM provider abstraction, tool loop, resource limits | `src/debugagent/llm/router.py`, `llm/tools.py` |
| ADK verdict: wrap, do not replace; optional dependency | `src/debugagent/adk_bridge.py`, `docs/adr-001…` |
| current behaviour, verified boundaries, known limitations | `docs/audit-001-production-readiness.md` |
| MVP is independent of live EDA tool integration | `docs/decision-log.md` (2026-09-27), `docs/final-project-definition.md` |
| VLSI case fields, environment vocabulary | `docs/project-ideas.md`, `docs/domain-neutral-system-design.md` |
| planned VLSI worker directions (pre-existing, brief) | `README.md` ("Planned VLSI engineering direction") |

# Appendix B — Document status

| Property | Value |
|---|---|
| Nature of this milestone | documentation only |
| Code changed | none |
| VLSI capability implemented | none |
| EDA tools integrated | none |
| New dependencies | none |
| Protected files touched | none |
| Claim on current behaviour | verified against `3b6fd33`, 1038 tests passing |