"""P1 hub-and-spoke delegation seam (docs/adr-001-hub-spoke-coordinator.md).

Data, authorization and dispatch. Nothing here imports the Phase 1 pipeline, so there is no path from
this package into evidence construction or verification, and nothing here talks to Hindsight directly
- a worker reaches memory only through the `MemoryPort` it was given.
"""

from debugagent.agents.coordinator import Coordinator, Dispatch
from debugagent.agents.registry import (
    AGENT_DEFINITION_FIELDS,
    COORDINATOR_TOOLS,
    WORKER_IDS,
    WORKER_ROSTER,
    AgentDefinition,
    AuthorizationError,
    agent_names,
    authorize,
    authorize_tool,
    build_task_spec,
    get_agent,
)
from debugagent.agents.tasks import (
    ARTIFACT_KINDS,
    DELEGATOR,
    FAILURE_KINDS,
    MAX_SPAWN_DEPTH,
    RESULT_STATUSES,
    TASK_TOOL,
    Artifact,
    SubAgentResult,
    TaskSpec,
    TaskSpecError,
    WorkerContext,
)

__all__ = [
    "AGENT_DEFINITION_FIELDS",
    "ARTIFACT_KINDS",
    "COORDINATOR_TOOLS",
    "DELEGATOR",
    "FAILURE_KINDS",
    "MAX_SPAWN_DEPTH",
    "RESULT_STATUSES",
    "TASK_TOOL",
    "WORKER_IDS",
    "WORKER_ROSTER",
    "AgentDefinition",
    "Artifact",
    "AuthorizationError",
    "Coordinator",
    "Dispatch",
    "SubAgentResult",
    "TaskSpec",
    "TaskSpecError",
    "WorkerContext",
    "agent_names",
    "authorize",
    "authorize_tool",
    "build_task_spec",
    "get_agent",
]
