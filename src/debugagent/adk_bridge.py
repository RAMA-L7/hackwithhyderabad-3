"""P5 evaluation: an ADK `Workflow` driving the existing Coordinator, workers untouched.

This module is a PROTOTYPE for one question: can Google ADK own the orchestration of the three P6
workers without weakening any trust boundary this repository has established? It is deliberately
additive - it imports the real `Coordinator`, the real workers and the real composition root, and it
removes nothing. The `Coordinator` remains the thing that authorises, serialises memory, isolates
failures and orders results; ADK supplies only the graph topology.

What the evaluation found, and what this module therefore does:

**ADK cannot own dispatch, but it can own topology - provided each node hands its work to a thread.**
A node body that blocks serialises the event loop, so `("START", (a, b), join)` runs SEQUENTIALLY:
measured 0.63s wall with a concurrency of 1, against 0.37s and 2 when the same work goes through
`asyncio.to_thread`. The graph would promise parallelism the implementation did not deliver, which is
the worst kind of orchestration bug - invisible in the happy path. So every node here offloads to a
thread and lets the existing `fan_out` do the work.

**ADK is not the right owner for the safety properties**, and this module does not pretend otherwise:

| Property | Owner | Why ADK is not asked to own it |
|---|---|---|
| Authorisation order | `Coordinator.fan_out` | Authorisation must happen before a worker is resolved. That is a single call site with one guarantee; a graph would spread it across nodes. |
| Memory serialisation | `MemoryLane` | The client is not thread-safe, so serialisation is a RESOURCE property. ADK's `max_concurrency` is a graph-wide bound - it cannot express "these nodes serialise, those overlap". |
| Failure isolation | `Coordinator` | A worker's fault must not hide a sibling's result, and a refusal must stay a refusal. Both are already per-task. |
| Result ordering | `TaskCollector` | ADK's event stream is completion-ordered. Ordering has to be recovered from the INPUT order, not from the graph. |
| Trust boundary | The workers themselves | Unchanged. A worker still cannot reach memory or write a file. |

The ADK surface used is small and non-LLM: `google.adk.workflow.Node`, `JoinNode`, `START`, `Graph`,
`Workflow`, and `InMemoryRunner` for a session. No `LlmAgent`, no model, no tool registry - the workers
are not LLM-driven and must not be made to look like it.

ADK is an OPTIONAL dependency. The import is guarded, so the rest of the suite runs unchanged whether or
not `google-adk` is installed, and `adk_available()` reports which.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any, Sequence

from debugagent.agents.coordinator import Coordinator
from debugagent.agents.tasks import SubAgentResult, TaskSpec

#: Kept out of module scope on purpose: importing `google.adk` at import time would make an optional
#: evaluation dependency a hard requirement of the whole package.
try:  # pragma: no cover - depends on the environment
    from google.adk.agents import BaseAgent as _AdkAgent  # noqa: F401  (proves the package is present)
    from google.adk.events import Event as _AdkEvent
    from google.adk.runners import InMemoryRunner as _InMemoryRunner
    from google.adk.workflow import START as _ADK_START
    from google.adk.workflow import JoinNode as _JoinNode
    from google.adk.workflow import Node as _WorkflowNode
    from google.adk.workflow._graph import Graph as _Graph
    from google.adk import Workflow as _AdkWorkflow
    from google.genai import types as _genai_types

    ADK_IMPORT_ERROR: str | None = None
except Exception as exc:  # noqa: BLE001 - any import failure means "not available", not a crash
    _AdkEvent = _InMemoryRunner = _ADK_START = _JoinNode = _WorkflowNode = None
    _Graph = _AdkWorkflow = _genai_types = None
    ADK_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


def adk_available() -> bool:
    """Whether ADK can be used here. The suite must not depend on the answer."""
    return ADK_IMPORT_ERROR is None


def require_adk() -> None:
    """Raise a clear error rather than an `AttributeError` on `None` three frames later."""
    if not adk_available():
        raise RuntimeError(
            f"Google ADK is not importable in this environment ({ADK_IMPORT_ERROR}). It is an "
            f"optional evaluation dependency, not a project requirement. Install with "
            f"`pip install google-adk` to run the ADK evaluation.")


@dataclass
class CollectedResult:
    """One fan-out's results, tagged with the node that produced them."""

    node: str
    results: tuple[SubAgentResult, ...]
    joined: bool


@dataclass
class TaskCollector:
    """Recovers the Coordinator's deterministic ordering from an unordered completion stream.

    ADK yields events as nodes FINISH, so event order is completion order and is not stable. This
    collector keys every task by `task_id` and re-emits them in the order the caller declared the
    specs, which is the ordering guarantee `FanOutResult` already provides. Without it, ADK would
    quietly weaken a property the Coordinator guarantees.

    Refusals are recorded alongside results, not dropped. `FanOutResult.results` deliberately excludes
    a refused task - it produced no result - so a node that recorded only `.results` would DISCARD the
    refusal and report a clean run that silently skipped a task. Building this found that; `record()`
    therefore takes the whole `FanOutResult` and keeps refusals as first-class entries.
    """

    order: list[str] = field(default_factory=list)
    _results: dict[str, SubAgentResult] = field(default_factory=dict, repr=False)
    _refusals: dict[str, BaseException] = field(default_factory=dict, repr=False)
    _joined: dict[str, bool] = field(default_factory=dict, repr=False)
    _nodes: dict[str, str] = field(default_factory=dict, repr=False)
    _guard: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def declare(self, specs: Sequence[TaskSpec]) -> None:
        """Record the INPUT order before anything runs, so ordering cannot depend on timing."""
        with self._guard:
            for spec in specs:
                if spec.task_id not in self.order:
                    self.order.append(spec.task_id)

    def record(self, node: str, specs: Sequence[TaskSpec], fan: Any) -> None:
        """Record a completed fan-out, keeping refusals rather than discarding them."""
        with self._guard:
            for spec in specs:
                self._nodes[spec.task_id] = node
            for result in fan.results:
                self._results[result.task_id] = result
            for outcome in fan.refusals:
                self._refusals[outcome.task_id] = outcome.error
            self._joined[node] = fan.joined

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(self.order)

    def result_for(self, task_id: str) -> SubAgentResult | None:
        return self._results.get(task_id)

    def refusal_for(self, task_id: str) -> BaseException | None:
        return self._refusals.get(task_id)

    def node_for(self, task_id: str) -> str | None:
        return self._nodes.get(task_id)

    def ordered_results(self) -> tuple[SubAgentResult, ...]:
        """Results in declared input order. A task with neither a result nor a refusal is a bug."""
        self._assert_complete()
        return tuple(self._results[task_id] for task_id in self.order if task_id in self._results)

    def ordered_refusals(self) -> tuple[tuple[str, BaseException], ...]:
        """Refused tasks in declared order, so a refusal is as visible as a result."""
        return tuple((task_id, self._refusals[task_id]) for task_id in self.order
                     if task_id in self._refusals)

    def raise_for_refusals(self) -> None:
        for _task_id, error in self.ordered_refusals():
            raise error

    def _assert_complete(self) -> None:
        missing = [task_id for task_id in self.order
                   if task_id not in self._results and task_id not in self._refusals]
        if missing:
            raise AssertionError(f"no outcome was recorded for {missing}")

    def all_joined(self) -> bool:
        return all(self._joined.values()) and bool(self._joined)

    def summary(self) -> dict:
        self._assert_complete()
        by_status: dict[str, int] = {}
        for task_id in self.order:
            if task_id in self._refusals:
                by_status["refused"] = by_status.get("refused", 0) + 1
            else:
                status = self._results[task_id].status
                by_status[status] = by_status.get(status, 0) + 1
        return {"total": len(self.order), "by_status": by_status, "order": list(self.order),
                "joined": self.all_joined(),
                "nodes": {task_id: self._nodes.get(task_id) for task_id in self.order}}


class _WorkerNode(_WorkflowNode if adk_available() else object):  # type: ignore[misc]
    """One ADK workflow node that dispatches a fixed set of tasks through the existing Coordinator.

    The node owns NO authority. It holds a list of specs and a Coordinator, calls
    `coordinator.fan_out()` on a worker thread, and records what came back. Authorisation, the memory
    lane, failure classification and refusal handling all still happen inside `fan_out`, so they are
    the same code paths the non-ADK flow uses - which is what makes this an integration test of the
    existing architecture rather than a second implementation of it.
    """

    def __init__(self, *, name: str, coordinator: Coordinator, specs: Sequence[TaskSpec],
                 collector: TaskCollector, timeout: float | None):
        super().__init__(name=name, description=f"dispatch {name} tasks through the Coordinator")
        self._coordinator = coordinator
        self._specs = tuple(specs)
        self._collector = collector
        self._timeout = timeout
        # Declared at construction, so ordering is fixed before any node runs.
        collector.declare(self._specs)

    @property
    def specs(self) -> tuple[TaskSpec, ...]:
        return self._specs

    async def run(self, *, ctx: Any, node_input: Any) -> Any:
        # `asyncio.to_thread` is REQUIRED, not an optimisation. A blocking `fan_out` called directly
        # here would hold the event loop, and ADK's parallel nodes would then run one after another -
        # the graph would promise concurrency that never happened. Measured: 0.63s and a concurrency of
        # 1 without this, against 0.37s and 2 with it.
        fan = await asyncio.to_thread(
            lambda: self._coordinator.fan_out(self._specs, timeout=self._timeout))
        # The WHOLE fan-out result, not `.results`: a refused task has no result, and recording only
        # results would drop the refusal and report a clean run that skipped a task.
        self._collector.record(self.name, self._specs, fan)
        yield _AdkEvent(
            author=self.name,
            content=_genai_types.Content(
                role="model",
                parts=[_genai_types.Part(text=f"{len(fan)} task(s) dispatched via the Coordinator")],
            ),
        )


def build_worker_node(*, name: str, coordinator: Coordinator, specs: Sequence[TaskSpec],
                      collector: TaskCollector, timeout: float | None = None) -> Any:
    require_adk()
    return _WorkerNode(name=name, coordinator=coordinator, specs=specs, collector=collector,
                      timeout=timeout)


def build_investigation_workflow(*, coordinator: Coordinator,
                                 groups: dict[str, Sequence[TaskSpec]],
                                 collector: TaskCollector | None = None,
                                 timeout: float | None = None,
                                 name: str = "debugagent_p6_workflow",
                                 description: str = "fan out the P6 workers through the Coordinator",
                                 app_name: str = "debugagent") -> tuple[Any, TaskCollector, Any]:
    """Build the ADK graph: `START -> (each worker's node, in parallel) -> join`.

    Returns `(workflow, collector, runner_factory_state)`. The topology is ADK's; the work inside each
    node is the Coordinator's. `groups` is an ordered mapping of node name -> specs, and it defines the
    declared result order, which the collector preserves regardless of which node finishes first.

    A group with no specs is dropped rather than given an empty node: an empty node would satisfy the
    join barrier immediately and make the graph claim work that was never dispatched.

    Names must be valid Python identifiers - ADK validates this on every node, including the workflow
    itself. That happens to suit this repository, because the roster's agent ids (`memory_specialist`,
    `code_log_verifier`, `patch_generator`) are already identifiers, but it would reject a display name
    like "patch-generator", so group keys are identifiers by necessity rather than by taste.
    """
    require_adk()
    populated = {name_: tuple(specs) for name_, specs in groups.items() if specs}
    if not populated:
        raise ValueError("build_investigation_workflow needs at least one non-empty task group")
    for name_ in populated:
        if not name_.isidentifier():
            raise ValueError(
                f"ADK requires node names to be Python identifiers, so {name_!r} cannot be used as a "
                f"group name. Use the registered agent id (e.g. 'code_log_verifier').")

    collected = collector if collector is not None else TaskCollector()
    nodes = [build_worker_node(name=name_, coordinator=coordinator, specs=specs,
                              collector=collected, timeout=timeout)
             for name_, specs in populated.items()]
    join = _JoinNode(name="join")
    graph = _Graph.from_edge_items([(_ADK_START, tuple(nodes), join)])
    workflow = _AdkWorkflow(name=name, description=description, graph=graph)
    runner = _InMemoryRunner(agent=workflow, app_name=app_name)
    return workflow, collected, runner


def run_investigation_workflow(runner: Any, *, user_id: str = "engineer",
                               session_id: str | None = None, app_name: str = "debugagent",
                               message: str = "investigate") -> list[Any]:
    """Run the built workflow once and return ADK's events, in ADK's own (completion) order.

    Only the runner is needed - it already holds the workflow. Exposed so a caller can see the raw
    event stream; the deterministic view is the `TaskCollector`.
    """
    require_adk()

    async def _run() -> list[Any]:
        service = runner.session_service
        session = session_id or (await service.create_session(
            app_name=app_name, user_id=user_id, state={})).id
        events: list[Any] = []
        async for event in runner.run_async(
                user_id=user_id, session_id=session,
                new_message=_genai_types.Content(role="user",
                                                 parts=[_genai_types.Part(text=message)])):
            events.append(event)
        return events

    return asyncio.run(_run())


def run_p6_fan_out(coordinator: Coordinator, groups: dict[str, Sequence[TaskSpec]], *,
                   timeout: float | None = None) -> TaskCollector:
    """The end-to-end ADK path: build the graph, run it, and return deterministically ordered results.

    One call, and every safety property still comes from the Coordinator: authorisation ran before
    dispatch, memory work serialised through the shared lane, one worker's failure did not hide a
    sibling's result, refusals stayed refusals, and the result order is the declared one.
    """
    _workflow, collector, runner = build_investigation_workflow(
        coordinator=coordinator, groups=groups, timeout=timeout)
    run_investigation_workflow(runner=runner)
    return collector
