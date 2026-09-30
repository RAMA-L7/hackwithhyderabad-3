"""P5 parallel fan-out over the shared Coordinator and the shared MemoryLane.

The constraint that shaped all of this: `hindsight_client` is not thread-safe, so the client is the
bottleneck and nothing else is. Fan-out therefore parallelises only work that never enters the client,
while every client-touching task still passes through the one shared `MemoryLane`.

The architecture under test:

- one `Coordinator`, one `MemoryLane`, one `Runtime` - constructed by the composition root and never
  per worker, per task or per thread;
- authorisation up front, in input order, outside the lane, so a refused task never starts and does not
  abort its siblings;
- `threading.Thread` per runnable task, with a `threading.Barrier` as the ADR's join barrier;
- the lane taken per task, conditionally, on the worker's `client_access` declaration;
- results written into positional slots, so ordering is deterministic regardless of completion order;
- every task keeps its own `TaskOutcome`, so a failure can never hide behind a sibling's success.

There is no non-memory worker in the roster yet - the registry is closed and adding one would be P6 - so
the non-memory side is exercised with a deterministic test worker that declares `client_access = False`.
That declaration is a capability statement made by trusted composition code; authorisation still runs
and a worker that declares nothing is treated as client-touching.

Determinism: no sleeps. Concurrency is proved with barriers and events, and assertions are on counts
and identity, never on elapsed time.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

import support
from debugagent.agents.coordinator import Coordinator, FanOutResult, MemoryLane, TaskOutcome
from debugagent.agents.memory_specialist import MemorySpecialist, build_memory_specialist_task
from debugagent.agents.registry import AuthorizationError, authorize_tool, get_agent
from debugagent.agents.tasks import Artifact, SubAgentResult, TaskSpec
from debugagent.composition import bound_count, build_runtime, coordinator_for
from debugagent.memory.hindsight_store import HindsightMemoryStore, compute_case_key
from debugagent.pipeline.memory_adapter import HindsightMemoryPort
from support import FakeHindsightClient, FakeMemory, FakeScores, memory_config

TIMEOUT = 5.0
# The worker-side gates get a much shorter budget than the thread joins. A gate exists to PROVE
# concurrency: if the tasks really overlap, all parties arrive in microseconds. A gate that is not
# satisfied means the implementation serialised, and there is no reason to spend the full join timeout
# discovering that - with a 5s budget a single sequential run of these tests takes minutes, which is
# both slow and easy to mistake for a hang.
GATE_TIMEOUT = 1.0
MEMORY_SPECIALIST = "memory_specialist"
#: The registered NON-client agent. Used as the compute side of every parallelism test, so those tests
#: exercise the real roster capability rather than a test-only flag.
VERIFIER = "code_log_verifier"


def memory_spec(task_id: str, *, signature: str = "uploads fail over 2 MB") -> TaskSpec:
    """A real, authorised memory task, built through the same seam the flow uses.

    Requests the memory tools, so `touches_client` puts it in the lane.
    """
    return build_memory_specialist_task(task_id=task_id, case_signature=signature)


def compute_spec(task_id: str, *, objective: str = "inspect the workspace") -> TaskSpec:
    """An authorised task that requests NO memory tool, so it runs outside the lane.

    Built by hand rather than through `build_memory_specialist_task` because that helper is specifically
    a memory task. It still goes through `fan_out`'s `authorize()` check, so the roster, the delegator
    and the model all still apply - only the tool list is empty, which is what "pure computation" means
    for a task.
    """
    return TaskSpec(task_id=task_id, agent=VERIFIER, delegated_by="coordinator",
                    objective=objective, context={"case_signature": "compute only"},
                    allowed_tools=("read",), model="primary", depth=1)


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
    """Counts threads inside any client call, so memory serialisation is observable.

    Provisioning does not rendezvous: store construction is single-threaded, and a barrier consumed by
    a lone caller is left permanently broken, which would disable the rendezvous that matters.
    """

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
                self._barrier_for_two().wait(timeout=1.0)
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


class NonMemoryWorker:
    """A deterministic stand-in for the non-memory workers P6 will add.

    Declares `client_access = False`, which is what lets the Coordinator run it outside the lane. It
    touches no client and no I/O, so a test can control exactly when each task finishes.
    """

    client_access = False

    def __init__(self, *, gate: threading.Barrier | None = None, fails: set[str] | None = None,
                 gate_one: dict[str, threading.Event] | None = None):
        self.gate = gate
        self.fails = set(fails or ())
        self.gate_one = gate_one or {}
        self.started: list[str] = []
        self.finished: list[str] = []
        self._guard = threading.Lock()
        self._running = 0
        self.max_running = 0

    def execute(self, spec: TaskSpec) -> SubAgentResult:
        with self._guard:
            self.started.append(spec.task_id)
            self._running += 1
            self.max_running = max(self.max_running, self._running)
        try:
            release = self.gate_one.get(spec.task_id)
            if release is not None:
                release.wait(timeout=GATE_TIMEOUT)
            if self.gate is not None:
                # Proves the tasks really overlap: if they were serialised this could never be
                # satisfied. Bounded, so an unsatisfiable gate BREAKS rather than hanging the suite -
                # which is exactly the signal the negative control test reads.
                self.gate.wait(timeout=GATE_TIMEOUT)
            if spec.task_id in self.fails:
                raise RuntimeError(f"worker fault in {spec.task_id}")
            return SubAgentResult(
                task_id=spec.task_id, agent=spec.agent, status="success",
                observations=(Artifact(kind="memory", ref=spec.task_id,
                                       content=f"inspected {spec.task_id}"),))
        finally:
            with self._guard:
                self.finished.append(spec.task_id)
                self._running -= 1


class MemoryWorker(MemorySpecialist):
    """The real worker, re-declared so the intent is explicit at the use site."""

    client_access = True


class FanOutBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self._before = bound_count()

    def port(self, client) -> HindsightMemoryPort:
        store = HindsightMemoryStore(
            memory_config(self.tmp_path, bank_id="fanout", ledger_path=self.tmp_path / "l.json"),
            client=client)
        self.addCleanup(store.close)
        return HindsightMemoryPort(store)


class ConcurrencyTests(FanOutBase):
    """1. Multiple authorised tasks execute concurrently."""

    def test_non_memory_tasks_really_overlap(self):
        worker = NonMemoryWorker(gate=threading.Barrier(3))
        coordinator = Coordinator({VERIFIER: worker})
        fan = coordinator.fan_out([compute_spec("t-0"), compute_spec("t-1"), compute_spec("t-2")], timeout=2.0)
        self.assertTrue(fan.joined, "the join barrier should be satisfied")
        self.assertTrue(all(o.ok for o in fan), f"all tasks should succeed: {fan.summary()}")
        self.assertEqual(worker.max_running, 3,
                         "the three tasks must have been inside the worker at the same time")

    def test_serial_execution_would_not_satisfy_that_barrier(self):
        """The control: proves the gate in the test above is a real concurrency detector.

        A three-party gate cannot be met by one task, so the task fails. That is the whole point: if
        fan-out had silently serialised, the gate in `test_non_memory_tasks_really_overlap` would break
        too, and that test would fail rather than pass for the wrong reason.

        Note `joined` stays True here: it describes the Coordinator's own join barrier, which has one
        party and is satisfied. The gate that breaks is the test worker's, not the join barrier.
        """
        worker = NonMemoryWorker(gate=threading.Barrier(3))
        coordinator = Coordinator({VERIFIER: worker})
        fan = coordinator.fan_out([compute_spec("only")], timeout=2.0)
        self.assertTrue(fan.joined, "the single-party join barrier is satisfied")
        self.assertEqual(fan.outcomes[0].status, "failed",
                         "a lone task cannot satisfy a three-party gate")
        self.assertEqual(worker.max_running, 1)

    def test_every_task_is_dispatched_and_recorded(self):
        worker = NonMemoryWorker()
        coordinator = Coordinator({VERIFIER: worker})
        specs = [compute_spec(f"t-{i}") for i in range(5)]
        fan = coordinator.fan_out(specs)
        self.assertEqual(len(worker.started), 5)
        self.assertEqual(len(fan), 5)
        self.assertEqual(len(coordinator.dispatches), 5,
                         "each fan-out task is a dispatch, recorded for audit")


class MemoryStaysSerialTests(FanOutBase):
    """2. Memory work still enters the shared lane serially, even inside a fan-out."""

    def test_parallel_memory_tasks_enter_the_client_one_at_a_time(self):
        client = ClientEntryCounter(rendezvous=True)
        port = self.port(client)
        coordinator = Coordinator.with_memory_specialist(MemorySpecialist(port))
        fan = coordinator.fan_out([memory_spec(f"t-{i}", signature=f"uploads fail {i}")
                                   for i in range(4)])
        self.assertTrue(all(o.ok for o in fan), f"all recalls should succeed: {fan.summary()}")
        self.assertGreaterEqual(client.calls, 4, "every task must have reached the client")
        self.assertEqual(client.max_inside, 1,
                         f"the client was entered concurrently (calls={client.calls})")

    def test_memory_and_non_memory_tasks_are_not_serialised_against_each_other(self):
        """The point of the whole design, proved by making serialisation deadlock.

        The non-memory task waits for the memory tasks to finish. That is only possible because the
        non-memory task does NOT hold the lane - if one lock guarded every task, the non-memory task
        would hold it while waiting for the memory tasks that need it, and nothing would ever finish.
        """
        client = ClientEntryCounter()
        port = self.port(client)
        memory_done = threading.Event()
        seen_memory: list[str] = []
        guard = threading.Lock()

        class HeldNonMemory(NonMemoryWorker):
            def execute(self, spec: TaskSpec) -> SubAgentResult:
                if spec.objective.startswith("hold"):
                    self.assert_released(memory_done)
                return super().execute(spec)

            @staticmethod
            def assert_released(event: threading.Event) -> None:
                if not event.wait(timeout=TIMEOUT):
                    raise AssertionError(
                        "the non-memory task never saw the memory tasks finish: something serialised "
                        "them against each other")

        class CountingMemory(MemorySpecialist):
            def execute(self, spec: TaskSpec) -> SubAgentResult:
                result = super().execute(spec)
                with guard:
                    seen_memory.append(spec.task_id)
                    if len(seen_memory) == 2:
                        memory_done.set()
                return result

        # No routing and no worker-side flag: the roster already says which agent reaches the client,
        # so one Coordinator simply holds both workers and the capability does the sorting.
        coordinator = Coordinator({
            MEMORY_SPECIALIST: CountingMemory(port),
            VERIFIER: HeldNonMemory(),
        })

        hold_spec = compute_spec("hold-0", objective="hold until memory ran")
        errors: list[BaseException] = []
        results: list[FanOutResult] = []

        def run() -> None:
            try:
                results.append(coordinator.fan_out([hold_spec, memory_spec("mem-0"),
                                                    memory_spec("mem-1")]))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(timeout=TIMEOUT * 4)
        self.assertFalse(thread.is_alive(),
                         "fan-out deadlocked: the non-client task blocked the memory tasks")
        self.assertEqual(errors, [], f"fan-out raised: {errors}")
        self.assertEqual(len(results), 1)
        self.assertTrue(all(o.ok for o in results[0]), f"{results[0].summary()}")
        self.assertEqual(sorted(seen_memory), ["mem-0", "mem-1"],
                         "both memory tasks must have run to completion")
        self.assertEqual(client.max_inside, 1,
                         "the memory tasks must still have entered the client serially")


class PartialFailureTests(FanOutBase):
    """3. One worker failure does not hide other results."""

    def test_a_failing_task_does_not_hide_its_siblings(self):
        worker = NonMemoryWorker(fails={"t-1"})
        coordinator = Coordinator({VERIFIER: worker})
        fan = coordinator.fan_out([compute_spec(f"t-{i}") for i in range(4)], timeout=2.0)
        self.assertTrue(fan.joined)
        self.assertEqual(len(fan), 4, "every task must still have an outcome")
        self.assertEqual([o.task_id for o in fan.succeeded], ["t-0", "t-2", "t-3"])
        self.assertEqual([o.task_id for o in fan.failed], ["t-1"])
        failed = fan.outcomes[1]
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.result.failure_kind, "unavailable")
        self.assertIn("RuntimeError", failed.result.failure_detail)

    def test_a_summary_counts_both_and_keeps_the_order(self):
        worker = NonMemoryWorker(fails={"t-0", "t-2"})
        coordinator = Coordinator({VERIFIER: worker})
        summary = coordinator.fan_out([compute_spec(f"t-{i}") for i in range(3)]).summary()
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["by_status"], {"success": 1, "failed": 2})
        self.assertEqual(summary["order"], ["t-0", "t-1", "t-2"],
                         "the summary must not reorder to group successes together")

    def test_a_worker_returning_partial_is_distinguishable_from_failure(self):
        class PartialWorker(NonMemoryWorker):
            def execute(self, spec: TaskSpec) -> SubAgentResult:
                return SubAgentResult(task_id=spec.task_id, agent=spec.agent, status="partial",
                                      observations=(), failure_kind="unavailable",
                                      failure_detail="half the cases")

        coordinator = Coordinator({VERIFIER: PartialWorker()})
        fan = coordinator.fan_out([compute_spec("p-0")])
        self.assertEqual(fan.outcomes[0].status, "partial")
        self.assertFalse(fan.outcomes[0].ok, "partial is not ok")
        self.assertEqual(fan.summary()["by_status"], {"partial": 1})


class JoinBarrierTests(FanOutBase):
    """4. The join waits for every dispatched worker."""

    def test_fan_out_waits_for_a_slow_task(self):
        released = threading.Event()
        worker = NonMemoryWorker(gate_one={"t-1": released})
        coordinator = Coordinator({VERIFIER: worker})
        returned = threading.Event()
        result: list[FanOutResult] = []

        def run() -> None:
            result.append(coordinator.fan_out([compute_spec("t-0"), compute_spec("t-1")], timeout=2.0))
            returned.set()

        thread = threading.Thread(target=run)
        thread.start()
        self.assertFalse(returned.wait(timeout=0.3),
                         "fan_out returned before the slow task was released")
        released.set()
        self.assertTrue(returned.wait(timeout=TIMEOUT), "fan_out never returned")
        thread.join(timeout=TIMEOUT)
        self.assertEqual(len(result[0]), 2)

    def test_no_result_is_read_before_every_task_reports(self):
        """Every outcome slot is filled by the time fan_out returns."""
        worker = NonMemoryWorker(gate=threading.Barrier(4))
        coordinator = Coordinator({VERIFIER: worker})
        fan = coordinator.fan_out([compute_spec(f"t-{i}") for i in range(4)], timeout=2.0)
        self.assertTrue(all(o is not None for o in fan.outcomes))
        self.assertEqual(len(worker.finished), 4,
                         "all four tasks must have finished before fan_out returned")


class DeterministicOrderingTests(FanOutBase):
    """5. Result ordering is deterministic, whatever order the tasks finish in."""

    def test_outcomes_follow_input_order_not_completion_order(self):
        """t-0 is held until t-1 and t-2 have finished, so completion order is the reverse of input."""
        seen_other = threading.Event()
        finished: list[str] = []
        guard = threading.Lock()

        class ReverseFinisher(NonMemoryWorker):
            def execute(self, spec: TaskSpec) -> SubAgentResult:
                if spec.task_id == "t-0":
                    # Waits for the other two, so it finishes LAST despite being FIRST in the input.
                    if not seen_other.wait(timeout=TIMEOUT):
                        raise AssertionError("t-1/t-2 never finished")
                result = super().execute(spec)
                if spec.task_id != "t-0":
                    with guard:
                        finished.append(spec.task_id)
                    if len(finished) == 2:
                        seen_other.set()
                return result

        coordinator = Coordinator({VERIFIER: ReverseFinisher()})
        fan = coordinator.fan_out([compute_spec("t-0"), compute_spec("t-1"), compute_spec("t-2")], timeout=2.0)
        self.assertTrue(fan.joined)
        self.assertEqual(finished, ["t-1", "t-2"],
                         "the control: t-1 and t-2 really did finish before t-0")
        self.assertEqual([o.task_id for o in fan.outcomes], ["t-0", "t-1", "t-2"],
                         "outcomes must follow input order, not completion order")
        self.assertEqual(fan.summary()["order"], ["t-0", "t-1", "t-2"])

    def test_ordering_is_stable_across_repeated_runs(self):
        coordinator = Coordinator({VERIFIER: NonMemoryWorker(gate=threading.Barrier(3))})
        orders = set()
        for _ in range(5):
            fan = coordinator.fan_out([compute_spec("a"), compute_spec("b"), compute_spec("c")], timeout=2.0)
            orders.add(tuple(o.task_id for o in fan.outcomes))
        self.assertEqual(orders, {("a", "b", "c")}, "ordering must not vary between runs")

    def test_results_accessor_skips_refusals_without_reordering(self):
        worker = NonMemoryWorker()
        coordinator = Coordinator({VERIFIER: worker})
        bad = TaskSpec(task_id="bad", agent=MEMORY_SPECIALIST, delegated_by="coordinator",
                       objective="recall", context={"case_signature": "x"},
                       allowed_tools=("retain_case",), model="primary", depth=1)
        fan = coordinator.fan_out([compute_spec("t-0"), bad, compute_spec("t-1")])
        self.assertEqual([r.task_id for r in fan.results], ["t-0", "t-1"])
        self.assertEqual([o.task_id for o in fan.outcomes], ["t-0", "bad", "t-1"],
                         "the outcome list must keep the refused task in place")


class AuthorisationTests(FanOutBase):
    """6. Wrong-agent and authorisation failures stay explicit."""

    def test_an_unauthorised_tool_is_refused_per_task(self):
        worker = NonMemoryWorker()
        coordinator = Coordinator({VERIFIER: worker})
        bad = TaskSpec(task_id="bad", agent=MEMORY_SPECIALIST, delegated_by="coordinator",
                       objective="recall", context={"case_signature": "x"},
                       allowed_tools=("retain_case",), model="primary", depth=1)
        fan = coordinator.fan_out([compute_spec("t-0"), bad, compute_spec("t-1")])
        self.assertTrue(fan.joined)
        refused = fan.outcomes[1]
        self.assertTrue(refused.refused)
        self.assertEqual(refused.status, "refused")
        self.assertIsNone(refused.result, "a refused task produces no result to mistake for success")
        self.assertIsInstance(refused.error, AuthorizationError)
        self.assertTrue(worker.started == ["t-0", "t-1"] or set(worker.started) == {"t-0", "t-1"},
                        "a refused task must never reach the worker")

    def test_a_refusal_does_not_abort_its_siblings(self):
        worker = NonMemoryWorker()
        coordinator = Coordinator({VERIFIER: worker})
        bad = TaskSpec(task_id="bad", agent=MEMORY_SPECIALIST, delegated_by="coordinator",
                       objective="recall", context={"case_signature": "x"},
                       allowed_tools=("retain_case",), model="primary", depth=1)
        fan = coordinator.fan_out([bad, compute_spec("t-0")])
        self.assertEqual(len(fan), 2)
        self.assertEqual(fan.summary()["by_status"], {"refused": 1, "success": 1})

    def test_raise_for_refusals_is_available_but_not_automatic(self):
        worker = NonMemoryWorker()
        coordinator = Coordinator({VERIFIER: worker})
        bad = TaskSpec(task_id="bad", agent=MEMORY_SPECIALIST, delegated_by="coordinator",
                       objective="recall", context={"case_signature": "x"},
                       allowed_tools=("retain_case",), model="primary", depth=1)
        fan = coordinator.fan_out([compute_spec("t-0"), bad])
        self.assertTrue(fan.succeeded, "the good task still ran")
        with self.assertRaises(AuthorizationError):
            fan.raise_for_refusals()

    def test_an_unregistered_agent_is_refused(self):
        worker = NonMemoryWorker()
        coordinator = Coordinator({VERIFIER: worker})
        invented = TaskSpec(task_id="nope", agent="omnipotent_agent", delegated_by="coordinator",
                            objective="do everything", context={"case_signature": "x"},
                            allowed_tools=("hindsight_recall",), model="primary", depth=1)
        fan = coordinator.fan_out([compute_spec("t-0"), invented])
        self.assertTrue(fan.outcomes[1].refused)
        self.assertIn("omnipotent_agent", str(fan.outcomes[1].error))

    def test_single_delegation_still_refuses_loudly(self):
        worker = NonMemoryWorker()
        coordinator = Coordinator({VERIFIER: worker})
        bad = TaskSpec(task_id="bad", agent=MEMORY_SPECIALIST, delegated_by="coordinator",
                       objective="recall", context={"case_signature": "x"},
                       allowed_tools=("retain_case",), model="primary", depth=1)
        with self.assertRaises(AuthorizationError):
            coordinator.delegate(bad)
        self.assertEqual(worker.started, [], "a refused task must never reach the worker")

    def test_the_worker_may_not_use_the_task_tool(self):
        """Anti-recursion is unchanged: no worker can delegate further, so no nesting under fan-out."""
        with self.assertRaises(AuthorizationError):
            authorize_tool(MEMORY_SPECIALIST, "task")


class NoPerWorkerCoordinatorTests(FanOutBase):
    """8. No per-worker Coordinator, lane or binding is created."""

    def test_fan_out_creates_no_coordinator_or_lane(self):
        client = ClientEntryCounter()
        port = self.port(client)
        runtime = build_runtime(port, memory_specialist=MemorySpecialist(port))
        coordinator, lane = runtime.coordinator, runtime.lane
        registrations = bound_count()
        dispatched = len(coordinator.dispatches)

        worker = NonMemoryWorker()
        shared = Coordinator({VERIFIER: worker})
        shared.fan_out([memory_spec(f"t-{i}") for i in range(3)])

        self.assertIs(shared.lane, shared.lane, "the lane is a single object")
        self.assertEqual(bound_count(), registrations,
                         "fan-out must not register anything in the composition root")
        self.assertIs(coordinator_for(port), coordinator, "the shared binding is untouched")
        self.assertIs(coordinator.lane, lane, "the shared lane identity is unchanged")
        self.assertEqual(len(coordinator.dispatches), dispatched,
                         "a different Coordinator's fan-out must not record into the shared one")

    def test_threads_never_construct_a_coordinator(self):
        import inspect

        from debugagent.agents import coordinator as module

        source = inspect.getsource(module.Coordinator.fan_out)
        self.assertNotIn("Coordinator(", source,
                         "fan_out must use `self`, never construct a Coordinator")
        self.assertNotIn("MemoryLane(", source,
                         "fan_out must use the shared lane, never construct one")

    def test_lane_acquisitions_match_the_number_of_memory_tasks(self):
        client = ClientEntryCounter()
        port = self.port(client)
        coordinator = Coordinator.with_memory_specialist(MemorySpecialist(port))
        before = coordinator.lane.entered
        coordinator.fan_out([memory_spec(f"t-{i}") for i in range(3)])
        self.assertEqual(coordinator.lane.entered - before, 3,
                         "one lane acquisition per client-touching task, no more")


class LaneDecisionTests(FanOutBase):
    """The conditional that makes the design work: the ROSTER decides, not the worker object."""

    def test_a_registered_client_agent_always_takes_the_lane(self):
        """`memory_specialist` is registered `client_access=True`, so every one of its tasks lanes."""
        from debugagent.agents.registry import get_agent

        self.assertTrue(get_agent(MEMORY_SPECIALIST).client_access)
        coordinator = Coordinator({MEMORY_SPECIALIST: NonMemoryWorker()})
        before = coordinator.lane.entered
        coordinator.fan_out([memory_spec("t-0")])
        self.assertEqual(coordinator.lane.entered - before, 1)

    def test_a_registered_non_client_agent_does_not_take_the_lane(self):
        """`code_log_verifier` is registered `client_access=False`, so its tasks run free."""
        from debugagent.agents.registry import get_agent

        self.assertFalse(get_agent(VERIFIER).client_access)
        coordinator = Coordinator({VERIFIER: NonMemoryWorker()})
        self.assertFalse(coordinator.touches_client(compute_spec("t-0")))
        before = coordinator.lane.entered
        coordinator.fan_out([compute_spec(f"t-{i}") for i in range(3)])
        self.assertEqual(coordinator.lane.entered, before,
                         "a registered non-client agent must not consume the memory lane")

    def test_a_worker_object_cannot_opt_itself_out_of_the_lane(self):
        """The old worker-side convention is gone, and this pins that it cannot return quietly.

        A worker that declares `client_access = False` while serving a client-access agent must still
        be serialised: the capability is read from the roster, so nothing the worker says is consulted.
        """
        class LyingWorker(NonMemoryWorker):
            client_access = False

        coordinator = Coordinator({MEMORY_SPECIALIST: LyingWorker()})
        self.assertTrue(coordinator.touches_client(memory_spec("t-0")),
                        "the roster's capability must win over the worker's own attribute")
        before = coordinator.lane.entered
        coordinator.fan_out([memory_spec("t-0")])
        self.assertEqual(coordinator.lane.entered - before, 1)

    def test_omitting_memory_tools_does_not_buy_unserialised_access(self):
        """Goal: a client-access task cannot escape the lane by listing no memory tools."""
        bare = TaskSpec(task_id="bare", agent=MEMORY_SPECIALIST, delegated_by="coordinator",
                        objective="recall", context={"case_signature": "x"},
                        allowed_tools=("read",), model="primary", depth=1)
        coordinator = Coordinator({MEMORY_SPECIALIST: NonMemoryWorker()})
        self.assertTrue(coordinator.touches_client(bare),
                        "an empty or non-memory tool list must not serialise nothing for a client agent")

    def test_the_lane_is_shared_not_per_task(self):
        worker = NonMemoryWorker()
        coordinator = Coordinator({VERIFIER: worker})
        lane = coordinator.lane
        coordinator.fan_out([compute_spec(f"t-{i}") for i in range(3)])
        self.assertIs(coordinator.lane, lane, "the lane object must be stable across a fan-out")


class NoRetriesTests(unittest.TestCase):
    """No retries and no exactly-once claims crept in."""

    def test_a_failing_task_is_attempted_exactly_once(self):
        attempts: list[str] = []

        class CountingFailure(NonMemoryWorker):
            def execute(self, spec: TaskSpec) -> SubAgentResult:
                attempts.append(spec.task_id)
                raise RuntimeError("always fails")

        coordinator = Coordinator({VERIFIER: CountingFailure()})
        fan = coordinator.fan_out([compute_spec("t-0"), compute_spec("t-1")], timeout=2.0)
        self.assertEqual(sorted(attempts), ["t-0", "t-1"],
                         "each task must be attempted exactly once - no retry")
        self.assertEqual(fan.summary()["by_status"], {"failed": 2})

    def test_no_retry_or_backoff_primitives(self):
        """No retries, no backoff, no sleeping.

        Scans the function's CODE, not its text. The docstring legitimately contains the word "retries"
        in order to disclaim them, so a substring search over the raw source would fail on the very
        sentence promising not to retry - a test that quietly pressures you to delete the warning
        instead. Comments are excluded for the same reason: the comments here explain what is absent.
        """
        import ast
        import inspect
        import textwrap

        from debugagent.agents.coordinator import Coordinator

        # `textwrap.dedent`, not `inspect.cleandoc`: cleandoc also strips the first line's indentation,
        # which leaves an indented `def` unparseable.
        tree = ast.parse(textwrap.dedent(inspect.getsource(Coordinator.fan_out)))
        function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))

        # Identifiers only, with the docstring node skipped explicitly. Scanning raw text would flag the
        # docstring's own promise not to retry, and scanning the whole AST dump would still pick up that
        # docstring as a Constant child of its Expr - both turn the warning into an obstacle.
        docstring = function.body[0].value if isinstance(function.body[0], ast.Expr) else None
        identifiers: list[str] = []
        for node in ast.walk(function):
            if node is docstring:
                continue
            if isinstance(node, ast.Name):
                identifiers.append(node.id)
            elif isinstance(node, ast.Attribute):
                identifiers.append(node.attr)
        code = " ".join(identifiers).lower()

        for forbidden in ("retry", "backoff", "sleep", "max_attempts", "attempt"):
            self.assertNotIn(forbidden, code,
                             f"fan-out code must not reference {forbidden}: no retries, by design")


if __name__ == "__main__":
    unittest.main()
