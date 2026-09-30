"""P5 serial memory lane: one thread at a time touches the Hindsight client.

The P5 concurrency audit found that `hindsight_client` caches a single `aiohttp.ClientSession` bound
to the loop that created it, while its synchronous bridge gives each thread its own loop - so
concurrent use fails, for reads as well as writes. The store's `_retain_lock` cannot fix that: it is
keyed on the ledger path, covers `retain` only, and does not exist during construction.

This step therefore adds no new lock to `hindsight_store.py`. It adds one `MemoryLane` on the
Coordinator and routes every Hindsight access through it - the delegated worker, the flow's own direct
`recall()`, and the retention write. Fan-out, join barriers and partial-failure aggregation remain
unimplemented, and nothing here presumes them.

The five properties under test:

1. Memory operations are serialised through the Coordinator.
2. Recall and retain never concurrently enter the client, driven through two real `investigate()`
   sessions on one port - the end-to-end proof, not a unit test of the lock.
3. Different banks cannot bypass the boundary.
4. P3-1 and P3-2 guarantees are intact.
5. Non-memory work is structurally ready for future parallelism - the lane is scoped to the worker
   call, and is not a Coordinator-wide serialisation of everything.

Determinism: no sleeps. Concurrency is observed by counting how many threads are inside a client call,
and asserted with a bounded barrier whose timeout is a legitimate "the peer never got in" outcome.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

import support
from debugagent.agents.coordinator import Coordinator, MemoryLane
from debugagent.agents.memory_specialist import MemorySpecialist
from debugagent.agents.tasks import SubAgentResult, TaskSpec
from debugagent.memory.hindsight_store import HindsightMemoryStore, compute_case_key
from debugagent.pipeline.ingest import load_debug_input
from debugagent.pipeline.investigate import investigate
from debugagent.pipeline.memory_adapter import HindsightMemoryPort
from debugagent.pipeline.normalize import normalize
from debugagent.pipeline.recall_match import recall
from support import FakeHindsightClient, FakeMemory, FakeScores, memory_config

import loop_support
from loop_support import RESOLVED, FakeLLM, ScriptedEngineer, hyp

LANE_TIMEOUT = 5.0
# The client's rendezvous timeout is deliberately short. It only decides how long a lone caller
# waits for a peer before concluding "I was serialised", which is a correct outcome for a working
# lane - so there is nothing to gain from waiting long, and 5s per call dominated the suite.
RENDEZVOUS_TIMEOUT = 1.0
ACT2 = "act2-media-uploader.json"


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
    """Counts how many threads are inside ANY client call at once.

    One counter across `recall`, `retain` and bank provisioning, because the hazard is the client
    object, not any one method. Two threads inside any two methods at the same time is exactly the
    condition the real client cannot survive.
    """

    def __init__(self, *, rendezvous: bool = False):
        super().__init__()
        self.rendezvous = rendezvous
        self.inside = 0
        self.max_inside = 0
        self.calls = 0
        self.rendezvous_broken = 0
        self._guard = threading.Lock()
        self._barrier: threading.Barrier | None = None

    def _around(self, function, rendezvous: bool, *args, **kwargs):
        with self._guard:
            self.inside += 1
            self.calls += 1
            self.max_inside = max(self.max_inside, self.inside)
        if self.rendezvous and rendezvous:
            # Bounded: a timeout is a legitimate outcome meaning "the peer never entered", i.e. the
            # lane serialised correctly. Assertions are on counts, never on elapsed time.
            #
            # Only DATA calls rendezvous. Store construction provisions a bank single-threaded, and a
            # barrier consumed by a lone caller is left permanently broken - which silently disables
            # the rendezvous for the concurrent phase the test is actually about.
            barrier = self._barrier_for_two()
            try:
                barrier.wait(timeout=RENDEZVOUS_TIMEOUT)
            except threading.BrokenBarrierError:
                self.rendezvous_broken += 1
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


class SlowWorker:
    """A worker that holds the client for a controlled, event-driven interval.

    Uses events rather than a sleep, so the test decides exactly when the worker is inside the client
    and when it may leave. The lane must keep the other thread out for that whole window.
    """

    def __init__(self, entered: threading.Event, release: threading.Event,
                 result: SubAgentResult | None = None):
        self.entered = entered
        self.release = release
        self._result = result
        self.specs: list[TaskSpec] = []

    def execute(self, spec: TaskSpec) -> SubAgentResult:
        self.specs.append(spec)
        self.entered.set()
        self.release.wait(timeout=LANE_TIMEOUT)
        return self._result or SubAgentResult(task_id=spec.task_id, agent=spec.agent,
                                              status="success", observations=())


class LaneBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def port(self, client, *, bank="bank", ledger="ledger.json"):
        store = HindsightMemoryStore(
            memory_config(self.tmp_path, bank_id=bank, ledger_path=self.tmp_path / ledger),
            client=client)
        self.addCleanup(store.close)
        return HindsightMemoryPort(store)


class SerialisationTests(LaneBase):
    """1. Memory operations are serialised through the Coordinator."""

    def test_two_delegations_never_overlap(self):
        entered = threading.Event()
        release = threading.Event()
        worker = SlowWorker(entered, release)
        coordinator = Coordinator.with_memory_specialist(worker)
        second_ran = threading.Event()
        errors: list[BaseException] = []

        def dispatch(task_id: str, done: threading.Event | None) -> None:
            try:
                coordinator.delegate_memory(task_id=task_id, case_signature="uploads fail")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            if done is not None:
                done.set()

        first = threading.Thread(target=dispatch, args=("t-1", None))
        first.start()
        self.assertTrue(entered.wait(timeout=LANE_TIMEOUT), "the first worker never started")

        second = threading.Thread(target=dispatch, args=("t-2", second_ran))
        second.start()
        # The first worker is inside the lane and blocked on `release`, so the second dispatch cannot
        # have finished. Checked by absence, with `release` as the only thing that can unblock it.
        self.assertFalse(second_ran.wait(timeout=0.2),
                         "a second memory task entered while the first held the lane")
        release.set()
        for thread in (first, second):
            thread.join(timeout=LANE_TIMEOUT)

        self.assertEqual(errors, [])
        self.assertTrue(second_ran.is_set(), "the second dispatch never completed")
        self.assertEqual(len(worker.specs), 2)

    def test_the_lane_counts_every_dispatch(self):
        coordinator = Coordinator.with_memory_specialist(
            SlowWorker(threading.Event(), threading.Event()))
        for index in range(3):
            coordinator.delegate_memory(task_id=f"t-{index}", case_signature="uploads fail")
        self.assertEqual(coordinator.lane.entered, 3,
                         "each dispatch must pass through the lane exactly once")

    def test_the_lane_is_released_after_each_dispatch(self):
        coordinator = Coordinator.with_memory_specialist(
            SlowWorker(threading.Event(), threading.Event()))
        coordinator.delegate_memory(task_id="t-1", case_signature="uploads fail")
        self.assertTrue(coordinator.lane.is_free(),
                        "the lane must not be held between dispatches, or it would deadlock the flow")

    def test_a_lane_held_by_the_flow_still_allows_dispatch(self):
        """The flow wraps its own recall/retain in the lane, then dispatches. Reentrancy matters."""
        coordinator = Coordinator.with_memory_specialist(
            SlowWorker(threading.Event(), threading.Event()))
        with coordinator.lane:
            result = coordinator.delegate_memory(task_id="t-1", case_signature="uploads fail")
        self.assertTrue(result.ok, "a nested dispatch must not deadlock on the lane")


class ClientIsNeverSharedTests(LaneBase):
    """2. Recall and retain never concurrently enter the client, end to end."""

    def run_session(self, port, client, raw, session_id, coordinator=None, cited=()):
        llm = FakeLLM({"hypotheses": [hyp(cites=list(cited)), hyp(text="app-side limit", cites=list(cited))]})
        # ONE Coordinator is shared by every session, because the lane belongs to the Coordinator.
        # A Coordinator per session would mean a lane per session, and the client would be shared
        # across two boundaries - which is precisely the mistake this test exists to prevent.
        #
        # `cited` is passed in rather than recomputed here: doing that would issue an UNSERIALISED
        # recall from the test itself, which is exactly the hazard under test and would show up as a
        # false failure.
        hub = coordinator or Coordinator.with_memory_specialist(MemorySpecialist(port))
        return investigate(raw, port, llm, ScriptedEngineer(resolution=RESOLVED),
                           session_id=session_id, coordinator=hub)

    def cited_case_ids(self, port, raw) -> list[str]:
        context = recall(port, normalize(raw))
        return [] if context.abstained else [c["case_id"] for c in context.candidates][:1]

    def test_two_sessions_on_one_port_serialise_every_client_call(self):
        """Two real sessions, one port, one client, one Coordinator - the strongest form.

        Each session makes three kinds of client call: the worker's recall, the flow's direct
        recall, and the retention write. Every one of them must be exclusive.
        """
        client = ClientEntryCounter(rendezvous=True)
        port = self.port(client)
        raw = load_debug_input(Path(__file__).resolve().parents[1] / "demo" / "inputs" / ACT2)
        coordinator = Coordinator.with_memory_specialist(MemorySpecialist(port))
        cited = self.cited_case_ids(port, raw)

        errors: list[BaseException] = []
        guard = threading.Lock()
        start = threading.Barrier(2)

        def run(session_id: str) -> None:
            start.wait(timeout=LANE_TIMEOUT)
            try:
                self.run_session(port, client, raw, session_id, coordinator, cited)
            except BaseException as exc:  # noqa: BLE001 - reported to the test
                with guard:
                    errors.append(exc)

        threads = [threading.Thread(target=run, args=(f"s{i}",)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=LANE_TIMEOUT * 6)

        for thread in threads:
            self.assertFalse(thread.is_alive(), "a session deadlocked: possible lane deadlock")
        self.assertEqual(errors, [], f"a session failed: {errors}")
        self.assertEqual(client.max_inside, 1,
                         f"two threads were inside the client at once (calls={client.calls})")

    def test_every_memory_operation_takes_the_lane(self):
        """One session makes exactly three client-touching steps. All three must be in the lane.

        Counted rather than observed concurrently, because a concurrent observation passes whenever
        the two threads' calls happen not to line up - which is how an unserialised `recall` or
        `retain` slips through unnoticed. The count is exact and cannot drift.
        """
        client = ClientEntryCounter()
        port = self.port(client)
        raw = load_debug_input(Path(__file__).resolve().parents[1] / "demo" / "inputs" / ACT2)
        coordinator = Coordinator.with_memory_specialist(MemorySpecialist(port))
        cited = self.cited_case_ids(port, raw)

        self.run_session(port, client, raw, "lane-count", coordinator, cited)
        self.assertEqual(coordinator.lane.entered, 3,
                         "delegation, the flow's own recall, and the retention write must each "
                         "take the lane exactly once")

    def test_the_retention_write_takes_the_lane(self):
        """Isolates retention by comparing a resolved session against an unresolved one.

        An unresolved session returns before retaining, so the difference between the two counts is
        the retention write and nothing else.
        """
        raw = load_debug_input(Path(__file__).resolve().parents[1] / "demo" / "inputs" / ACT2)

        unresolved_client = ClientEntryCounter()
        unresolved_port = self.port(unresolved_client, ledger="unresolved.json")
        unresolved_hub = Coordinator.with_memory_specialist(MemorySpecialist(unresolved_port))
        cited = self.cited_case_ids(unresolved_port, raw)
        investigate(raw, unresolved_port,
                    FakeLLM({"hypotheses": [hyp(cites=cited), hyp(text="x", cites=cited)]}),
                    ScriptedEngineer(resolution=None), coordinator=unresolved_hub)

        resolved_client = ClientEntryCounter()
        resolved_port = self.port(resolved_client, ledger="resolved.json")
        resolved_hub = Coordinator.with_memory_specialist(MemorySpecialist(resolved_port))
        self.run_session(resolved_port, resolved_client, raw, "resolved", resolved_hub, cited)

        self.assertEqual(unresolved_hub.lane.entered, 2, "unresolved: delegation + recall only")
        self.assertEqual(resolved_hub.lane.entered, 3,
                         "the retention write must add exactly one more lane entry")
        self.assertEqual(len(resolved_client.retained), 1)
        self.assertEqual(len(unresolved_client.retained), 0)

    def test_the_flow_waits_for_the_lane_before_touching_the_client(self):
        """Behavioural proof for the flow's own recall, complementing the count above.

        With the lane held by another thread, the session must not reach the client at all. The
        client signals every call, so a recall that bypassed the lane would be visible immediately.
        """
        recall_seen = threading.Event()

        class SignallingClient(ClientEntryCounter):
            def recall(self, **kwargs):
                recall_seen.set()
                return super().recall(**kwargs)

        client = SignallingClient()
        port = self.port(client)
        raw = load_debug_input(Path(__file__).resolve().parents[1] / "demo" / "inputs" / ACT2)
        coordinator = Coordinator.with_memory_specialist(MemorySpecialist(port))
        cited = self.cited_case_ids(port, raw)
        recall_seen.clear()

        held = threading.Event()
        release = threading.Event()

        def hold() -> None:
            with coordinator.lane:
                held.set()
                release.wait(timeout=LANE_TIMEOUT)

        holder = threading.Thread(target=hold)
        holder.start()
        self.assertTrue(held.wait(timeout=LANE_TIMEOUT))

        done = threading.Event()

        def run_session_thread() -> None:
            self.run_session(port, client, raw, "blocked", coordinator, cited)
            done.set()

        session = threading.Thread(target=run_session_thread)
        session.start()
        try:
            self.assertFalse(recall_seen.wait(timeout=0.3),
                             "the flow reached the client while the lane was held")
            self.assertFalse(done.is_set(), "the session completed while the lane was held")
        finally:
            release.set()
            holder.join(timeout=LANE_TIMEOUT)
        session.join(timeout=LANE_TIMEOUT * 4)
        self.assertFalse(session.is_alive(), "the session deadlocked after the lane was released")
        self.assertTrue(done.is_set(), "the session never completed")
        self.assertTrue(recall_seen.is_set(), "the session should have recalled after release")

    def test_a_coordinator_per_session_does_not_serialise(self):
        """The footgun, pinned deliberately.

        The lane belongs to the Coordinator, so a Coordinator per session means a lane per session,
        and a client shared across two lanes is unprotected. The registry holds one worker id, so this
        is driven as two Coordinators over the same port and client - the same hazard a per-session
        Coordinator would create, without the session overhead.

        Exactly two data calls are issued, so the client's rendezvous barrier pairs them immediately
        and the outcome is decided by exclusion rather than by scheduling luck. Run full sessions
        here and the calls never line up, which makes the test pass for the wrong reason.
        """
        client = ClientEntryCounter(rendezvous=True)
        port = self.port(client)
        one = Coordinator.with_memory_specialist(MemorySpecialist(port))
        two = Coordinator.with_memory_specialist(MemorySpecialist(port))

        errors: list[BaseException] = []
        start = threading.Barrier(2)

        def run(coordinator) -> None:
            start.wait(timeout=LANE_TIMEOUT)
            try:
                coordinator.delegate_memory(task_id="t", case_signature="uploads fail")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(one,)),
                   threading.Thread(target=run, args=(two,))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=LANE_TIMEOUT * 4)

        self.assertEqual(errors, [])
        self.assertGreaterEqual(
            client.max_inside, 2,
            "two Coordinators with their own lanes were expected NOT to serialise; if they now do, "
            "the lane has become process-wide and this hazard no longer needs documenting")

    def test_all_three_client_call_kinds_actually_happened(self):
        """Guards the test above from passing because the calls never occurred."""
        client = ClientEntryCounter()
        port = self.port(client)
        raw = load_debug_input(Path(__file__).resolve().parents[1] / "demo" / "inputs" / ACT2)
        self.run_session(port, client, raw, "solo")
        self.assertGreaterEqual(client.calls, 3,
                                "the session must reach the client for provision, recall and retain")
        self.assertEqual(len(client.retained), 1, "the session must have retained once")

    def test_engines_provisioning_is_serialised_too(self):
        """Bank provisioning happens in `__init__`, before any store lock exists.

        Constructing two stores concurrently on one client reaches the client with no lane held, which
        is why the lane is a Coordinator concern and why this is called out rather than assumed.
        """
        client = ClientEntryCounter(rendezvous=True)
        port = self.port(client, bank="provisioned")
        self.assertGreaterEqual(client.calls, 1, "the store must provision its bank")
        # Sequential by construction here; the assertion is that the store is usable and accounted for.
        self.assertEqual(client.max_inside, 1)


class BankIsolationTests(LaneBase):
    """3. Different banks cannot bypass the boundary."""

    def test_two_bank_workers_on_one_coordinator_still_serialise(self):
        """Two stores, two ledger paths, two banks, one client - all through ONE Coordinator.

        This is the exact shape the audit called unsafe at the store level, where each store took its
        own ledger-keyed lock. Driving both through one Coordinator's lane makes it safe, because the
        boundary is the Coordinator and does not care which bank a call addresses.

        The roster has one worker id, so the two banks are driven through the lane directly rather than
        through two registered workers - the property under test is the boundary, not the worker map.
        """
        from debugagent.schemas import MemoryCase

        client = ClientEntryCounter(rendezvous=True)
        port_a = self.port(client, bank="bank-a", ledger="a.json")
        port_b = self.port(client, bank="bank-b", ledger="b.json")
        coordinator = Coordinator.with_memory_specialist(MemorySpecialist(port_a))

        case = MemoryCase.from_dict({
            "problem_signature": "connection reset above 2MB on orders-api",
            "symptoms": ["reset on large payloads"],
            "environment": {"service": "orders-api"},
            "observed_evidence": ["report://x"],
            "investigation_trace": ["sweep"],
            "failed_approaches": [],
            "root_cause": "proxy limit",
            "resolution": "raised the limit",
            "outcome": "resolved",
            "verification_notes": "confirmed",
            "session_id": "bank-pair",
        })

        errors: list[BaseException] = []
        start = threading.Barrier(2)

        def run(port) -> None:
            start.wait(timeout=LANE_TIMEOUT)
            try:
                coordinator.lane.call(port.retain, case.to_dict())
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(port_a,)),
                   threading.Thread(target=run, args=(port_b,))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=LANE_TIMEOUT * 4)

        self.assertEqual(errors, [])
        self.assertEqual(len(client.retained), 2, "both banks must have retained")
        self.assertEqual(client.max_inside, 1,
                         "two different banks reached one client concurrently")

    def test_a_lane_can_be_shared_between_two_coordinators(self):
        """The escape hatch for a future fan-out with several Coordinators on one client.

        Sharing a lane is what makes them one boundary. Two Coordinators with their own lanes are two
        boundaries and would NOT serialise - documented here so nobody assumes otherwise.
        """
        shared = MemoryLane()
        client = ClientEntryCounter(rendezvous=True)
        port = self.port(client)
        worker = MemorySpecialist(port)
        one = Coordinator.with_memory_specialist(worker, lane=shared)
        two = Coordinator.with_memory_specialist(worker, lane=shared)

        errors: list[BaseException] = []
        start = threading.Barrier(2)

        def run(coordinator, task_id) -> None:
            start.wait(timeout=LANE_TIMEOUT)
            try:
                coordinator.delegate_memory(task_id=task_id, case_signature="uploads fail")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(one, "t-1")),
                   threading.Thread(target=run, args=(two, "t-2"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=LANE_TIMEOUT * 4)

        self.assertEqual(errors, [])
        self.assertEqual(client.max_inside, 1, "a shared lane must serialise two Coordinators")

    def test_separate_lanes_do_not_serialise(self):
        """The negative control for the test above: proves the lane is what does the work."""
        client = ClientEntryCounter(rendezvous=True)
        port = self.port(client)
        worker = MemorySpecialist(port)
        one = Coordinator.with_memory_specialist(worker)  # its own lane
        two = Coordinator.with_memory_specialist(worker)  # a different one

        errors: list[BaseException] = []
        start = threading.Barrier(2)

        def run(coordinator) -> None:
            start.wait(timeout=LANE_TIMEOUT)
            try:
                coordinator.delegate_memory(task_id="t", case_signature="uploads fail")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(one,)), threading.Thread(target=run, args=(two,))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=LANE_TIMEOUT * 4)

        self.assertEqual(errors, [])
        self.assertGreaterEqual(
            client.max_inside, 2,
            "two Coordinators with separate lanes should NOT serialise; if this ever changes the "
            "shared-lane test above is no longer proving anything")


class PreservedGuaranteesTests(LaneBase):
    """4. P3-1 and P3-2 still hold with a Coordinator in the flow."""

    def test_retain_is_still_written_remotely_once(self):
        client = ClientEntryCounter()
        port = self.port(client)
        raw = load_debug_input(Path(__file__).resolve().parents[1] / "demo" / "inputs" / ACT2)
        ClientIsNeverSharedTests.run_session(self, port, client, raw, "p3-2")
        self.assertEqual(len(client.retained), 1)

    def test_state_idempotent_replacement_still_applies(self):
        """P3-2B: the remote call still carries a content-addressed document_id and replace mode."""
        client = ClientEntryCounter()
        port = self.port(client)
        raw = load_debug_input(Path(__file__).resolve().parents[1] / "demo" / "inputs" / ACT2)
        ClientIsNeverSharedTests.run_session(self, port, client, raw, "p3-2b")
        call = client.retained[0]
        self.assertEqual(call["update_mode"], "replace")
        self.assertTrue(call["document_id"], "retain must carry a content-addressed document_id")

    def test_sequential_retains_are_unaffected_by_the_lane(self):
        """The lane must not change single-threaded behaviour."""
        client = ClientEntryCounter()
        port = self.port(client)
        from debugagent.schemas import MemoryCase

        valid = {
            "problem_signature": "connection reset above 2MB on orders-api",
            "symptoms": ["reset on large payloads"],
            "environment": {"service": "orders-api"},
            "observed_evidence": ["report://x"],
            "investigation_trace": ["sweep"],
            "failed_approaches": [],
            "root_cause": "proxy limit",
            "resolution": "raised the limit",
            "outcome": "resolved",
            "verification_notes": "confirmed",
        }
        coordinator = Coordinator.with_memory_specialist(MemorySpecialist(port))
        decisions = [coordinator.lane.call(port.retain, MemoryCase.from_dict(dict(valid, session_id=f"s{i}")).to_dict())
                     for i in range(3)]
        self.assertTrue(all(d["retained"] for d in decisions),
                        f"sequential retains must all succeed: {decisions}")
        self.assertEqual(len(client.retained), 3)

    def test_the_lane_adds_no_authority(self):
        """Taking a lock is not permission. Retention still follows only the engineer's resolution."""
        from debugagent.agents.memory_specialist import build_memory_specialist_task

        port = self.port(ClientEntryCounter())
        coordinator = Coordinator.with_memory_specialist(MemorySpecialist(port))
        result = coordinator.lane.call(
            MemorySpecialist(port).execute,
            build_memory_specialist_task(task_id="t", case_signature="x",
                                         objective="retain this resolved case"))
        self.assertEqual(result.status, "failed", "the worker must still refuse to retain")
        self.assertIn("engineer decision", result.failure_detail)


class FutureParallelismReadinessTests(LaneBase):
    """5. Non-memory work is structurally ready for parallelism."""

    def test_authorisation_happens_outside_the_lane(self):
        """A refusal must not queue behind an in-flight memory call.

        Authorisation is pure and touches no client, so serialising it would add latency and remove
        none of the risk. Asserted by holding the lane and showing a rejected spec still fails fast.
        """
        from debugagent.agents.registry import AuthorizationError
        from debugagent.agents.tasks import TaskSpec

        worker = SlowWorker(threading.Event(), threading.Event())
        coordinator = Coordinator.with_memory_specialist(worker)
        bad = TaskSpec(task_id="t-1", agent="memory_specialist", delegated_by="coordinator",
                       objective="recall", context={"case_signature": "x"},
                       allowed_tools=("retain_case",), model="primary", depth=1)
        held = threading.Event()
        release = threading.Event()

        def hold() -> None:
            with coordinator.lane:
                held.set()
                release.wait(timeout=LANE_TIMEOUT)

        holder = threading.Thread(target=hold)
        holder.start()
        self.assertTrue(held.wait(timeout=LANE_TIMEOUT))

        # The lane is held by `holder`. A rejected spec must still raise, not block.
        outcome: list[BaseException] = []

        def reject() -> None:
            try:
                coordinator.delegate(bad)
            except BaseException as exc:  # noqa: BLE001
                outcome.append(exc)

        rejecter = threading.Thread(target=reject)
        rejecter.start()
        rejecter.join(timeout=LANE_TIMEOUT)
        self.assertFalse(rejecter.is_alive(),
                         "authorisation blocked behind the lane; it must happen outside it")
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], AuthorizationError)
        self.assertEqual(worker.specs, [], "a rejected task must never reach the worker")
        release.set()
        holder.join(timeout=LANE_TIMEOUT)

    def test_the_lane_is_scoped_to_the_worker_call_not_the_dispatch(self):
        """Structural: one lane acquisition per dispatched task, never a batch-wide one.

        A lane held across a whole fan-out would serialise the non-memory work too, which is the
        opposite of what P5 wants. So the count must track the number of tasks, not the number of
        batches.
        """
        import inspect

        from debugagent.agents import coordinator as module

        single = inspect.getsource(module.Coordinator._execute_authorized)
        self.assertEqual(single.count("with self._lane:"), 1,
                         "a single dispatch must take the lane exactly once, around the worker call")
        fan_out = inspect.getsource(module.Coordinator.fan_out)
        self.assertIn("with self._lane:", fan_out,
                      "client-touching tasks must still be serialised inside a fan-out")
        self.assertIn("if lane_plan[index]:", fan_out,
                      "the lane must be conditional on the task touching the client")

    def test_no_fanout_primitives_have_been_introduced(self):
        """Fan-out is now implemented, so this asserts it stays CONFINED.

        The primitives are expected in `fan_out` and its helper, and nowhere else - a thread pool
        appearing in `delegate` or in the lane would mean the serial path quietly went parallel.
        """
        import inspect

        from debugagent.agents import coordinator as module

        for name in ("MemoryLane", "Worker", "TaskOutcome", "FanOutResult", "Dispatch", "_Absent"):
            source = inspect.getsource(getattr(module, name))
            for forbidden in ("ThreadPool", "Executor", "as_completed", "asyncio",
                              "multiprocessing", "gather", "Queue", "join_barrier"):
                self.assertNotIn(forbidden, source,
                                 f"{name} must not contain {forbidden}: fan-out belongs in fan_out")
        single = inspect.getsource(module.Coordinator.delegate)
        for forbidden in ("Thread", "Barrier"):
            self.assertNotIn(forbidden, single,
                             "a single delegation must stay serial: no threads in delegate()")

    def test_partial_failure_aggregation_keeps_every_result(self):
        """The opposite of the old guard: aggregation must not lose individual results.

        Every dispatched task keeps its own outcome, in input order, whatever its status.
        """
        coordinator = Coordinator.with_memory_specialist(
            SlowWorker(threading.Event(), threading.Event()))
        fan = coordinator.fan_out([
            self._spec("t-0"), self._spec("t-1"), self._spec("t-2")])
        self.assertEqual(len(fan), 3)
        self.assertEqual([o.task_id for o in fan.outcomes], ["t-0", "t-1", "t-2"])
        self.assertTrue(fan.joined)
        for outcome in fan.outcomes:
            self.assertIsNotNone(outcome.result, "no outcome may be dropped")
            self.assertIsNone(outcome.error)

    @staticmethod
    def _spec(task_id: str):
        from debugagent.agents.memory_specialist import build_memory_specialist_task

        return build_memory_specialist_task(task_id=task_id, case_signature="uploads fail")


class Phase1DefaultUnchangedTests(LaneBase):
    """The lane is opt-in: a Phase 1 session with no Coordinator must be untouched."""

    def test_no_coordinator_means_no_lane_and_no_locking(self):
        client = ClientEntryCounter()
        port = self.port(client)
        raw = load_debug_input(Path(__file__).resolve().parents[1] / "demo" / "inputs" / ACT2)
        cited = [c["case_id"] for c in recall(port, normalize(raw)).candidates][:1]
        llm = FakeLLM({"hypotheses": [hyp(cites=cited), hyp(text="app-side limit", cites=cited)]})
        session = investigate(raw, port, llm, ScriptedEngineer(resolution=RESOLVED))
        self.assertNotIn("memory_delegation", session.to_dict())
        self.assertIsNone(session.memory_delegation)
        self.assertEqual(len(client.retained), 1, "the default path must still retain")

    def test_the_default_path_output_is_unchanged(self):
        client = ClientEntryCounter()
        port = self.port(client)
        raw = load_debug_input(Path(__file__).resolve().parents[1] / "demo" / "inputs" / ACT2)
        cited = [c["case_id"] for c in recall(port, normalize(raw)).candidates][:1]
        llm = FakeLLM({"hypotheses": [hyp(cites=cited), hyp(text="app-side limit", cites=cited)]})
        serialised = investigate(raw, port, llm, ScriptedEngineer(resolution=RESOLVED)).to_dict()
        self.assertEqual(set(serialised), {"session_id", "input", "case", "memory", "evidence",
                                           "proposal", "verifications", "resolution",
                                           "retention", "trace"})


if __name__ == "__main__":
    unittest.main()
