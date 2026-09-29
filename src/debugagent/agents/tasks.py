"""P1 delegation schemas for the Hub-and-Spoke Coordinator (docs/adr-001-hub-spoke-coordinator.md).

This module holds *data only*: a validated delegation request, the isolated context handed to a
worker, and a validated worker result. There is no execution, no tool dispatch, and no LLM call
here. P1 is the seam; P2 adds the tool loop and P4+ runs workers.

Three invariants are structural, enforced by construction rather than by instruction:

1. **A TaskSpec can never carry the `task` tool.** `TASK_TOOL` is rejected in
   `TaskSpec.from_dict`, so no delegation request can ask a worker to delegate further.
2. **Depth is bounded.** `MAX_SPAWN_DEPTH = 1` (ADR "Tool Spawning"). Coordinator -> worker is
   depth 1; a second hop would be depth 2 and is rejected.
3. **Context is copied, never shared.** `WorkerContext` holds only immutable tuples, so a worker
   cannot reach coordinator state by holding a reference to it.

Trust boundary (ADR "Trust Hierarchy"): nothing here writes EVIDENCE, records an engineer
decision, retains memory, or applies a patch. A `SubAgentResult` is *untrusted input* that a later
phase must re-validate; per the ADR it is not a hypothesis, not evidence, and not a decision. This
package deliberately imports nothing from `debugagent.pipeline` or `debugagent.memory` — enforced
by a test — so no path exists from a worker result to the engineer's current-case facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# --- vocabulary ---------------------------------------------------------------------------------
# Coordination identities. `coordinator` is the only role that may delegate (ADR "Tool Spawning").
DELEGATOR = "coordinator"
TASK_TOOL = "task"

# ADR: "A max_spawn_depth = 1 counter is defence in depth." Coordinator -> worker is depth 1.
MAX_SPAWN_DEPTH = 1

# A worker result is success, partial (findings plus a failure), or failed. "partial" and "failed"
# are kept distinct from "success" so a degraded worker can never be read as a clean one.
RESULT_STATUSES = ("success", "partial", "failed")

# Failure vocabulary mirrors the classes the codebase already raises, so a worker failure maps onto
# an existing boundary rather than inventing a new one: MemoryFailure kinds are
# ("unavailable", "auth", "schema") and LLMError error classes include TIMEOUT / AUTH /
# INVALID_OUTPUT. `unavailable` is the ADR's "failure is never 'nothing found'" case.
FAILURE_KINDS = ("unavailable", "timeout", "auth", "invalid_output", "schema")

# What a worker is handed. Each kind carries a provenance label so that, per the ADR refinement,
# tool-derived observations keep a non-`engineer` source all the way to the DECISION section.
ARTIFACT_KINDS = ("log", "code", "memory")

# Required provenance prefix per kind. "log"/"code" may never claim to be engineer-stated.
ARTIFACT_SOURCE_PREFIX = {"log": "log:", "code": "code:", "memory": "memory:"}


class TaskSpecError(ValueError):
    """A delegation request is malformed or unauthorized. Fails closed, never coerced."""


def _errors_to_raise(errors: list[str], label: str) -> None:
    if errors:
        raise TaskSpecError(f"{label}: " + "; ".join(errors))


def _require_str(value: Any, label: str, errors: list[str]) -> str:
    if not isinstance(value, str):
        errors.append(f"{label}: expected string, got {type(value).__name__}")
        return ""
    if not value.strip():
        errors.append(f"{label}: must not be empty")
    return value


def _require_mapping(value: Any, label: str, errors: list[str]) -> dict:
    if not isinstance(value, dict):
        errors.append(f"{label}: expected object, got {type(value).__name__}")
        return {}
    return value


def _require_str_tuple(value: Any, label: str, errors: list[str], *, allow_empty: bool = False) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        errors.append(f"{label}: expected array of strings, got {type(value).__name__}")
        return ()
    out: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            errors.append(f"{label}[{index}]: expected string, got {type(item).__name__}")
        elif not item.strip():
            errors.append(f"{label}[{index}]: must not be empty")
        else:
            out.append(item)
    if not out and not allow_empty:
        errors.append(f"{label}: must not be empty")
    return tuple(out)


def _require_int(value: Any, label: str, errors: list[str]) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{label}: expected integer, got {type(value).__name__}")
        return 0
    return value


@dataclass(frozen=True)
class Artifact:
    """One explicitly supplied item of worker context or one worker observation.

    `content` is inlined rather than referenced: a spawned worker starts with a blank context
    window and cannot resolve an id. `ref` is the provenance label (file path, log id, case id) that
    the ADR requires to remain distinct from an `engineer` source.
    """

    kind: str
    ref: str
    content: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "ref": self.ref, "content": self.content}

    @property
    def source(self) -> str:
        """Provenance label for this artifact, e.g. `log:nginx-error`."""
        return f"{ARTIFACT_SOURCE_PREFIX[self.kind]}{self.ref}"

    @classmethod
    def from_dict(cls, data: Any, label: str, errors: list[str]) -> "Artifact":
        mapping = _require_mapping(data, label, errors)
        kind = _require_str(mapping.get("kind"), f"{label}.kind", errors)
        if kind and kind not in ARTIFACT_KINDS:
            errors.append(f"{label}.kind: '{kind}' is not one of {list(ARTIFACT_KINDS)}")
        ref = _require_str(mapping.get("ref"), f"{label}.ref", errors)
        content = _require_str(mapping.get("content"), f"{label}.content", errors, )
        if kind and kind in ARTIFACT_SOURCE_PREFIX and ref and ref.startswith(ARTIFACT_SOURCE_PREFIX[kind]):
            errors.append(f"{label}.ref: must not repeat the '{ARTIFACT_SOURCE_PREFIX[kind]}' prefix")
        return cls(kind=kind, ref=ref, content=content)


@dataclass(frozen=True)
class WorkerContext:
    """Immutable context handed to one worker. Explicit supply, no shared coordinator state.

    Every collection is a tuple, so a worker cannot append to, reorder, or otherwise mutate what the
    coordinator holds. `environment` is kept as sorted pairs so the whole record stays hashable.
    """

    case_signature: str
    symptoms: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    artifacts: tuple[Artifact, ...] = ()

    def env_dict(self) -> dict:
        return dict(self.environment)

    def to_dict(self) -> dict:
        return {
            "case_signature": self.case_signature,
            "symptoms": list(self.symptoms),
            "environment": [list(pair) for pair in self.environment],
            "artifacts": [a.to_dict() for a in self.artifacts],
        }

    @classmethod
    def from_dict(cls, data: Any, label: str, errors: list[str]) -> "WorkerContext":
        mapping = _require_mapping(data, label, errors)
        signature = _require_str(mapping.get("case_signature"), f"{label}.case_signature", errors)
        symptoms = _require_str_tuple(mapping.get("symptoms", []), f"{label}.symptoms", errors, allow_empty=True)

        raw_env = mapping.get("environment", {})
        # Accepted as a mapping, or as the sorted [key, value] pairs that `to_dict` emits, so a
        # context survives a serialize -> validate round trip unchanged.
        if isinstance(raw_env, dict):
            pairs: list[tuple[Any, Any]] = list(raw_env.items())
        elif isinstance(raw_env, (list, tuple)):
            pairs = []
            for index, pair in enumerate(raw_env):
                if isinstance(pair, (list, tuple)) and len(pair) == 2:
                    pairs.append((pair[0], pair[1]))
                else:
                    errors.append(f"{label}.environment[{index}]: expected a [key, value] pair")
        else:
            errors.append(f"{label}.environment: expected object or array of pairs, "
                          f"got {type(raw_env).__name__}")
            pairs = []

        environment: list[tuple[str, str]] = []
        for key, value in pairs:
            if not isinstance(key, str) or not key.strip():
                errors.append(f"{label}.environment: keys must be non-empty strings")
            elif not isinstance(value, str) or not value.strip():
                # An unstated fact is dropped rather than guessed (ADR: no invented values).
                continue
            else:
                environment.append((key, value))
        environment.sort()

        raw_artifacts = mapping.get("artifacts", [])
        artifacts: list[Artifact] = []
        if isinstance(raw_artifacts, (str, bytes)) or not isinstance(raw_artifacts, (list, tuple)):
            errors.append(f"{label}.artifacts: expected array, got {type(raw_artifacts).__name__}")
        else:
            for index, item in enumerate(raw_artifacts):
                artifacts.append(Artifact.from_dict(item, f"{label}.artifacts[{index}]", errors))

        return cls(case_signature=signature, symptoms=symptoms,
                   environment=tuple(environment), artifacts=tuple(artifacts))


@dataclass(frozen=True)
class TaskSpec:
    """One coordinator delegation request. Data only; P1 never executes it."""

    task_id: str
    agent: str
    delegated_by: str
    objective: str
    context: WorkerContext
    allowed_tools: tuple[str, ...]
    model: str
    depth: int = 1

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "agent": self.agent,
            "delegated_by": self.delegated_by,
            "objective": self.objective,
            "context": self.context.to_dict(),
            "allowed_tools": list(self.allowed_tools),
            "model": self.model,
            "depth": self.depth,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "TaskSpec":
        errors: list[str] = []
        mapping = _require_mapping(data, "TaskSpec", errors)
        task_id = _require_str(mapping.get("task_id"), "TaskSpec.task_id", errors)
        agent = _require_str(mapping.get("agent"), "TaskSpec.agent", errors)
        delegated_by = _require_str(mapping.get("delegated_by"), "TaskSpec.delegated_by", errors)
        objective = _require_str(mapping.get("objective"), "TaskSpec.objective", errors)
        model = _require_str(mapping.get("model"), "TaskSpec.model", errors)
        tools = _require_str_tuple(mapping.get("allowed_tools"), "TaskSpec.allowed_tools", errors)
        context = WorkerContext.from_dict(mapping.get("context"), "TaskSpec.context", errors)
        depth = _require_int(mapping.get("depth", 1), "TaskSpec.depth", errors)

        # Anti-recursion, structural. A delegation request can never ask for the `task` tool, so a
        # worker cannot be handed a means of delegating further no matter who wrote the request.
        if TASK_TOOL in tools:
            errors.append(f"TaskSpec.allowed_tools: '{TASK_TOOL}' is coordinator-only and must not "
                          "appear in a delegation request")
        if depth < 1:
            errors.append("TaskSpec.depth: a delegation is at least depth 1")
        if depth > MAX_SPAWN_DEPTH:
            errors.append(f"TaskSpec.depth: {depth} exceeds max_spawn_depth {MAX_SPAWN_DEPTH} "
                          "(workers may not delegate)")
        if delegated_by and delegated_by != DELEGATOR:
            errors.append(f"TaskSpec.delegated_by: '{delegated_by}' may not delegate; only "
                          f"'{DELEGATOR}' may")
        unknown = set(mapping) - {"task_id", "agent", "delegated_by", "objective", "context",
                                  "allowed_tools", "model", "depth"}
        for key in sorted(unknown):
            errors.append(f"TaskSpec.{key}: not allowed")

        _errors_to_raise(errors, "TaskSpec")
        return cls(task_id=task_id, agent=agent, delegated_by=delegated_by, objective=objective,
                   context=context, allowed_tools=tools, model=model, depth=depth)


@dataclass(frozen=True)
class SubAgentResult:
    """One worker result, as untrusted input to the coordinator.

    `status` keeps partial and failed distinguishable from success, and a failed result must carry a
    `failure_kind` (ADR: "Failure is never 'nothing found'"). Observations are artifacts carrying
    their own provenance, never evidence items.
    """

    task_id: str
    agent: str
    status: str
    observations: tuple[Artifact, ...] = ()
    failure_kind: str | None = None
    failure_detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "agent": self.agent,
            "status": self.status,
            "observations": [a.to_dict() for a in self.observations],
            "failure_kind": self.failure_kind,
            "failure_detail": self.failure_detail,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "SubAgentResult":
        errors: list[str] = []
        mapping = _require_mapping(data, "SubAgentResult", errors)
        task_id = _require_str(mapping.get("task_id"), "SubAgentResult.task_id", errors)
        agent = _require_str(mapping.get("agent"), "SubAgentResult.agent", errors)
        status = _require_str(mapping.get("status"), "SubAgentResult.status", errors)
        if status and status not in RESULT_STATUSES:
            errors.append(f"SubAgentResult.status: '{status}' is not one of {list(RESULT_STATUSES)}")

        failure_kind = mapping.get("failure_kind")
        if failure_kind is not None:
            failure_kind = _require_str(failure_kind, "SubAgentResult.failure_kind", errors)
            if failure_kind and failure_kind not in FAILURE_KINDS:
                errors.append(f"SubAgentResult.failure_kind: '{failure_kind}' is not one of {list(FAILURE_KINDS)}")
        failure_detail = mapping.get("failure_detail")
        if failure_detail is not None:
            failure_detail = _require_str(failure_detail, "SubAgentResult.failure_detail", errors)

        raw_observations = mapping.get("observations", [])
        observations: list[Artifact] = []
        if isinstance(raw_observations, (str, bytes)) or not isinstance(raw_observations, (list, tuple)):
            errors.append(f"SubAgentResult.observations: expected array, got {type(raw_observations).__name__}")
        else:
            for index, item in enumerate(raw_observations):
                observations.append(Artifact.from_dict(item, f"SubAgentResult.observations[{index}]", errors))

        # Status/failure coherence: a clean success has findings and no failure; a failure has a
        # kind and no findings; partial has both. Anything else is rejected rather than guessed.
        if status == "success":
            if failure_kind is not None:
                errors.append("SubAgentResult: a successful result must not carry a failure_kind")
            if not observations:
                errors.append("SubAgentResult: a successful result must carry at least one observation")
        elif status == "partial":
            if failure_kind is None:
                errors.append("SubAgentResult: a partial result must state which failure degraded it")
            if not observations:
                errors.append("SubAgentResult: a partial result must carry at least one observation")
        elif status == "failed":
            if failure_kind is None:
                errors.append("SubAgentResult: a failed result must carry a failure_kind "
                              "(a failure is never 'nothing found')")
            if observations:
                errors.append("SubAgentResult: a failed result must not carry observations")

        for key in sorted(set(mapping) - {"task_id", "agent", "status", "observations",
                                           "failure_kind", "failure_detail"}):
            errors.append(f"SubAgentResult.{key}: not allowed")

        _errors_to_raise(errors, "SubAgentResult")
        return cls(task_id=task_id, agent=agent, status=status, observations=tuple(observations),
                   failure_kind=failure_kind, failure_detail=failure_detail)
