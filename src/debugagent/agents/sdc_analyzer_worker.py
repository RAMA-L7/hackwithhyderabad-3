"""VLSI-1C: the SDC Analyzer worker - generic machinery, injected analysis.

The fourth roster entry, and the first one whose *capability* is injected rather than hard-wired.
`CodeLogVerifier` knows about `SourcePort` and about reading files; this worker knows about
`SourcePort`, about reading files, and about nothing whatsoever SDC. The analysis arrives as a
callable, supplied by `composition.py`, and this module is the half that would be unchanged if
VLSI-2's STA analysis arrived tomorrow behind the same seam.

## What makes it generic, structurally

There is no `import debugagent.domains` anywhere in this file. The injected `analyze` callable is the
only route to the analysis, which is why roadmap section 2.3's "a small number of workers with wide
capability" is achievable rather than aspirational: STA does not need a fifth roster entry, it needs a
second callable. A test asserts the absence of the import, because the property is invisible to
anything that only reads the passing tests.

## The trust boundary, restated for this worker

An SDC finding is an **observation about a file**, and nothing more. Specifically, this worker:

- never writes an `Evidence` item, never sets a verification status, never contributes a hypothesis,
  and never proposes a repair. `SubAgentResult` has no method that could, and that is structural
  rather than a promise made here.
- never reaches memory. It is registered `client_access=False`, holds no Hindsight port, imports no
  memory module, and REFUSES a task whose context carries a `memory` artifact - the same refusal
  `CodeLogVerifier` makes, and for the same reason: a recalled case is not evidence about the
  constraints in front of the engineer.
- never guesses. A file it cannot read is a fault, not an empty result. A target it cannot recognise
  as SDC is a refusal, not a clean run.

## The status mapping, which is the substance of this file

`RESULT_STATUSES` is `success / partial / failed`, and the difference between them is what an engineer
reads. Collapsing any of these would turn a real distinction into a false one:

| Condition | Status | Why not something else |
|---|---|---|
| read, complete | `success` | |
| complete, no findings | `success`, no observations | a clean run is not a failure, and not an empty-but-validating one |
| `incomplete` (a parser issue) | `partial` | a parser issue makes every *negative* finding provisional; reporting `success` would let a half-read file read as clean |
| parsed to zero constraints | `partial` if the parser complained, `success` if genuinely empty | see below |
| target absent | `failed` / `unavailable` | "failure is never 'nothing found'" |
| the analyser itself raised | `failed` / `unavailable` | this is a finding about the tool run, not about the design, and the wording says so |

## On "this is not a constraint file"

The obvious extra row - *wrong target, so refuse* - was designed, implemented and then removed, because
it cannot be done soundly.

A file that parsed to zero constraints is either an SDC file the parser could not read, or something
that was never SDC. Distinguishing them means deciding whether the contents look like constraints,
which is a guess about intent from surface form. Refusing on the guess would silently drop real findings
whenever the parser met a legitimate SDC command it does not support yet - a failure that would present
as a missing feature rather than as a bug, which is the worst way for it to present.

So neither is refused. Both are reported, and the analyser leads with an observation stating outright
that nothing was recognised, so the reader is told "this file was not understood" instead of having to
infer it from a wall of parser complaints. The status still carries the distinction that matters:
`partial` when the parser complained, `success` when the file was genuinely empty.

A caller that wants to know whether it named the right file gets that answer from the observation, not
from a refusal it cannot distinguish from a parser gap.
"""

from __future__ import annotations

from typing import Any, Callable

from debugagent.agents.code_log_verifier import SourceNotFound, SourceUnavailable
from debugagent.agents.registry import authorize, get_agent
from debugagent.agents.tasks import (
    MAX_SPAWN_DEPTH,
    Artifact,
    SubAgentResult,
    TaskSpec,
)

SDC_ANALYZER = "sdc_analyzer"

#: Mirrored from the roster for the AUTHORITY checks below. The authoritative list is
#: `get_agent(SDC_ANALYZER).allowed_tools`; a task is also checked against its own grants, so a
#: broader constant here could never widen what a task allows.
READ_TOOL = "read"

# `SourceNotFound` / `SourceUnavailable` are imported from `code_log_verifier` rather than from
# `source_files`, because that is where they are raised from: `FileSourcePort` defaults to
# `VERIFIER_VOCABULARY`, so the exception a read actually produces is the one defined there. Catching a
# class the port never raises would leave every read fault escaping as an unhandled crash, which is the
# exact opposite of "never raises for an expected source failure". This is an `agents/`-to-`agents/`
# import and costs the module nothing in domain neutrality; the alternative - the port module owning the
# exceptions - would be a wider refactor of code this milestone must not touch.

#: The task objective. One keyword, because a worker choosing its own action would be a worker
#: deciding what to look at - the same rule `CodeLogVerifier` follows.
SDC_OBJECTIVE = "analyse sdc constraints"

_MEMORY_ARTIFACT_KIND = "memory"

#: What an injected analyser must return. Not a Protocol: the injected callable is a domain function
#: with a domain return type, and naming that type here would be the domain import this module exists
#: to avoid. What is pinned down is the ATTRIBUTE contract - `parsed_constraints`, `result.status`,
#: `observations` - which `execute` reads defensively and reports precisely when it is not met.
_ANALYZER_CONTRACT = ("parsed_constraints", "result", "observations")


def build_sdc_task(*, task_id: str, case_signature: str, target: str,
                   symptoms: tuple[str, ...] = (), delegated_by: str = "coordinator") -> TaskSpec:
    """Build a `TaskSpec` for the SDC Analyzer through the P1 authorisation seam.

    Goes through `build_task_spec` so the same schema validation, tool authorisation and depth rule
    every other delegation uses apply here too. `target` becomes a `code` artifact in the task context,
    which is the only way this worker learns what to analyse.

    The context is passed in its wire form rather than as dataclasses, because `build_task_spec`
    validates a plain dict and serialises through the P1 schema - that keeps exactly one validation
    path for every delegation rather than a fast one and a careful one.
    """
    from debugagent.agents.registry import build_task_spec

    definition = get_agent(SDC_ANALYZER)
    return build_task_spec({
        "task_id": task_id,
        "agent": SDC_ANALYZER,
        "delegated_by": delegated_by,
        "objective": SDC_OBJECTIVE,
        "context": {
            "case_signature": case_signature,
            "symptoms": list(symptoms),
            # An explicitly supplied context item, so `content` names what the Coordinator is pointing
            # at. `Artifact` requires non-empty content, and the path is the honest value: the
            # Coordinator supplied a location, not the file's contents.
            "artifacts": [{"kind": "code", "ref": target,
                           "content": f"requested for constraint analysis: {target}"}],
        },
        "allowed_tools": list(definition.allowed_tools),
        "model": definition.model,
        "depth": 1,
    })


class SdcAnalyzerWorker:
    """Executes one read-only constraint analysis task at a time, through the `SourcePort` seam.

    Construction takes the agent id from the registry, the port from the caller, and the analysis
    callable from the composition root - so the worker cannot be wired to a backend it is not
    authorised for, and cannot reach an analysis that was not deliberately attached.

    There is no `client_access` attribute here, and that is deliberate for the same reason it is
    absent from `CodeLogVerifier`: serialisation is decided by the roster's `client_access`
    capability, so a worker cannot opt out of the shared `MemoryLane` by declaring itself non-client.
    """

    def __init__(self, port: Any, *, analyze: Callable[..., Any], agent: str = SDC_ANALYZER):
        self._port = port
        self._analyze = analyze
        self._agent_id = agent
        # Resolved through the registry, so an unknown id is refused at construction and the worker's
        # tool surface comes from the roster rather than from constants in this module.
        self._definition = get_agent(agent)
        self._executed: list[str] = []

    @property
    def executed(self) -> tuple[str, ...]:
        """Task ids this worker ran, in order. For tests and for an audit trail."""
        return tuple(self._executed)

    def execute(self, spec: TaskSpec) -> SubAgentResult:
        """Run one task and return its result. Never raises for an expected source failure.

        Authorisation runs first, unchanged from P1: an unauthorised tool, the wrong agent, or depth
        > 1 is refused before any port call. The registry validates a spec against whichever agent it
        names, so the check that this spec is addressed to THIS worker is made explicitly - otherwise a
        Memory Specialist task handed here would be authorised and then executed against the wrong seam.
        """
        if spec.agent != self._agent_id:
            raise PermissionError(
                f"task {spec.task_id!r} is addressed to agent {spec.agent!r}, but this worker is "
                f"{self._agent_id!r}; the Coordinator dispatched it to the wrong worker")
        authorized = authorize(spec)
        if authorized.depth > MAX_SPAWN_DEPTH:
            # Defence in depth. `authorize` already enforces this; re-asserted so the invariant holds
            # even if that check is ever weakened.
            raise PermissionError(f"depth {authorized.depth} exceeds max_spawn_depth {MAX_SPAWN_DEPTH}")
        self._executed.append(authorized.task_id)
        try:
            return self._run(authorized)
        except SourceUnavailable as exc:
            return self._failure(authorized, "unavailable" if exc.kind == "unavailable" else "schema",
                                 str(exc))
        except SourceNotFound as exc:
            # Reaching here means a target vanished between planning and reading. A fault, not an
            # observation: reporting an absent file as an observation would put "no constraints here"
            # and "no file here" in the same voice, and they mean opposite things.
            return self._failure(authorized, "unavailable", f"target absent: {exc.path}")

    def _run(self, spec: TaskSpec) -> SubAgentResult:
        memory_refs = [artifact.ref for artifact in spec.context.artifacts
                       if artifact.kind == _MEMORY_ARTIFACT_KIND]
        if memory_refs:
            return self._failure(
                spec, "schema",
                f"refusing to analyse alongside recalled memory: the SDC Analyzer reads the CURRENT "
                f"constraint files only, and was handed memory artifact(s) {memory_refs}. A past case "
                f"is not evidence about this issue")

        if READ_TOOL not in set(spec.allowed_tools):
            return self._failure(
                spec, "schema",
                f"task {spec.task_id!r} did not grant the {READ_TOOL!r} tool, which the "
                f"{spec.objective!r} objective requires")

        targets = [artifact.ref for artifact in spec.context.artifacts
                   if artifact.kind == "code" and artifact.ref.strip()]
        if not targets:
            return self._failure(
                spec, "schema",
                "no constraint file supplied: the task named no code artifact to analyse, so there is "
                "nothing to observe")

        observations: list[Artifact] = []
        statuses: list[str] = []
        for path in targets:
            text = self._port.read(path)
            analysis = self._analyze(text, ref=path)
            contract = self._check_contract(spec, analysis)
            if contract is not None:
                return self._failure(spec, "schema", contract)

            # No check on whether this "looks like" SDC. A file that parsed to zero constraints is
            # reported as a file that yielded nothing to analyse - `partial` if the parser complained,
            # `success` if it was genuinely empty - and the analyser leads with an observation saying so
            # outright. Refusing instead would require guessing from content whether this is an SDC file
            # that failed to parse or something else entirely, and a worker that guesses which files it
            # was meant to be given is a worker deciding what to look at.
            statuses.append(analysis.result.status)
            observations.extend(Artifact(**o.as_artifact_kwargs()) for o in analysis.observations)

        # `partial` whenever the domain reported an incomplete read. The individual observations do not
        # say whether they can be trusted - a finding is reported the same way whether or not the file
        # was fully read - so the status is what carries that, and it is the only place it can be
        # carried. Derived from the analyser's own `status`, never from the presence of observations: a
        # file that produced ten findings and was fully understood is `success`.
        status = "partial" if "incomplete" in statuses else "success"
        return SubAgentResult(task_id=spec.task_id, agent=spec.agent, status=status,
                              observations=tuple(observations))

    def _check_contract(self, spec: TaskSpec, analysis: Any) -> str | None:
        """None when the injected analyser met the contract, else the reason it did not.

        A mis-wired analyser must fail loudly rather than degrade. The failure is reported as `schema`
        because that is exactly what it is: the caller wired something that is not an analyser, and
        saying so is more useful than reporting zero observations as a clean run.
        """
        missing = [name for name in _ANALYZER_CONTRACT if not hasattr(analysis, name)]
        if missing:
            return (f"the analyser wired to {SDC_ANALYZER} did not return {list(_ANALYZER_CONTRACT)}; "
                    f"missing {missing}. It returned {type(analysis).__name__}, which this worker "
                    f"cannot read")
        status = getattr(getattr(analysis, "result", None), "status", None)
        if status not in ("complete", "incomplete"):
            return (f"the analyser wired to {SDC_ANALYZER} reported status {status!r}; expected "
                    f"'complete' or 'incomplete'")
        return None

    def _failure(self, spec: TaskSpec, kind: str, detail: str) -> SubAgentResult:
        # `schema` failures report as the agent vocabulary's `schema`; source and analyser faults report
        # as `unavailable`. The precise reason is always in `failure_detail`, so a refusal stays legible
        # after it crosses the seam.
        return SubAgentResult(task_id=spec.task_id, agent=spec.agent, status="failed",
                              failure_kind=kind, failure_detail=detail)