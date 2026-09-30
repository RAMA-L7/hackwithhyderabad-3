"""P4: the Memory Specialist worker.

The first real worker (ADR 001, migration phase P4: "Memory Specialist only, single task, serial").
It executes exactly one authorised task through the existing `MemoryPort` seam and returns a
`SubAgentResult` to the Coordinator.

Three properties this module exists to guarantee, all of them structural rather than instructional:

1. **Memory informs; it never verifies.** The worker's tool surface is `hindsight_recall` and
   `hindsight_get_facts` only. It is constructed with a `MemoryPort`, never an `Evidence` builder, so
   recalled content has no path into evidence construction at all.

2. **Failure is never "nothing found".** A `MemoryFailure` becomes `status="failed"` with a
   `failure_kind`, never an empty observation list. An abstention or a clean "no relevant case" is a
   SUCCESS with an explanatory observation; a broken backend is a FAILURE. Collapsing the two would
   tell the engineer their memory is empty when it is unreachable.

3. **Nothing here authorizes anything.** The result is a proposal input. `SubAgentResult` carries
   observations with `memory:` provenance, which the Coordinator may rank but which cannot satisfy a
   verification requirement.

P4 scope: ONE task, SERIAL. There is no scheduler, no pool, no fan-out and no concurrency anywhere in
this module. Parallel execution is P5 and is deliberately absent.
"""

from __future__ import annotations

from typing import Any, Protocol

from debugagent.agents.registry import authorize, get_agent
from debugagent.agents.tasks import (
    MAX_SPAWN_DEPTH,
    Artifact,
    SubAgentResult,
    TaskSpec,
)


class MemoryFailure(RuntimeError):
    """A memory operation failed, raised by a port that is not the pipeline's own port.

    The worker catches failures STRUCTURALLY, by `kind` attribute (see `_failure_kind_of`), so this
    class is a convenience for fakes rather than the contract. The real
    `pipeline.memory_port.MemoryFailure` carries the same `kind` attribute and is caught by the same
    path; importing it here would violate the P1 rule that the agents package must not import the
    Phase 1 pipeline.
    """

    def __init__(self, kind: str, message: str):
        super().__init__(f"memory {kind}: {message}")
        self.kind = kind


#: The memory failure vocabulary a port may report, mirroring `pipeline.memory_port.FAILURE_KINDS`.
MEMORY_FAILURE_KINDS = ("unavailable", "ambiguous", "persist", "auth", "schema")


def _failure_kind_of(exc: BaseException) -> str | None:
    """The memory failure kind for `exc`, or None if it is not a memory failure.

    Duck-typed on a `kind` attribute instead of an isinstance check, because the exception the
    pipeline raises is deliberately not importable from this package. An exception without a
    recognisable `kind` is NOT treated as a memory failure and is left to propagate: a programming
    error must not be reported to the engineer as a memory outage.
    """
    kind = getattr(exc, "kind", None)
    if isinstance(kind, str) and kind in MEMORY_FAILURE_KINDS:
        return kind
    return None


class MemoryPort(Protocol):
    """The memory seam this worker needs. Mirrors `pipeline.memory_port.MemoryPort`."""

    def recall_and_classify(self, query: str) -> dict:
        """Return a MemoryView, or raise MemoryFailure."""

    def retain(self, case: dict) -> dict:
        """Store one case. Declared so the seam is complete; this worker never calls it."""

# The registry agent id this worker implements. `authorize()` refuses a TaskSpec naming any other
# agent, so the seam and the registry cannot drift apart.
MEMORY_SPECIALIST = "memory_specialist"

# How much of one recalled case is carried to the Coordinator. This is a DISPLAY bound, not a
# truncation of engineering truth: the observation is explicitly labelled as memory and marked
# partial, so the Coordinator can never mistake a clipped case for a whole one. P3-B's rule is that
# tool RESULTS are refused rather than clipped; here the value is not a tool result, it is memory
# offered as context, and a bounded excerpt is what "informs, does not verify" requires.
MAX_CASE_EXCERPT_CHARS = 400

# `MemoryFailure.kind` -> `SubAgentResult.failure_kind`. The agent vocabulary is
# ("unavailable", "timeout", "auth", "invalid_output", "schema"), so P3-2C's two new kinds are mapped
# onto the closest existing member rather than widening the agent contract. The mapping is lossy on
# purpose and is recorded in `failure_detail`, which keeps the precise kind visible to the engineer.
_FAILURE_KIND_MAP = {
    "unavailable": "unavailable",
    "ambiguous": "unavailable",  # remote outcome unknown; see failure_detail for the distinction
    "persist": "unavailable",     # local persistence gap; see failure_detail
    "auth": "auth",
    "schema": "schema",
}


class MemorySpecialist:
    """Executes one memory retrieval or retention task, serially, through `MemoryPort`.

        Construction takes the agent id from the registry and the port from the pipeline, so the worker
        cannot be wired to a backend it is not authorised for, and cannot reach anything except memory.

        Note there is no `client_access` attribute here, and that is deliberate. P5 serialises client
        access from the registered `AgentDefinition`'s `client_access` capability, not from the worker,
        so a worker cannot opt out of the shared `MemoryLane` by declaring itself non-client.
        """


    def __init__(self, port: MemoryPort, *, agent: str = MEMORY_SPECIALIST):
        self._port = port
        self._agent_id = agent
        # Resolved through the registry, so an unknown id is refused at construction and the worker's
        # tool surface comes from the registry rather than from a constant in this module.
        self._definition = get_agent(agent)
        self._executed: list[str] = []

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def allowed_tools(self) -> tuple[str, ...]:
        return self._definition.allowed_tools

    @property
    def model(self) -> str:
        return self._definition.model

    def execute(self, spec: TaskSpec) -> SubAgentResult:
        """Run one task and return its result. Never raises for an expected memory failure.

        Authorisation runs first, unchanged from P1: an unauthorised tool, the wrong agent, or
        depth > 1 is refused before any port call. The registry validates a spec against whichever
        agent it names, so the check that this spec is addressed to THIS worker is made explicitly
        below - otherwise a Patch Generator task handed to the Memory Specialist would be authorised
        and then executed against the memory port.
        """
        if spec.agent != self._agent_id:
            raise PermissionError(
                f"task {spec.task_id!r} is addressed to agent {spec.agent!r}, but this worker is "
                f"{self._agent_id!r}; the Coordinator dispatched it to the wrong worker")
        authorized = authorize(spec)
        if authorized.depth > MAX_SPAWN_DEPTH:
            # Defence in depth. `authorize` already enforces this; re-asserted so the invariant
            # holds even if that check is ever weakened.
            raise PermissionError(f"depth {authorized.depth} exceeds max_spawn_depth {MAX_SPAWN_DEPTH}")
        self._executed.append(authorized.task_id)
        try:
            return self._run(authorized)
        except Exception as exc:  # noqa: BLE001 - narrowed to memory failures immediately below
            kind = _failure_kind_of(exc)
            if kind is None:
                # Not a memory failure: a bug in this worker or in the port's contract. It must
                # surface, not be reported to the engineer as "memory is unavailable".
                raise
            return self._failure(authorized, kind, exc)

    def _run(self, spec: TaskSpec) -> SubAgentResult:
        action = _requested_action(spec)
        if action == "recall":
            return self._recall(spec)
        if action == "retain":
            return self._retain(spec)
        raise MemoryFailure("schema", f"objective asks for an unsupported memory action: {spec.objective!r}")

    def _recall(self, spec: TaskSpec) -> SubAgentResult:
        view = self._port.recall_and_classify(spec.context.case_signature)
        # `HindsightMemoryPort` nests the classified rows under "report"; the flat "candidates" key
        # is accepted too so a simpler port implementation still works. Reading only the nested form
        # would silently report "no relevant case" on a real recall.
        report = view.get("report") if isinstance(view.get("report"), dict) else {}
        candidates = report.get("candidates") or view.get("candidates") or []
        if not candidates:
            # A clean "no relevant case" is a SUCCESS, not a failure: the backend answered. It must
            # stay distinguishable from an unreachable backend, which becomes status="failed".
            note = view.get("abstention") or report.get("abstention") or {}
            reason = str(note.get("reason") or "no relevant past case")
            return SubAgentResult(
                task_id=spec.task_id, agent=spec.agent, status="success",
                observations=(Artifact(kind="memory", ref=spec.context.case_signature,
                                       content=f"MEMORY (past case, not current-system evidence): "
                                               f"no relevant past case: {reason}"),))

        observations = tuple(
            Artifact(kind="memory",
                     ref=str(candidate.get("case_id") or f"candidate-{index}"),
                     content=_excerpt(candidate, spec.context.case_signature))
            for index, candidate in enumerate(candidates))
        return SubAgentResult(task_id=spec.task_id, agent=spec.agent, status="success",
                              observations=observations)

    def _retain(self, spec: TaskSpec) -> SubAgentResult:
        """Retention is a Coordinator decision, not a worker one.

        The worker is NOT given the authority to decide an engineering outcome, so retention is not
        performed here. The task is reported as a refusal with the reason, which keeps the boundary
        in code rather than in a prompt. A future phase that adds retention must decide the
        authorisation explicitly, not inherit it from this worker.
        """
        return SubAgentResult(
            task_id=spec.task_id, agent=spec.agent, status="failed",
            failure_kind="schema",
            failure_detail=(
                "retention is an engineer decision, not a worker action: the Memory Specialist "
                "produces MEMORY information only and cannot retain a case on its own authority"))

    def _failure(self, spec: TaskSpec, kind: str, exc: BaseException) -> SubAgentResult:
        reported = _FAILURE_KIND_MAP.get(kind, "unavailable")
        detail = f"memory {kind}: {exc}"
        return SubAgentResult(task_id=spec.task_id, agent=spec.agent, status="failed",
                              failure_kind=reported, failure_detail=detail)


def _requested_action(spec: TaskSpec) -> str:
    """Which memory operation the Coordinator asked for.

    The objective is a plain string, so this reads an explicit verb the Coordinator writes. An
    unrecognised objective is a schema failure rather than a guess, because guessing could run
    retrieval when retention was intended.
    """
    objective = spec.objective.lower()
    if "retain" in objective or "store" in objective:
        return "retain"
    if "recall" in objective or "retrieve" in objective or "past" in objective or "similar" in objective:
        return "recall"
    raise MemoryFailure("schema", f"objective names no known memory action: {spec.objective!r}")


def _excerpt(candidate: dict, case_signature: str) -> str:
    """One recalled case as a bounded, clearly-labelled memory observation.

    Explicitly marked as memory and as partial, so it can never be read as a current-system
    observation. Environment values are deliberately NOT restated as facts about the current case:
    a past environment is not the current environment.
    """
    parts = [f"MEMORY (past case, not current-system evidence) for: {case_signature}"]
    if candidate.get("case_id"):
        parts.append(f"case: {candidate['case_id']}")
    if candidate.get("relevance_class"):
        parts.append(f"relevance: {candidate['relevance_class']}")
    if candidate.get("outcome"):
        parts.append(f"outcome: {candidate['outcome']}")
    if candidate.get("text"):
        parts.append(f"recalled: {candidate['text']}")
    return "\n".join(parts)[:MAX_CASE_EXCERPT_CHARS]


def build_memory_specialist_task(
    *, task_id: str, case_signature: str, symptoms: tuple[str, ...] = (),
    objective: str = "recall similar past debugging cases", delegated_by: str = "coordinator",
) -> TaskSpec:
    """Build a TaskSpec for the Memory Specialist through the P1 authorisation seam.

    Goes through `build_task_spec` so the same schema validation, tool authorisation and depth rule
    that every other delegation uses apply here too. The worker does not construct its own spec.
    """
    from debugagent.agents.registry import build_task_spec

    definition = get_agent(MEMORY_SPECIALIST)
    # `build_task_spec` validates a plain dict and serialises through the P1 schema, so the context
    # is passed in its wire form. That keeps one validation path for every delegation.
    return build_task_spec({
        "task_id": task_id,
        "agent": MEMORY_SPECIALIST,
        "delegated_by": delegated_by,
        "objective": objective,
        "context": {"case_signature": case_signature, "symptoms": list(symptoms)},
        "allowed_tools": list(definition.allowed_tools),
        "model": definition.model,
        "depth": 1,
    })
