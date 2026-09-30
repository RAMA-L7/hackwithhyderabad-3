"""P6: the Code/Log Verifier worker.

The second real worker (ADR 001, migration phase P6: "Code/Log Verifier, then Patch Generator"). Like
the Memory Specialist it executes authorised tasks through the existing seam and returns a
`SubAgentResult` to the Coordinator - but where the Memory Specialist answers "what happened before",
this one answers "what does the CURRENT system actually say".

Four properties this module exists to guarantee, all structural rather than instructional:

1. **It reports observations; it does not verify truth.** Nothing here decides a root cause, confirms a
   hypothesis, or upgrades a status. The worker's output is an untrusted `SubAgentResult` whose
   artifacts carry `code:` / `log:` provenance, and the Coordinator may rank it but it can never
   satisfy an evidence or verification requirement.

2. **It never touches memory.** The worker's authorised tools are `read`, `grep` and `glob`. There is no
   port to Hindsight, no import of one, and a recalled case injected into the task context is
   explicitly REFUSED rather than quietly read - the registry prompt requires it, and a past case must
   not be laundered into an observation about the current system.

3. **Failure is never "nothing found".** A request with no inspection target is a `schema` failure, not
   an empty success. A file that does not exist becomes an observation saying so - the registry prompt
   requires the worker to say so rather than infer contents - and a batch where nothing could actually
   be read comes back `partial`, not `success`. Only a search that genuinely ran and matched nothing is
   a clean success, and even that carries an explicit observation.

4. **It uses only what it was authorised to use.** Each inspection step checks the tool against
   `spec.allowed_tools`, so a task that grants `grep` alone cannot make the worker open a file, even
   though the worker is capable of it.

P6 scope: this module is the Verifier only. The Patch Generator is NOT implemented, and there is no
scheduler, no pool and no concurrency here - `Coordinator.fan_out` (P5) is what runs tasks in parallel.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from debugagent.agents.registry import authorize, get_agent
from debugagent.agents.tasks import (
    MAX_SPAWN_DEPTH,
    Artifact,
    SubAgentResult,
    TaskSpec,
)

# The registry agent id this worker implements. `authorize()` refuses a TaskSpec naming any other
# agent, so the seam and the registry cannot drift apart.
CODE_LOG_VERIFIER = "code_log_verifier"

#: The tools this worker may use, mirrored from the roster for the AUTHORITY checks below. The
#: authoritative list is `get_agent(CODE_LOG_VERIFIER).allowed_tools`; a task is checked against the
#: spec's own grants as well, so a broader constant here could never widen what a task allows.
READ_TOOL = "read"
GREP_TOOL = "grep"
GLOB_TOOL = "glob"

#: How much of one file or match is carried back. A DISPLAY bound, not a truncation of engineering
#: truth: the observation says it is partial, so the Coordinator cannot read a clipped finding as a
#: whole one.
MAX_EXCERPT_CHARS = 2_000

#: Context artifact kinds that carry recalled memory. Handled explicitly, and refused - see property 2.
_MEMORY_ARTIFACT_KIND = "memory"


class SourceNotFound(RuntimeError):
    """A requested file or log does not exist.

    Not a failure. The worker was asked to inspect something that is not there, and the honest answer
    is "it does not exist" - which the registry prompt requires rather than inferring contents.
    """

    def __init__(self, path: str):
        super().__init__(f"no such file: {path}")
        self.path = path
        self.kind = "not_found"


class SourceUnavailable(RuntimeError):
    """The source could not be read for a reason other than absence.

    Distinct from `SourceNotFound` on purpose: absence is a finding, unavailability is a fault, and
    collapsing them would tell the engineer a file is missing when the disk is unreadable.
    """

    def __init__(self, kind: str, message: str):
        if kind not in ("unavailable", "invalid_pattern"):
            raise ValueError(f"unknown SourceUnavailable kind {kind!r}")
        super().__init__(f"source {kind}: {message}")
        self.kind = kind


class SourcePort(Protocol):
    """The read-only surface this worker needs. Mirrors the `MemoryPort` seam.

    Declared as a Protocol so the worker cannot reach a Hindsight client, a write API, or anything else
    it has no business touching: the only operations on this seam are reads.
    """

    def read(self, path: str) -> str:
        """Return the text at `path`. Raises `SourceNotFound` or `SourceUnavailable`."""

    def glob(self, pattern: str) -> tuple[str, ...]:
        """Return the paths matching `pattern`. Raises `SourceUnavailable`."""

    def grep(self, pattern: str, paths: Sequence[str]) -> tuple[tuple[str, int, str], ...]:
        """Return `(path, line_number, line)` for each match. Raises `SourceUnavailable`."""


class CodeLogVerifier:
    """Executes one read-only inspection task at a time, through the `SourcePort` seam.

    Construction takes the agent id from the registry and the port from the caller, so the worker cannot
    be wired to a backend it is not authorised for.

    Note there is no `client_access` attribute here, and that is deliberate. P5 decides serialisation from
    the registered `AgentDefinition`'s `client_access` capability, not from the worker, so a worker
    cannot opt out of the shared `MemoryLane` by declaring itself non-client. The roster already records
    this agent as `client_access=False`; nothing here is consulted, because nothing here reaches a client.
    """

    def __init__(self, port: SourcePort, *, agent: str = CODE_LOG_VERIFIER):
        self._port = port
        self._agent_id = agent
        # Resolved through the registry, so an unknown id is refused at construction and the worker's
        # tool surface comes from the roster rather than from constants in this module.
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
        """Run one task and return its result. Never raises for an expected source failure.

        Authorisation runs first, unchanged from P1: an unauthorised tool, the wrong agent, or
        depth > 1 is refused before any port call. The registry validates a spec against whichever agent
        it names, so the check that this spec is addressed to THIS worker is made explicitly below -
        otherwise a Memory Specialist task handed to the Verifier would be authorised and then executed
        against the wrong seam.
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
            return self._failure(authorized, exc.kind, exc)
        except SourceNotFound as exc:
            # Reaching here means a target vanished between planning and reading, or a port signalled
            # absence from a place the worker expected content. Reported as a finding, not a crash.
            return self._not_found(authorized, exc.path)

    def _run(self, spec: TaskSpec) -> SubAgentResult:
        objective = spec.objective.lower()
        for action in ("grep", "glob", "read", "inspect"):
            if action in objective:
                return self._inspect(spec, action)
        # An unrecognised objective is a schema failure rather than a guess: guessing could sweep
        # files the Coordinator never named, over a search it never asked for.
        return self._failure(spec, "schema",
                             SourceUnavailable("invalid_pattern",
                                               f"objective asks for no supported inspection action: "
                                               f"{spec.objective!r}"))

    def _inspect(self, spec: TaskSpec, action: str) -> SubAgentResult:
        # Property 2, enforced rather than prompted: a recalled case in the context is refused outright.
        # The registry prompt tells this worker a past case is not evidence about the current system;
        # relying on that alone would leave one refactor away from the observation being emitted.
        memory_refs = [artifact.ref for artifact in spec.context.artifacts
                       if artifact.kind == _MEMORY_ARTIFACT_KIND]
        if memory_refs:
            return self._failure(
                spec, "schema",
                SourceUnavailable("invalid_pattern",
                                  "refusing to inspect recalled memory: the Code/Log Verifier reads the "
                                  f"CURRENT system only, and was handed memory artifact(s) "
                                  f"{memory_refs}. A past case is not evidence about this issue"))

        if action == "grep":
            return self._grep(spec)
        if action == "glob":
            return self._glob(spec)
        return self._read_targets(spec)

    def _require_tool(self, spec: TaskSpec, tool: str) -> str | None:
        """None when `tool` is granted, else a failure detail explaining the refusal."""
        if tool in set(spec.allowed_tools):
            return None
        return (f"task {spec.task_id!r} did not grant the {tool!r} tool, which the "
                f"{spec.objective!r} objective requires")

    def _pattern(self, spec: TaskSpec, key: str, tool: str) -> tuple[str | None, str | None]:
        """Read a pattern out of the task's environment. Returns `(pattern, failure_detail)`."""
        for name, value in spec.context.environment:
            if name == key and value.strip():
                return value, None
        return None, (f"objective {spec.objective!r} requires a {key!r} in the task environment, and "
                      f"none was supplied")

    def _glob(self, spec: TaskSpec) -> SubAgentResult:
        """Resolve targets from a pattern, then read what it matched."""
        denied = self._require_tool(spec, GLOB_TOOL)
        if denied:
            return self._failure(spec, "schema", SourceUnavailable("invalid_pattern", denied))
        pattern, missing_pattern = self._pattern(spec, "glob_pattern", GLOB_TOOL)
        if missing_pattern:
            return self._failure(spec, "schema", SourceUnavailable("invalid_pattern", missing_pattern))
        try:
            matched = tuple(self._port.glob(pattern))
        except SourceUnavailable as exc:
            return self._failure(spec, exc.kind, exc)
        if not matched:
            # The glob RAN and matched nothing. That is a real answer and a clean success, but it says
            # so explicitly - an empty observation list would be indistinguishable from a fault.
            return SubAgentResult(
                task_id=spec.task_id, agent=spec.agent, status="success",
                observations=(Artifact(
                    kind="code", ref=pattern,
                    content=(f"OBSERVED for issue {spec.context.case_signature!r}: pattern {pattern!r} "
                             f"matched no paths. Current-system observation, not a verification.")),))
        return self._read_targets(spec, targets=list(matched), extra_note=f"matched by {pattern!r}")

    def _grep(self, spec: TaskSpec) -> SubAgentResult:
        """Search the named files for a pattern and report the matching lines."""
        denied = self._require_tool(spec, GREP_TOOL)
        if denied:
            return self._failure(spec, "schema", SourceUnavailable("invalid_pattern", denied))
        pattern, missing_pattern = self._pattern(spec, "grep_pattern", GREP_TOOL)
        if missing_pattern:
            return self._failure(spec, "schema", SourceUnavailable("invalid_pattern", missing_pattern))
        targets = _explicit_targets(spec)
        if not targets:
            return self._failure(
                spec, "schema",
                SourceUnavailable("invalid_pattern",
                                  f"objective {spec.objective!r} requires files to search, but the task "
                                  f"named no artifact to read"))
        try:
            hits = tuple(self._port.grep(pattern, targets))
        except SourceUnavailable as exc:
            return self._failure(spec, exc.kind, exc)
        if not hits:
            return SubAgentResult(
                task_id=spec.task_id, agent=spec.agent, status="success",
                observations=(Artifact(
                    kind="code", ref=pattern,
                    content=(f"OBSERVED for issue {spec.context.case_signature!r}: pattern {pattern!r} "
                             f"matched no lines in {targets}. The search ran; it found nothing. "
                             f"Current-system observation, not a verification.")),))
        observations = tuple(
            Artifact(kind=_artifact_kind(path), ref=f"{path}:{line_number}",
                     content=(f"OBSERVED match for {pattern!r} at {path} line {line_number}, for issue "
                              f"{spec.context.case_signature!r}. Current-system observation, not a "
                              f"verification and not a root cause.\n{_clip(text)}"))
            for path, line_number, text in hits)
        return SubAgentResult(task_id=spec.task_id, agent=spec.agent, status="success",
                              observations=observations)

    def _read_targets(self, spec: TaskSpec, targets: list[str] | None = None,
                      extra_note: str = "") -> SubAgentResult:
        """Read each target, reporting absence as a finding and unreadability as a fault."""
        paths = _explicit_targets(spec) if targets is None else list(targets)
        if not paths:
            return self._failure(
                spec, "schema",
                SourceUnavailable("invalid_pattern",
                                  "no inspection target supplied: the task named no artifact to read "
                                  "and no pattern to search, so there is nothing to observe"))

        may_read = self._require_tool(spec, READ_TOOL) is None
        observations: list[Artifact] = []
        read_count = 0
        missing: list[str] = []
        ungranted = 0

        for path in paths:
            try:
                if may_read:
                    text = self._read(spec, path)
                    read_count += 1
                    observations.append(Artifact(
                        kind=_artifact_kind(path), ref=path,
                        content=_excerpt(path, text, spec.context.case_signature, extra_note)))
                else:
                    # The task granted no `read`. Reporting that the file exists is not the same as
                    # reading it, and reading anyway would defeat the grant.
                    ungranted += 1
                    observations.append(Artifact(
                        kind=_artifact_kind(path), ref=path,
                        content=(f"NOT INSPECTED: {path} exists, but this task did not grant the "
                                 f"{READ_TOOL!r} tool, so its contents were not read")))
            except SourceNotFound:
                missing.append(path)
                observations.append(Artifact(
                    kind=_artifact_kind(path), ref=path,
                    content=f"DOES NOT EXIST: {path}. Contents are not inferred."))

        if read_count == 0:
            # Property 3: nothing was actually inspected, so this is not a clean success. `partial` is
            # the honest status - the worker did its job, and what it found was that it could not do it.
            reason = (f"all {len(missing)} requested target(s) are absent" if missing
                      else f"no target could be read: the task granted no {READ_TOOL!r} tool")
            return SubAgentResult(task_id=spec.task_id, agent=spec.agent, status="partial",
                                  observations=tuple(observations), failure_kind="unavailable",
                                  failure_detail=reason)
        return SubAgentResult(task_id=spec.task_id, agent=spec.agent,
                              status="partial" if missing else "success",
                              observations=tuple(observations),
                              failure_kind="unavailable" if missing else None,
                              failure_detail=(f"{len(missing)} of {len(paths)} target(s) absent"
                                                  if missing else None))

    def _read(self, spec: TaskSpec, path: str) -> str:
        if READ_TOOL not in set(spec.allowed_tools):
            raise PermissionError(
                f"task {spec.task_id!r} did not grant the {READ_TOOL!r} tool; refusing to read {path!r}")
        return self._port.read(path)

    def _not_found(self, spec: TaskSpec, path: str) -> SubAgentResult:
        return SubAgentResult(
            task_id=spec.task_id, agent=spec.agent, status="partial",
            observations=(Artifact(kind=_artifact_kind(path), ref=path,
                                   content=f"DOES NOT EXIST: {path}. Contents are not inferred."),),
            failure_kind="unavailable", failure_detail=f"target absent: {path}")

    def _failure(self, spec: TaskSpec, kind: str, exc: BaseException) -> SubAgentResult:
        # `schema` failures report as the agent vocabulary's `schema`; source faults report as
        # `unavailable`. The precise reason is always in `failure_detail`, so a refusal stays legible
        # after it crosses the seam.
        reported = "unavailable" if kind == "unavailable" else "schema"
        return SubAgentResult(task_id=spec.task_id, agent=spec.agent, status="failed",
                              failure_kind=reported, failure_detail=str(exc))


def _explicit_targets(spec: TaskSpec) -> list[str]:
    """The paths this task asks to inspect, in a deterministic order.

    Only EXPLICITLY supplied artifacts count. The issue signature is never globbed or searched: a
    pattern derived from it would sweep the tree looking for something nobody named, which is how a
    worker ends up reporting on files the engineer never asked about.
    """
    return [artifact.ref for artifact in spec.context.artifacts
            if artifact.kind in ("code", "log") and artifact.ref.strip()]


def _artifact_kind(path: str) -> str:
    """Which provenance label an observation carries: `log:` or `code:`.

    Derived from the reference, because that is the only thing the task supplied. A worker guessing
    `code` for a log excerpt would mislabel its own provenance, and provenance is what keeps a
    tool-derived observation distinct from an engineer-stated one.
    """
    lowered = path.lower()
    return "log" if any(marker in lowered for marker in (".log", "/logs/", "\\logs\\", ".txt")) else "code"


def _excerpt(path: str, text: str, case_signature: str, extra_note: str = "") -> str:
    """One file's observation, clipped to a display bound and labelled as an observation.

    The header states what was read and against which issue, and says plainly that this is an
    observation of the current system and not a verification. That disclaimer is part of the artifact's
    content rather than a convention in the renderer, so it cannot be lost by a caller that only carries
    `content` across.
    """
    body = _clip(text)
    clipped = "" if len(text) <= MAX_EXCERPT_CHARS else (
        f"\n... [clipped at {MAX_EXCERPT_CHARS} characters; this observation is PARTIAL]")
    note = f" ({extra_note})" if extra_note else ""
    return (f"OBSERVED in {path}{note} for issue {case_signature!r}. Current-system observation, not a "
            f"verification and not a root cause.\n{body}{clipped}")


def _clip(text: str) -> str:
    return text if len(text) <= MAX_EXCERPT_CHARS else text[:MAX_EXCERPT_CHARS]


def build_verifier_task(*, task_id: str, case_signature: str, targets: Sequence[str] = (),
                        symptoms: tuple[str, ...] = (), objective: str = "inspect the current system",
                        delegated_by: str = "coordinator") -> TaskSpec:
    """Build a `TaskSpec` for the Code/Log Verifier through the P1 authorisation seam.

    Goes through `build_task_spec` so the same schema validation, tool authorisation and depth rule
    that every other delegation uses apply here too. `targets` become `code`/`log` artifacts in the
    task context, which is the only way this worker learns what to inspect.
    """
    from debugagent.agents.registry import build_task_spec

    definition = get_agent(CODE_LOG_VERIFIER)
    # `build_task_spec` validates a plain dict and serialises through the P1 schema, so the context is
    # passed in its wire form. That keeps one validation path for every delegation.
    return build_task_spec({
        "task_id": task_id,
        "agent": CODE_LOG_VERIFIER,
        "delegated_by": delegated_by,
        "objective": objective,
        "context": {
            "case_signature": case_signature,
            "symptoms": list(symptoms),
            # Each target is an explicitly supplied context item, so `content` names what the
            # Coordinator is pointing at. `Artifact` requires non-empty content, and the path is the
            # honest value: the Coordinator supplied a location, not the file's contents.
            "artifacts": [{"kind": _artifact_kind(path), "ref": path,
                           "content": f"requested for inspection: {path}"}
                          for path in targets],
        },
        "allowed_tools": list(definition.allowed_tools),
        "model": definition.model,
        "depth": 1,
    })
