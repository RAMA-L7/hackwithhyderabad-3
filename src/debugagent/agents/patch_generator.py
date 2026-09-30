"""P6: the Patch Generator worker.

The third registered worker (ADR 001, migration phase P6: "Code/Log Verifier, then Patch Generator").
Where the Memory Specialist answers "what happened before" and the Code/Log Verifier answers "what does
the CURRENT system say", this one answers "what change would address it" - and stops there. The patch
is a PROPOSAL, carried back in a `SubAgentResult` for the engineer to accept, amend or discard.

Four properties this module exists to guarantee, all structural rather than instructional:

1. **It proposes; it never applies.** The worker's only seam is read-only and has exactly two
   operations, `read` and `diff` - there is no write, no apply, no commit and no VCS call anywhere in
   this module, so there is no path from here to a modified working tree. Applying a patch is the
   engineer's decision, on the engineer's machine.

2. **A proposal is not a verification.** Every artifact is prefixed `PROPOSED PATCH` and states that it
   is unapplied and unverified. Nothing here claims the change is correct, safe, or even complete; the
   same trust rule as every other worker applies, and it is stated inside the artifact's content so a
   caller carrying only `content` cannot lose it.

3. **It never touches memory.** Its authorised tools are `read` and `diff`. There is no Hindsight import
   and no memory seam, and a recalled case injected into the task context is REFUSED rather than read -
   a past case is not a basis for changing the current repository.

4. **Failure, refusal and insufficient input are all distinct from success.** A task with no target, no
   proposed content, or an empty proposal is `failed` with `failure_kind="schema"`; a port fault is
   `failed` with `unavailable`; a wrong-agent or unauthorised task is refused by `PermissionError` or
   `AuthorizationError` before any seam call. None of them is a success, and in particular a missing
   input is never reported as "no patch needed" - that phrase belongs only to a genuine comparison that
   found the proposal identical to the current file.

P6 scope: this module is the Patch Generator only. There is no scheduler, no pool and no concurrency
here - `Coordinator.fan_out` (P5) is what runs tasks in parallel.
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
PATCH_GENERATOR = "patch_generator"

#: The two tools this worker may use, mirrored from the roster for the AUTHORITY checks below. The
#: authoritative list is `get_agent(PATCH_GENERATOR).allowed_tools`.
READ_TOOL = "read"
DIFF_TOOL = "diff"

#: The task environment key carrying the proposed new content for the target file.
PROPOSED_KEY = "proposed_content"

#: How much of one diff is carried back. A DISPLAY bound; the artifact says when it is clipped, so a
#: partial diff cannot be read as a whole one.
MAX_DIFF_CHARS = 4_000

#: Context artifact kind carrying recalled memory. Refused outright - see property 3.
_MEMORY_ARTIFACT_KIND = "memory"

#: The header every proposed patch carries. Constant so the wording cannot drift per call site.
PROPOSAL_HEADER = ("PROPOSED PATCH (unapplied, unverified): a candidate change for the engineer to "
                   "review. It has NOT been written, applied, committed or run, and it is not a "
                   "verified fix.")


class SourceNotFound(RuntimeError):
    """A requested file does not exist.

    Not a failure of the worker: the file it was asked to patch is not there, which is a finding about
    the current repository and not a fault. Distinct from `SourceUnavailable` on purpose - absence is a
    finding, unavailability is a fault.
    """

    def __init__(self, path: str):
        super().__init__(f"no such file: {path}")
        self.path = path
        self.kind = "not_found"


class SourceUnavailable(RuntimeError):
    """The repository could not be read or diffed for a reason other than absence."""

    def __init__(self, kind: str, message: str):
        if kind not in ("unavailable", "invalid_patch"):
            raise ValueError(f"unknown SourceUnavailable kind {kind!r}")
        super().__init__(f"repository {kind}: {message}")
        self.kind = kind


class PatchSourcePort(Protocol):
    """The READ-ONLY surface this worker needs.

    Deliberately has no mutating method. A worker whose seam offers `write` would have a path to a
    modified working tree even if every current call site stayed read-only, so the absence is in the
    type rather than in this module's discipline. Both operations correspond one-to-one with an
    authorised tool, and each is gated on the task's grant in `execute`.
    """

    def read(self, path: str) -> str:
        """Return the current text at `path`. Raises `SourceNotFound` or `SourceUnavailable`."""

    def diff(self, path: str, proposed: str) -> tuple[str, ...]:
        """Return unified-diff lines turning the current `path` into `proposed`.

        Empty means the proposal is byte-identical to the current file. Read-only: it compares, it
        never writes.
        """


class PatchGenerator:
    """Proposes one patch at a time, through the read-only `PatchSourcePort` seam.

    Construction takes the agent id from the registry and the port from the caller, so the worker cannot
    be wired to a repository it is not authorised for.

    Note there is no `client_access` attribute here, and that is deliberate. P5 decides serialisation from
    the registered `AgentDefinition`'s `client_access` capability, not from the worker, so a worker
    cannot opt out of the shared `MemoryLane` by declaring itself non-client. The roster already records
    this agent as `client_access=False`; nothing here is consulted, because nothing here reaches a client.
    """

    def __init__(self, port: PatchSourcePort, *, agent: str = PATCH_GENERATOR):
        self._port = port
        self._agent_id = agent
        # Resolved through the registry, so an unknown id is refused at construction and the worker's
        # tool surface comes from the roster rather than from constants in this module.
        self._definition = get_agent(agent)
        self._proposed: list[str] = []

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
        """Run one task and return its result. Never raises for an expected repository failure.

        Authorisation runs first, unchanged from P1: an unauthorised tool, the wrong agent, or
        depth > 1 is refused before any seam call. The registry validates a spec against whichever agent
        it names, so the check that this spec is addressed to THIS worker is made explicitly below -
        otherwise a Verifier task handed to this worker would be authorised and then executed against the
        wrong seam.
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
        try:
            return self._run(authorized)
        except SourceUnavailable as exc:
            return self._failure(authorized, exc)
        except SourceNotFound as exc:
            # The file to patch vanished, or the seam signalled absence where content was expected.
            # `partial`: the worker did its job and the answer is that it could not.
            return SubAgentResult(
                task_id=authorized.task_id, agent=authorized.agent, status="partial",
                observations=(Artifact(
                    kind="code", ref=exc.path,
                    content=(f"{PROPOSAL_HEADER}\nNO PATCH PRODUCED: {exc.path} does not exist, so "
                             f"there is nothing to propose a change against. Contents are not "
                             f"invented.")),),
                failure_kind="unavailable", failure_detail=f"target absent: {exc.path}")

    def _run(self, spec: TaskSpec) -> SubAgentResult:
        # Property 3, enforced rather than prompted: a recalled case in the context is refused
        # outright. The registry prompt tells this worker a past case is not a basis for changing the
        # current repository; relying on that alone would leave one refactor away from it being used.
        memory_refs = [artifact.ref for artifact in spec.context.artifacts
                       if artifact.kind == _MEMORY_ARTIFACT_KIND]
        if memory_refs:
            return self._failure(spec, SourceUnavailable(
                "invalid_patch",
                "refusing to build a patch from recalled memory: the Patch Generator proposes changes to "
                f"the CURRENT repository only, and was handed memory artifact(s) {memory_refs}. A past "
                "case is not evidence about this issue"))
        return self._propose(spec)

    def _propose(self, spec: TaskSpec) -> SubAgentResult:
        # The objective must actually ask for a change. Without this, a task sent to the wrong worker
        # with a well-formed proposal would quietly get a patch - so "retain this case" or "inspect the
        # logs" would succeed here rather than being refused. An unrecognised objective is a refusal,
        # not a guess: guessing could propose a change the Coordinator never asked for.
        objective = spec.objective.lower()
        if not any(verb in objective for verb in ("patch", "propose", "change", "diff", "fix")):
            return self._failure(spec, SourceUnavailable(
                "invalid_patch",
                f"objective {spec.objective!r} asks for no patch action. The Patch Generator proposes "
                f"changes only; it does not retain, verify or inspect"))

        targets = _explicit_targets(spec)
        if not targets:
            return self._failure(spec, SourceUnavailable(
                "invalid_patch",
                "no target supplied: the task named no file to patch, so there is nothing to propose a "
                "change to. This is missing input, not a finding that no patch is needed"))
        if len(targets) > 1:
            # One file per proposal, deliberately. A multi-file change has no single correct base for a
            # diff, and guessing one would produce a patch that silently applies to the wrong context.
            return self._failure(spec, SourceUnavailable(
                "invalid_patch",
                f"a proposal targets exactly one file, but the task named {targets}. Split it into one "
                f"task per file rather than guessing a diff base"))
        proposed = _proposed_content(spec)
        if proposed is None:
            return self._failure(spec, SourceUnavailable(
                "invalid_patch",
                f"no {PROPOSED_KEY!r} supplied in the task environment, so there is no proposed change "
                f"to render. This is missing input, not a finding that no patch is needed"))
        if not proposed.strip():
            return self._failure(spec, SourceUnavailable(
                "invalid_patch",
                f"{PROPOSED_KEY!r} is empty. An empty proposal is not a patch, and is not reported as a "
                f"successful 'no change needed'"))

        path = targets[0]
        granted = set(spec.allowed_tools)
        if READ_TOOL not in granted:
            return self._failure(spec, SourceUnavailable(
                "invalid_patch",
                f"task {spec.task_id!r} did not grant the {READ_TOOL!r} tool, which proposing a change "
                f"to {path!r} requires: the current contents are the diff base"))
        if DIFF_TOOL not in granted:
            return self._failure(spec, SourceUnavailable(
                "invalid_patch",
                f"task {spec.task_id!r} did not grant the {DIFF_TOOL!r} tool, so no patch can be "
                f"produced. Only read and diff are available to this worker, and neither applies a "
                f"change"))

        current = self._port.read(path)
        lines = tuple(self._port.diff(path, proposed))
        self._proposed.append(path)
        if not lines:
            # A genuine comparison that found no difference. This IS a clean success, and it says so -
            # the distinction from the missing-input failures above is exactly why those are failures.
            return SubAgentResult(
                task_id=spec.task_id, agent=spec.agent, status="success",
                observations=(Artifact(
                    kind="code", ref=path,
                    content=(f"{PROPOSAL_HEADER}\nNO CHANGE PROPOSED for {path}: the proposal is "
                             f"byte-identical to the current file. The comparison ran; it found no "
                             f"difference.")),))
        return SubAgentResult(
            task_id=spec.task_id, agent=spec.agent, status="success",
            observations=(Artifact(
                kind="code", ref=path,
                content=_render(path, lines, spec.context.case_signature)),))

    def _failure(self, spec: TaskSpec, exc: SourceUnavailable) -> SubAgentResult:
        # `unavailable` faults report as the agent vocabulary's `unavailable`; everything the worker
        # refuses about the request itself reports as `schema`. The precise reason is always in
        # `failure_detail`, so a refusal stays legible after it crosses the seam.
        reported = "unavailable" if exc.kind == "unavailable" else "schema"
        return SubAgentResult(task_id=spec.task_id, agent=spec.agent, status="failed",
                              failure_kind=reported, failure_detail=str(exc))


def _explicit_targets(spec: TaskSpec) -> list[str]:
    """The file this task asks to patch, in a deterministic order.

    Only EXPLICITLY supplied artifacts count. The issue signature is never globbed or searched: a pattern
    derived from it would sweep the tree looking for something nobody named, and a patch against a file
    the engineer never pointed at is worse than no patch.
    """
    return [artifact.ref for artifact in spec.context.artifacts
            if artifact.kind in ("code", "log") and artifact.ref.strip()]


def _proposed_content(spec: TaskSpec) -> str | None:
    """The proposed new content from the task environment, or None when it was not supplied."""
    for name, value in spec.context.environment:
        if name == PROPOSED_KEY:
            return value
    return None


def _render(path: str, lines: Sequence[str], case_signature: str) -> str:
    """One proposal, clipped to a display bound and labelled as a proposal.

    The header states that this is unapplied and unverified, names the issue it was drafted for, and
    names the file - so the artifact is self-describing wherever it is carried.
    """
    body = "\n".join(lines)
    truncated = body[:MAX_DIFF_CHARS]
    clipped = "" if len(body) <= MAX_DIFF_CHARS else (
        f"\n... [clipped at {MAX_DIFF_CHARS} characters; this proposal is PARTIAL]")
    return (f"{PROPOSAL_HEADER}\nTarget: {path}\nIssue: {case_signature!r}\n"
            f"{truncated}{clipped}")


def build_patch_task(*, task_id: str, case_signature: str, target: str, proposed: str,
                     objective: str = "propose a patch for the current repository",
                     delegated_by: str = "coordinator") -> TaskSpec:
    """Build a `TaskSpec` for the Patch Generator through the P1 authorisation seam.

    Goes through `build_task_spec` so the same schema validation, tool authorisation and depth rule that
    every other delegation uses apply here too. `target` becomes a `code` artifact and `proposed` is
    carried in the task environment - which is the only way this worker learns what to propose.
    """
    from debugagent.agents.registry import build_task_spec
    from debugagent.agents.tasks import WorkerContext

    definition = get_agent(PATCH_GENERATOR)
    # `build_task_spec` validates a plain dict and serialises through the P1 schema, so the context is
    # passed in its wire form. That keeps one validation path for every delegation.
    return build_task_spec({
        "task_id": task_id,
        "agent": PATCH_GENERATOR,
        "delegated_by": delegated_by,
        "objective": objective,
        "context": {
            "case_signature": case_signature,
            "artifacts": [{"kind": "code", "ref": target,
                           "content": f"requested for patching: {target}"}],
            "environment": [[PROPOSED_KEY, proposed]],
        },
        "allowed_tools": list(definition.allowed_tools),
        "model": definition.model,
        "depth": 1,
    })
