"""P5: client access is a structural capability, not a worker convention.

`hindsight_client` is not thread-safe, so every task that can reach it must be serialised through the
one shared `MemoryLane`. Until now the decision was read off the WORKER OBJECT (`client_access`), which
was a convention: a worker that reached the client but declared nothing - or declared the opposite -
would run unsynchronised, and nothing in authorisation would notice. The failure would surface much
later as an intermittent aiohttp `RuntimeError` in production.

The capability now lives on the registered `AgentDefinition`, where authorisation already looks, and
`Coordinator.touches_client` derives from that plus the task's own tools. A worker cannot opt out.

What is proved here:

1. A registered client-access agent's tasks ALWAYS take the lane.
2. A registered non-client agent's tasks never do.
3. A task cannot buy unsynchronised client access by omitting memory tools from its tool list.
4. Authorisation still happens before dispatch, and a refused task still never reaches a worker.
5. Memory stays serial and non-memory stays parallel, unchanged.
6. The convention is gone: no worker class declares a capability, and the roster is internally
   consistent (any agent granted a memory tool is client-access).

Determinism: no sleeps; concurrency is shown with barriers and by counting client entries.
"""

from __future__ import annotations

import inspect
import tempfile
import threading
import unittest
from pathlib import Path

import support
from debugagent.agents.coordinator import MEMORY_TOOLS, Coordinator
from debugagent.agents.memory_specialist import MemorySpecialist, build_memory_specialist_task
from debugagent.agents.registry import (
    AGENT_DEFINITION_FIELDS,
    WORKER_IDS,
    WORKER_ROSTER,
    AuthorizationError,
    AgentDefinition,
    authorize,
    get_agent,
)
from debugagent.agents.tasks import SubAgentResult, TaskSpec
from debugagent.memory.hindsight_store import HindsightMemoryStore, compute_case_key
from debugagent.pipeline.memory_adapter import HindsightMemoryPort
from support import FakeHindsightClient, FakeMemory, FakeScores, memory_config

TIMEOUT = 5.0
GATE_TIMEOUT = 1.0
MEMORY = "memory_specialist"
VERIFIER = "code_log_verifier"
PATCHER = "patch_generator"
# Added by VLSI-1C. Non-client for the same reason the other two are: reading a file and reporting what
# it says must never queue behind the bank, and a deterministic analysis has no reason to reach it.
SDC_ANALYZER = "sdc_analyzer"


def memory_spec(task_id: str) -> TaskSpec:
    return build_memory_specialist_task(task_id=task_id, case_signature="uploads fail over 2 MB")


def compute_spec(task_id: str, agent: str = VERIFIER, tools: tuple[str, ...] = ("read",)) -> TaskSpec:
    return TaskSpec(task_id=task_id, agent=agent, delegated_by="coordinator",
                    objective="inspect the current repository", context={"case_signature": "x"},
                    allowed_tools=tools, model="primary", depth=1)


def seed_row(seed):
    return FakeMemory(
        text=f"past case: {seed.problem_signature}",
        metadata={"case_key": compute_case_key(seed, seed.session_id or "seed"),
                  "outcome": seed.outcome,
                  "service": seed.environment.get("service", "unknown"),
                  "runtime": seed.environment.get("runtime", "unknown"),
                  "root_cause_key": (seed.root_cause or "")[:64]},
        scores=FakeScores(0.9, 0.8, 0.7), mentioned_at="2026-01-01T00:00:00+00:00")


def seeded_rows():
    from debugagent.seeds.loader import load_seed_file

    return [seed_row(seed) for seed in load_seed_file()]


class ClientEntryCounter(FakeHindsightClient):
    """Counts threads inside any client call. Provisioning does not rendezvous: a barrier consumed by a
    lone caller during store construction is left broken, disabling the rendezvous that matters."""

    def __init__(self, *, rendezvous: bool = False):
        super().__init__(recall_results=seeded_rows())
        self.rendezvous = rendezvous
        self.inside = 0
        self.max_inside = 0
        self.calls = 0
        self._guard = threading.Lock()
        self._barrier: threading.Barrier | None = None

    def _around(self, function, rendezvous: bool, *args, **kwargs):
        with self._guard:
            self.inside += 1
            self.calls += 1
            self.max_inside = max(self.max_inside, self.inside)
        if self.rendezvous and rendezvous:
            try:
                self._barrier_for_two().wait(timeout=GATE_TIMEOUT)
            except threading.BrokenBarrierError:
                pass
        try:
            return function(*args, **kwargs)
        finally:
            with self._guard:
                self.inside -= 1

    def _barrier_for_two(self) -> threading.Barrier:
        with self._guard:
            if self._barrier is None:
                self._barrier = threading.Barrier(2)
            return self._barrier

    def recall(self, **kwargs):
        return self._around(super().recall, True, **kwargs)

    def retain(self, **kwargs):
        return self._around(super().retain, True, **kwargs)

    def get_bank_config(self, bank_id):
        return self._around(super().get_bank_config, False, bank_id)

    def create_bank(self, bank_id, **kwargs):
        return self._around(super().create_bank, False, bank_id, **kwargs)


class ComputeWorker:
    """Stands in for the P6 verifier, which is registered but deliberately not implemented."""

    def __init__(self, *, gate: threading.Barrier | None = None):
        self.gate = gate
        self.seen: list[str] = []
        self._running = 0
        self.max_running = 0
        self._guard = threading.Lock()

    def execute(self, spec: TaskSpec) -> SubAgentResult:
        with self._guard:
            self.seen.append(spec.task_id)
            self._running += 1
            self.max_running = max(self.max_running, self._running)
        try:
            if self.gate is not None:
                self.gate.wait(timeout=GATE_TIMEOUT)
            return SubAgentResult(task_id=spec.task_id, agent=spec.agent, status="success")
        finally:
            with self._guard:
                self._running -= 1


class CapabilityBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def port(self, client) -> HindsightMemoryPort:
        store = HindsightMemoryStore(
            memory_config(self.tmp_path, bank_id="cap", ledger_path=self.tmp_path / "l.json"),
            client=client)
        self.addCleanup(store.close)
        return HindsightMemoryPort(store)


class RosterCapabilityTests(CapabilityBase):
    """The capability is in the registry, and the registry is internally consistent."""

    def test_the_capability_is_a_declared_payload_field(self):
        self.assertIn("client_access", AGENT_DEFINITION_FIELDS)
        self.assertEqual(len(AGENT_DEFINITION_FIELDS), 5)

    def test_only_the_memory_specialist_is_client_access(self):
        expected = {MEMORY: True, VERIFIER: False, PATCHER: False, SDC_ANALYZER: False}
        self.assertEqual({name: get_agent(name).client_access for name in WORKER_IDS}, expected)

    def test_an_agent_granted_a_memory_tool_must_be_client_access(self):
        """The invariant that stops a future edit from quietly unsynchronising memory access.

        Adding `hindsight_recall` to the verifier's tools without setting `client_access` would let it
        reach the bank outside the lane. This fails closed on that mistake.
        """
        for name, definition in WORKER_ROSTER.items():
            with self.subTest(agent=name):
                granted = bool(MEMORY_TOOLS & set(definition.allowed_tools))
                if granted:
                    self.assertTrue(definition.client_access,
                                    f"{name} is granted a memory tool, so it must be client_access")

    def test_the_capability_round_trips_through_from_dict(self):
        definition = get_agent(MEMORY)
        restored = AgentDefinition.from_dict(definition.to_dict(), "test")
        self.assertEqual(restored.client_access, definition.client_access)
        self.assertEqual(restored.to_dict(), definition.to_dict())

    def test_a_missing_capability_is_rejected_rather_than_defaulted(self):
        """Both defaults are dangerous, so the field is required.

        Defaulting True would silently serialise a compute agent; defaulting False would let a
        client-touching agent escape the lane. Requiring it means the roster author must decide.
        """
        payload = dict(get_agent(MEMORY).to_dict())
        del payload["client_access"]
        with self.assertRaises(AuthorizationError) as caught:
            AgentDefinition.from_dict(payload, "test")
        self.assertIn("client_access", str(caught.exception))

    def test_a_non_boolean_capability_is_rejected(self):
        payload = dict(get_agent(MEMORY).to_dict())
        payload["client_access"] = "yes"
        with self.assertRaises(AuthorizationError):
            AgentDefinition.from_dict(payload, "test")


class ConventionRemovedTests(unittest.TestCase):
    """The worker-side convention is gone, and cannot come back quietly."""

    @staticmethod
    def _executable_source(owner) -> str:
        """The owner`'s body with its docstring STATEMENT removed.

        The docstrings here deliberately DISCUSS `client_access` in order to say it is absent, so a
        substring scan over raw source would fail on the very sentence promising the convention is
        gone. Dropping the statement - not merely the string node - is what makes the scan meaningful:
        filtering `ast.walk` alone changes nothing, because the enclosing `Expr` still dumps its child.
        """
        import ast
        import inspect as _inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(_inspect.getsource(owner)))
        target = next(node for node in ast.walk(tree)
                      if isinstance(node, (ast.ClassDef, ast.FunctionDef)))
        body = target.body
        if body and isinstance(body[0], ast.Expr):
            body = body[1:]
        return " ".join(ast.dump(statement) for statement in body)

    def test_the_worker_protocol_declares_no_capability(self):
        from debugagent.agents.coordinator import Worker

        self.assertFalse(hasattr(Worker, "client_access"))
        self.assertNotIn("client_access", self._executable_source(Worker),
                         "the Worker protocol must not offer a capability attribute")

    def test_the_memory_worker_declares_no_capability(self):
        self.assertFalse(hasattr(MemorySpecialist, "client_access"),
                         "the worker must not carry client_access; it lives on the roster")
        self.assertNotIn("client_access", self._executable_source(MemorySpecialist))

    def test_touches_client_never_reads_the_worker_object(self):
        from debugagent.agents.coordinator import Coordinator

        source = self._executable_source(Coordinator.touches_client)
        self.assertIn("client_access", source, "the roster must be the authority")
        self.assertNotIn("getattr", source,
                         "touches_client must not consult the worker object for a capability")
        self.assertNotIn("worker_for", source,
                         "touches_client must not resolve the worker to ask it anything")

    def test_a_worker_lying_about_its_capability_is_ignored(self):
        class Liar:
            client_access = False

            def execute(self, spec: TaskSpec) -> SubAgentResult:
                return SubAgentResult(task_id=spec.task_id, agent=spec.agent, status="success")

        coordinator = Coordinator({MEMORY: Liar()})
        self.assertTrue(coordinator.touches_client(memory_spec("t-0")))


class LaneEnforcementTests(CapabilityBase):
    """1, 2 and 3: the capability decides the lane, and tools cannot override it."""

    def test_a_client_access_agent_always_enters_the_lane(self):
        coordinator = Coordinator({MEMORY: MemorySpecialist(self.port(ClientEntryCounter()))})
        before = coordinator.lane.entered
        coordinator.fan_out([memory_spec("t-0"), memory_spec("t-1")])
        self.assertEqual(coordinator.lane.entered - before, 2,
                         "one lane acquisition per client-access task")

    def test_a_non_client_agent_never_enters_the_lane(self):
        for agent in (VERIFIER, PATCHER):
            with self.subTest(agent=agent):
                tools = get_agent(agent).allowed_tools[:1]
                coordinator = Coordinator({agent: ComputeWorker()})
                before = coordinator.lane.entered
                coordinator.fan_out([compute_spec(f"{agent}-0", agent=agent, tools=tools),
                                     compute_spec(f"{agent}-1", agent=agent, tools=tools)])
                self.assertEqual(coordinator.lane.entered, before,
                                 f"{agent} is not client-access and must not take the lane")

    def test_omitting_memory_tools_does_not_buy_unserialised_access(self):
        """A client-access agent with no memory tool in its list is refused outright.

        Stronger than "it gets serialised": the task never runs. `touches_client` above already shows
        such a task WOULD be serialised if it ran, so the lane and the authorisation each independently
        prevent the bypass.
        """
        coordinator = Coordinator({MEMORY: MemorySpecialist(self.port(ClientEntryCounter()))})
        for tools in (("read",), ("read", "grep", "glob"), ("hindsight_get_facts",)):
            with self.subTest(tools=tools):
                spec = TaskSpec(task_id="bare", agent=MEMORY, delegated_by="coordinator",
                                objective="recall", context={"case_signature": "x"},
                                allowed_tools=tools, model="primary", depth=1)
                self.assertTrue(coordinator.touches_client(spec))
                if not MEMORY_TOOLS & set(tools):
                    with self.assertRaises(AuthorizationError):
                        authorize(spec)
        # An EMPTY tool list is authorised (it requests nothing, including no memory tool), which is
        # exactly the case the capability has to cover: it would be serialised, never left free.
        empty = TaskSpec(task_id="empty", agent=MEMORY, delegated_by="coordinator",
                         objective="recall", context={"case_signature": "x"}, allowed_tools=(),
                         model="primary", depth=1)
        self.assertTrue(coordinator.touches_client(empty))

    def test_a_memory_tool_still_forces_the_lane_even_if_the_capability_disagreed(self):
        """Defence in depth: the second net, for a roster edited carelessly.

        `authorize()` already refuses a memory tool to an agent not granted it, so this cannot be
        reached through the normal path. The rule is asserted directly because it is the net, and a net
        nobody tests is not a net.
        """
        coordinator = Coordinator({VERIFIER: ComputeWorker()})
        spec = TaskSpec(task_id="x", agent=VERIFIER, delegated_by="coordinator", objective="recall",
                        context={"case_signature": "x"}, allowed_tools=("hindsight_recall",),
                        model="primary", depth=1)
        self.assertTrue(coordinator.touches_client(spec),
                        "a task naming a memory tool must be serialised whatever the capability says")

    def test_a_non_client_agent_cannot_request_a_memory_tool_at_all(self):
        """The stronger guarantee: the bypass is not merely serialised, it is refused."""
        spec = TaskSpec(task_id="x", agent=VERIFIER, delegated_by="coordinator", objective="recall",
                        context={"case_signature": "x"}, allowed_tools=("hindsight_recall",),
                        model="primary", depth=1)
        with self.assertRaises(AuthorizationError):
            authorize(spec)


class AuthorisationFirstTests(CapabilityBase):
    """4. Authorisation still happens before dispatch."""

    def test_a_refused_task_never_reaches_a_worker(self):
        worker = ComputeWorker()
        coordinator = Coordinator({MEMORY: worker})
        bad = TaskSpec(task_id="bad", agent=MEMORY, delegated_by="coordinator", objective="recall",
                       context={"case_signature": "x"}, allowed_tools=("task",), model="primary",
                       depth=1)
        fan = coordinator.fan_out([memory_spec("t-0"), bad, memory_spec("t-1")])
        self.assertTrue(fan.outcomes[1].refused)
        self.assertNotIn("bad", worker.seen,
                         "the refused task must not reach worker code, in a fan-out either")
        self.assertEqual(sorted(worker.seen), ["t-0", "t-1"],
                         "its authorised siblings still ran")

    def test_single_dispatch_still_authorises_before_executing(self):
        worker = ComputeWorker()
        coordinator = Coordinator({MEMORY: worker})
        bad = TaskSpec(task_id="bad", agent=MEMORY, delegated_by="coordinator", objective="recall",
                       context={"case_signature": "x"}, allowed_tools=("retain_case",),
                       model="primary", depth=1)
        with self.assertRaises(AuthorizationError):
            coordinator.delegate(bad)
        self.assertEqual(worker.seen, [])

    def test_an_unregistered_agent_is_refused(self):
        coordinator = Coordinator({MEMORY: ComputeWorker()})
        invented = TaskSpec(task_id="x", agent="omnipotent", delegated_by="coordinator",
                            objective="do everything", context={"case_signature": "x"},
                            allowed_tools=("read",), model="primary", depth=1)
        fan = coordinator.fan_out([memory_spec("t-0"), invented])
        self.assertTrue(fan.outcomes[1].refused)

    def test_the_capability_grants_no_authority(self):
        """Being client-access is not permission to do anything extra."""
        coordinator = Coordinator({MEMORY: MemorySpecialist(self.port(ClientEntryCounter()))})
        result = coordinator.delegate(build_memory_specialist_task(
            task_id="t", case_signature="x", objective="retain this resolved case"))
        self.assertEqual(result.status, "failed")
        self.assertIn("engineer decision", result.failure_detail)


class ParallelSerialBehaviourTests(CapabilityBase):
    """5. Memory stays serial; non-memory stays parallel."""

    def test_memory_tasks_enter_the_client_one_at_a_time(self):
        client = ClientEntryCounter(rendezvous=True)
        port = self.port(client)
        coordinator = Coordinator({MEMORY: MemorySpecialist(port)})
        fan = coordinator.fan_out([memory_spec(f"t-{i}") for i in range(4)], timeout=2.0)
        self.assertTrue(all(o.ok for o in fan), f"{fan.summary()}")
        self.assertGreaterEqual(client.calls, 4)
        self.assertEqual(client.max_inside, 1,
                         f"the client was entered concurrently (calls={client.calls})")

    def test_non_memory_tasks_run_concurrently(self):
        worker = ComputeWorker(gate=threading.Barrier(3))
        coordinator = Coordinator({VERIFIER: worker})
        fan = coordinator.fan_out([compute_spec("a"), compute_spec("b"), compute_spec("c")],
                                  timeout=2.0)
        self.assertTrue(fan.joined)
        self.assertEqual(worker.max_running, 3,
                         "the three tasks must have been inside the worker at the same time")

    def test_one_coordinator_serves_both_classes(self):
        """The capability lets a single Coordinator route both, with no worker-side routing."""
        client = ClientEntryCounter(rendezvous=True)
        port = self.port(client)
        compute = ComputeWorker()
        coordinator = Coordinator({MEMORY: MemorySpecialist(port), VERIFIER: compute})
        fan = coordinator.fan_out([memory_spec("mem-0"), compute_spec("c-0"),
                                   memory_spec("mem-1"), compute_spec("c-1")], timeout=2.0)
        self.assertTrue(all(o.ok for o in fan), f"{fan.summary()}")
        self.assertEqual(client.max_inside, 1, "memory work must still be serial")
        self.assertEqual(sorted(compute.seen), ["c-0", "c-1"])
        self.assertEqual([o.task_id for o in fan.outcomes], ["mem-0", "c-0", "mem-1", "c-1"])


if __name__ == "__main__":
    unittest.main()
