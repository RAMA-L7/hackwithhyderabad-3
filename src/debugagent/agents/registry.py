"""P1 closed sub-agent registry and tool authorization (docs/adr-001-hub-spoke-coordinator.md).

The roster is closed: exactly the three workers named in the ADR, and nothing else. An unknown
agent identity is rejected rather than created. Each worker carries the ADR's four-field Agent
Definition Payload (`description`, `prompt`, `allowed_tools`, `model`) plus the P5 `client_access`
capability, which says whether the agent can reach the Hindsight client and therefore whether its
tasks must be serialised. That is the only field beyond the ADR's four, and it is listed in
`AGENT_DEFINITION_FIELDS` so it cannot be added quietly.

Tool permission is per-agent and structural. `task` is granted to the coordinator only; no worker's
`allowed_tools` contains it, and `authorize()` re-checks a `TaskSpec` against the registry so a
hand-built request cannot widen a worker's permissions. `TaskSpec.from_dict` independently refuses
the `task` tool, so the rule holds even if this module is bypassed.

No worker is executed here. `build_task_spec` validates a delegation request and returns it; what
runs, and when, is P2 (tool loop) and P4+ (worker execution).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from debugagent.agents.tasks import DELEGATOR, TASK_TOOL, TaskSpec, TaskSpecError

# The payload is exactly these fields: the ADR's four, plus the P5 `client_access` capability.
# `client_access` is a CAPABILITY, not a permission: it records whether executing this agent's tasks
# can reach the Hindsight client, which is what decides whether a task must be serialised through the
# shared `MemoryLane`. It grants nothing - authorisation is unchanged and still checks tools, delegator,
# model and depth - and it is deliberately on the definition, so a worker cannot opt itself out of the
# lane by forgetting to declare something.
AGENT_DEFINITION_FIELDS = ("description", "prompt", "allowed_tools", "model", "client_access")

# ADR: "`task` is granted **only** to the Coordinator." Exact tool sets, not extended.
COORDINATOR_TOOLS: tuple[str, ...] = ("task", "read", "grep", "glob")


class AuthorizationError(TaskSpecError):
    """An agent identity or tool request is not permitted. Fails closed."""


class RegistrationError(TaskSpecError):
    """A worker could not be attached to a Coordinator.

    Separate from `AuthorizationError` because it says nothing about whether a TASK is permitted: the
    task may be perfectly authorised, and the problem is that the identity has no worker, or already has
    a different one. Keeping them apart stops a wiring mistake from being reported as a permission
    problem, which would send a reader looking in the wrong place.
    """


@dataclass(frozen=True)
class AgentDefinition:
    """The definition payload. No field exists that is not listed in `AGENT_DEFINITION_FIELDS`.

    `client_access` says whether running this agent's tasks can reach the Hindsight client, and so
    whether they must be serialised through the shared `MemoryLane`. It lives HERE rather than on the
    worker class so that it cannot be forgotten: a worker that reached the client without the roster
    saying so would run unsynchronised, and no test of the worker would catch it.
    """

    description: str
    prompt: str
    allowed_tools: tuple[str, ...]
    model: str
    client_access: bool

    def permits(self, tool: str) -> bool:
        return tool in self.allowed_tools

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "prompt": self.prompt,
            "allowed_tools": list(self.allowed_tools),
            "model": self.model,
            "client_access": self.client_access,
        }

    @classmethod
    def from_dict(cls, data: Any, label: str) -> "AgentDefinition":
        errors: list[str] = []
        if not isinstance(data, dict):
            raise AuthorizationError(f"{label}: expected object, got {type(data).__name__}")
        for key in sorted(set(data) - set(AGENT_DEFINITION_FIELDS)):
            errors.append(f"{label}.{key}: not one of the payload fields {list(AGENT_DEFINITION_FIELDS)}")
        for key in AGENT_DEFINITION_FIELDS:
            if key not in data:
                errors.append(f"{label}.{key}: required")
        tools = data.get("allowed_tools")
        if not isinstance(tools, (list, tuple)) or isinstance(tools, str) or not tools:
            errors.append(f"{label}.allowed_tools: expected a non-empty array")
            tools = ()
        elif not all(isinstance(t, str) and t.strip() for t in tools):
            errors.append(f"{label}.allowed_tools: every tool must be a non-empty string")
        for key in ("description", "prompt", "model"):
            value = data.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{label}.{key}: expected a non-empty string")
        if not isinstance(data.get("client_access"), bool):
            # Required, not defaulted. A missing capability would have to mean SOMETHING, and both
            # readings are dangerous in opposite directions: defaulting to True costs parallelism,
            # defaulting to False lets a client-touching agent run unsynchronised.
            errors.append(f"{label}.client_access: expected a boolean")
        if TASK_TOOL in tuple(tools or ()):
            # Belt and braces: `tasks.TaskSpec` already refuses this, and the roster below is
            # asserted tool-clean by tests.
            errors.append(f"{label}.allowed_tools: '{TASK_TOOL}' is coordinator-only")
        if errors:
            raise AuthorizationError(f"{label}: " + "; ".join(errors))
        return cls(description=str(data["description"]), prompt=str(data["prompt"]),
                   allowed_tools=tuple(tools), model=str(data["model"]),
                   client_access=data["client_access"])


_MEMORY_SPECIALIST = AgentDefinition(
    description=(
        "Recall and characterise past debugging cases from the Hindsight bank. Use when the "
        "coordinator needs prior experience for the CURRENT issue. Returns recalled cases with "
        "their relevance classes; it does NOT verify anything about the current system."
    ),
    prompt=(
        "You are the Memory Specialist in a debugging hub. You answer ONE query: what past cases "
        "exist that resemble the CURRENT ISSUE?\n\n"
        "You receive, already injected:\n"
        "- CURRENT ISSUE: signature, symptoms, environment now\n"
        "- The memory bank to query\n\n"
        "Rules:\n"
        "- Report recalled cases with their case_id, relevance class, original environment, and any "
        "recorded failed approaches.\n"
        "- Past cases are prior experience, NEVER evidence about the current system.\n"
        "- Do not describe the current system using a past case's service, version or environment.\n"
        "- If nothing clears the usable floor, say so plainly and give the abstention reason. Do not "
        "pad with weak matches.\n"
        "- You have no tool to delegate further. Return findings only."
    ),
    allowed_tools=("hindsight_recall", "hindsight_get_facts"),
    model="primary",
    # The only registered agent that reaches the Hindsight client, and so the only one whose
    # tasks must be serialised through the shared MemoryLane.
    client_access=True,
)

_CODE_LOG_VERIFIER = AgentDefinition(
    description=(
        "Inspect repository files and logs for the CURRENT issue and report observed facts. Use to "
        "ground a hypothesis in something the engineer can check. Reports what the files say; does "
        "not decide root cause."
    ),
    prompt=(
        "You are the Code/Log Verifier in a debugging hub. You inspect the CURRENT system only.\n\n"
        "You receive, already injected:\n"
        "- CURRENT ISSUE: signature, symptoms, environment now\n"
        "- Explicit file paths and/or log excerpts to inspect\n\n"
        "Rules:\n"
        "- Report only what you actually observed, quoting file paths and line references.\n"
        "- A recalled past case may appear in your context. It is NOT evidence about the current "
        "system. Never use one to fill a gap in current observation.\n"
        "- If the requested file or log does not exist, say so. Do not infer its contents.\n"
        "- You have no tool to delegate further. Return observations only."
    ),
    allowed_tools=("read", "grep", "glob"),
    model="primary",
    # Reads the current repository and logs; never the memory bank.
    client_access=False,
)

_PATCH_GENERATOR = AgentDefinition(
    description=(
        "Draft a candidate fix as a unified diff against the CURRENT repository state. Use only "
        "after a hypothesis exists. Produces a proposal for the engineer, never an applied change."
    ),
    prompt=(
        "You are the Patch Generator in a debugging hub. You propose; you never apply.\n\n"
        "You receive, already injected:\n"
        "- The chosen hypothesis and its refutation conditions\n"
        "- The exact file paths you may read\n"
        "- The current repository state\n\n"
        "Rules:\n"
        "- Emit a unified diff ONLY. Never execute commands, never write files, never run tests.\n"
        "- Do not repeat an approach a past case recorded as failed, unless you state why it would "
        "differ now.\n"
        "- If the fix depends on a fact not yet verified, mark it insufficient rather than assuming "
        "it.\n"
        "- You have no tool to delegate further. Return the diff only."
    ),
    allowed_tools=("read", "diff"),
    model="primary",
    # Proposes a diff; never the memory bank.
    client_access=False,
)

# Closed roster: exactly the three ADR workers. Adding an agent is a code change, on purpose.
WORKER_ROSTER: Mapping[str, AgentDefinition] = {
    "memory_specialist": _MEMORY_SPECIALIST,
    "code_log_verifier": _CODE_LOG_VERIFIER,
    "patch_generator": _PATCH_GENERATOR,
}

WORKER_IDS: tuple[str, ...] = tuple(WORKER_ROSTER)

# The coordinator is not a worker and is not in the roster: it is the only delegating role, and its
# tool set is the only one that contains `task`.
_COORDINATOR = AgentDefinition(
    description=("Decomposes an investigation into tasks, delegates to the closed worker roster, "
                 "and aggregates untrusted worker results into a proposal. Never verifies and never "
                 "decides; those stay with the engineer."),
    prompt=("You are the Coordinator. You decompose the task, delegate to registered workers, and "
            "aggregate their untrusted results. You never write evidence, never decide, and never "
            "retain memory."),
    allowed_tools=COORDINATOR_TOOLS,
    model="primary",
    # The coordinator delegates; it never runs a worker's task and never reaches the client.
    client_access=False,
)



def agent_names() -> tuple[str, ...]:
    return WORKER_IDS


def get_agent(agent_id: str) -> AgentDefinition:
    """The closed roster. An unknown identity is rejected; it is never created."""
    if agent_id == DELEGATOR:
        return _COORDINATOR
    definition = WORKER_ROSTER.get(agent_id)
    if definition is None:
        raise AuthorizationError(
            f"unknown agent {agent_id!r}: the registry is closed to {list(WORKER_IDS)}")
    return definition


def authorize_tool(agent_id: str, tool: str) -> None:
    """Raise unless `agent_id` may use `tool`. A worker can never be granted `task`."""
    definition = get_agent(agent_id)
    if not definition.permits(tool):
        if tool == TASK_TOOL:
            raise AuthorizationError(
                f"agent {agent_id!r} may not use {TASK_TOOL!r}: only {DELEGATOR!r} delegates "
                f"(max_spawn_depth 1)")
        raise AuthorizationError(
            f"agent {agent_id!r} may not use {tool!r}: permitted tools are {list(definition.allowed_tools)}")


def authorize(spec: TaskSpec) -> TaskSpec:
    """Check a validated TaskSpec against the roster. Returns it unchanged, or raises.

    Checks, in order: the target is a registered worker; the delegator is the coordinator; every
    requested tool is on that worker's list; the model matches the registered definition; and depth
    is still within `MAX_SPAWN_DEPTH` (already checked by the schema, re-asserted here so the rule
    holds even for a directly-constructed spec).
    """
    from debugagent.agents.tasks import MAX_SPAWN_DEPTH

    if spec.agent == DELEGATOR:
        raise AuthorizationError("a task may not target the coordinator: the coordinator delegates, it does not run")
    definition = get_agent(spec.agent)
    if spec.delegated_by != DELEGATOR:
        raise AuthorizationError(f"only {DELEGATOR!r} may delegate; {spec.delegated_by!r} may not")
    for tool in spec.allowed_tools:
        authorize_tool(spec.agent, tool)
    if spec.model != definition.model:
        raise AuthorizationError(
            f"agent {spec.agent!r} is registered with model {definition.model!r}, not {spec.model!r}")
    if spec.depth > MAX_SPAWN_DEPTH:
        raise AuthorizationError(f"depth {spec.depth} exceeds max_spawn_depth {MAX_SPAWN_DEPTH}")
    return spec


def build_task_spec(data: Any) -> TaskSpec:
    """Schema-validate then authorize a delegation request. The only P1 entry point for one."""
    return authorize(TaskSpec.from_dict(data))
