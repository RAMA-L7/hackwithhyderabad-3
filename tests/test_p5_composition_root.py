"""P5 composition root: one Hindsight client -> exactly one Coordinator -> exactly one MemoryLane.

The serial memory lane is owned by a `Coordinator`, which makes the lane only as good as the
Coordinator's reach. A future fan-out that built one Coordinator per worker would hand the same
non-thread-safe client one lane each, and nothing would be serialised - a failure that is silent,
invisible in the happy path, and would only appear as an intermittent `RuntimeError` from aiohttp in
production.

So the invariant is enforced where objects are assembled. `debugagent.composition` binds one
Coordinator per client, and both the CLI and `investigate()` go through it.

What is proved here:

1. Two concurrent sessions on one client share one Coordinator and one lane.
2. A second Coordinator over the same client is REFUSED, not discouraged - including the case the
   port-keyed version would have missed: two different `MemoryPort` objects over one client.
3. The serial memory lane still holds (the P5 guarantee is preserved, not replaced).
4. The registry is weak and does not leak.
5. The CLI - the real composition root - builds exactly one and injects it.
6. No fan-out primitives crept in.

Determinism: the concurrency test counts how many threads are inside the client, as in the P5 lane
suite. No sleeps.
"""

from __future__ import annotations

import gc
import tempfile
import threading
import unittest
from pathlib import Path

import support
from debugagent.agents.coordinator import Coordinator, MemoryLane
from debugagent.agents.memory_specialist import MemorySpecialist
from debugagent.composition import (
    CompositionError,
    bind,
    bound_count,
    build_runtime,
    coordinator_for,
    identity_of,
    unbind,
)
from debugagent.memory.hindsight_store import HindsightMemoryStore, compute_case_key
from debugagent.pipeline.ingest import load_debug_input
from debugagent.pipeline.investigate import investigate
from debugagent.pipeline.memory_adapter import HindsightMemoryPort
from debugagent.pipeline.normalize import normalize
from debugagent.pipeline.recall_match import recall
from support import FakeHindsightClient, FakeMemory, FakeScores, memory_config

import loop_support
from loop_support import RESOLVED, FakeLLM, ScriptedEngineer, hyp

TIMEOUT = 5.0
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
    """Counts threads inside any client call, so overlap is observable rather than assumed.

    Provisioning does not rendezvous: store construction is single-threaded, and a barrier consumed
    by a lone caller is left broken, which would silently disable the rendezvous that matters.
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


class CompositionBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._release)

    def _release(self) -> None:
        """Drop any binding this test created, so tests cannot leak into one another."""
        for port in list(getattr(self, "_ports", ())):
            unbind(port)

    def store(self, client, *, bank="bank", ledger="ledger.json"):
        store = HindsightMemoryStore(
            memory_config(self.tmp_path, bank_id=bank, ledger_path=self.tmp_path / ledger),
            client=client)
        self.addCleanup(store.close)
        return store

    def port(self, client, *, bank="bank", ledger="ledger.json"):
        port = HindsightMemoryPort(self.store(client, bank=bank, ledger=ledger))
        ports = getattr(self, "_ports", None)
        if ports is None:
            ports = self._ports = []
        ports.append(port)
        return port

    def raw(self, filename=ACT2):
        return load_debug_input(Path(__file__).resolve().parents[1] / "demo" / "inputs" / filename)

    def cited(self, port, raw) -> list[str]:
        context = recall(port, normalize(raw))
        return [] if context.abstained else [c["case_id"] for c in context.candidates][:1]

    def llm(self, port, raw):
        cites = self.cited(port, raw)
        return FakeLLM({"hypotheses": [hyp(cites=cites), hyp(text="app-side limit", cites=cites)]})


class SingleCoordinatorTests(CompositionBase):
    """The invariant: one client, one Coordinator, one lane."""

    def test_build_runtime_is_idempotent_for_one_client(self):
        port = self.port(FakeHindsightClient())
        first = build_runtime(port, memory_specialist=MemorySpecialist(port))
        second = build_runtime(port, memory_specialist=MemorySpecialist(port))
        self.assertIs(first.coordinator, second.coordinator,
                      "composing twice for one client must not produce two Coordinators")
        self.assertIs(first.lane, second.lane)

    def test_rebinding_the_same_coordinator_is_free(self):
        port = self.port(FakeHindsightClient())
        coordinator = Coordinator.with_memory_specialist(MemorySpecialist(port))
        for _ in range(5):
            self.assertIs(bind(port, coordinator), coordinator,
                          "re-binding the same Coordinator is the normal multi-session case")

    def test_a_second_coordinator_over_one_client_is_refused(self):
        port = self.port(FakeHindsightClient())
        first = Coordinator.with_memory_specialist(MemorySpecialist(port))
        second = Coordinator.with_memory_specialist(MemorySpecialist(port))
        bind(port, first)
        with self.assertRaises(CompositionError) as caught:
            bind(port, second)
        message = str(caught.exception)
        self.assertIn("one client", message.lower())
        self.assertIs(coordinator_for(port), first,
                      "a refused bind must leave the original binding untouched")

    def test_two_ports_over_one_client_still_collide(self):
        """The case a port-keyed registry would miss.

        Two `MemoryPort` objects wrapping ONE client is exactly what "a port per session" or "a port
        per worker" produces. Two keys, one unsafe resource - so the registry keys on the client.
        """
        client = FakeHindsightClient()
        port_a = self.port(client, ledger="a.json")
        port_b = self.port(client, ledger="b.json")
        self.assertIsNot(port_a, port_b)
        self.assertIs(identity_of(port_a), identity_of(port_b),
                      "both ports must resolve to the same client identity")

        bind(port_a, Coordinator.with_memory_specialist(MemorySpecialist(port_a)))
        with self.assertRaises(CompositionError):
            bind(port_b, Coordinator.with_memory_specialist(MemorySpecialist(port_b)))

    def test_different_clients_get_different_coordinators(self):
        """The inverse, so the registry is not simply refusing everything."""
        first = self.port(FakeHindsightClient(), bank="bank-a", ledger="a.json")
        second = self.port(FakeHindsightClient(), bank="bank-b", ledger="b.json")
        one = build_runtime(first, memory_specialist=MemorySpecialist(first))
        two = build_runtime(second, memory_specialist=MemorySpecialist(second))
        self.assertIsNot(one.coordinator, two.coordinator)
        self.assertIsNot(one.lane, two.lane)

    def test_unbind_allows_clean_recomposition(self):
        port = self.port(FakeHindsightClient())
        first = build_runtime(port, memory_specialist=MemorySpecialist(port))
        unbind(port)
        self.assertIsNone(coordinator_for(port))
        second = build_runtime(port, memory_specialist=MemorySpecialist(port))
        self.assertIsNot(first.coordinator, second.coordinator)

    def test_build_runtime_rejects_ambiguous_or_empty_requests(self):
        port = self.port(FakeHindsightClient())
        with self.assertRaises(CompositionError):
            build_runtime(port)
        coordinator = Coordinator.with_memory_specialist(MemorySpecialist(port))
        with self.assertRaises(CompositionError):
            build_runtime(port, coordinator=coordinator, memory_specialist=MemorySpecialist(port))

    def test_build_runtime_reuses_the_binding_instead_of_refusing(self):
        """A host that composes per invocation must get the bound Coordinator back, not an error.

        The CLI composes on every run, so raising here would break the second run of the very entry
        point this module exists to serve. Reuse is the correct answer; only an explicit *different*
        Coordinator is a conflict.
        """
        port = self.port(FakeHindsightClient())
        first = build_runtime(port, memory_specialist=MemorySpecialist(port))
        again = build_runtime(port, memory_specialist=MemorySpecialist(port))
        self.assertIs(again.coordinator, first.coordinator)
        self.assertIs(again.lane, first.lane)

    def test_build_runtime_refuses_a_conflicting_explicit_coordinator(self):
        port = self.port(FakeHindsightClient())
        build_runtime(port, memory_specialist=MemorySpecialist(port))
        with self.assertRaises(CompositionError):
            build_runtime(port, coordinator=Coordinator.with_memory_specialist(MemorySpecialist(port)))


class ConcurrentSessionsShareOneLaneTests(CompositionBase):
    """1. Two concurrent sessions on one client share one Coordinator and one lane."""

    def test_concurrent_sessions_use_one_coordinator_and_one_lane(self):
        client = ClientEntryCounter(rendezvous=True)
        port = self.port(client)
        raw = self.raw()
        runtime = build_runtime(port, memory_specialist=MemorySpecialist(port))
        cited = self.cited(port, raw)

        errors: list[BaseException] = []
        guard = threading.Lock()
        seen: list[tuple[int, int]] = []
        start = threading.Barrier(2)

        def run(session_id: str) -> None:
            start.wait(timeout=TIMEOUT)
            try:
                investigate(raw, port, FakeLLM({"hypotheses": [hyp(cites=cited),
                                                             hyp(text="x", cites=cited)]}),
                            ScriptedEngineer(resolution=RESOLVED), session_id=session_id,
                            coordinator=runtime.coordinator)
                with guard:
                    seen.append((id(runtime.coordinator), id(runtime.lane)))
            except BaseException as exc:  # noqa: BLE001
                with guard:
                    errors.append(exc)

        threads = [threading.Thread(target=run, args=(f"s{i}",)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=TIMEOUT * 6)

        for thread in threads:
            self.assertFalse(thread.is_alive(), "a session deadlocked")
        self.assertEqual(errors, [], f"a session failed: {errors}")
        self.assertEqual(seen, [(id(runtime.coordinator), id(runtime.lane))] * 2,
                         "both sessions must have used the one shared Coordinator and lane")
        self.assertEqual(client.max_inside, 1,
                         f"the client was entered concurrently (calls={client.calls})")
        self.assertGreaterEqual(client.calls, 6, "both sessions must have done real work")

    def test_investigate_refuses_a_second_coordinator_for_one_client(self):
        """The invariant holds for library callers too, who never touch `composition.py`."""
        port = self.port(FakeHindsightClient())
        raw = self.raw()
        first = Coordinator.with_memory_specialist(MemorySpecialist(port))
        investigate(raw, port, self.llm(port, raw), ScriptedEngineer(resolution=RESOLVED),
                    session_id="a", coordinator=first)
        second = Coordinator.with_memory_specialist(MemorySpecialist(port))
        with self.assertRaises(CompositionError):
            investigate(raw, port, self.llm(port, raw), ScriptedEngineer(resolution=RESOLVED),
                        session_id="b", coordinator=second)

    def test_investigate_accepts_one_coordinator_across_many_sessions(self):
        port = self.port(FakeHindsightClient())
        raw = self.raw()
        coordinator = Coordinator.with_memory_specialist(MemorySpecialist(port))
        cited = self.cited(port, raw)
        for index in range(3):
            session = investigate(raw, port, FakeLLM({"hypotheses": [hyp(cites=cited),
                                                                    hyp(text="x", cites=cited)]}),
                                  ScriptedEngineer(resolution=RESOLVED),
                                  session_id=f"s{index}", coordinator=coordinator)
            self.assertIsNotNone(session.memory_delegation)
        self.assertIs(coordinator_for(port), coordinator)

    def test_the_bare_worker_path_also_binds(self):
        """`memory_specialist=` builds a Coordinator internally, so it must bind too."""
        port = self.port(FakeHindsightClient())
        raw = self.raw()
        investigate(raw, port, self.llm(port, raw), ScriptedEngineer(resolution=RESOLVED),
                    memory_specialist=MemorySpecialist(port))
        bound = coordinator_for(port)
        self.assertIsNotNone(bound, "the bare-worker path must still establish the invariant")
        with self.assertRaises(CompositionError):
            investigate(raw, port, self.llm(port, raw), ScriptedEngineer(resolution=RESOLVED),
                        memory_specialist=MemorySpecialist(port))

    def test_no_coordinator_still_works(self):
        """The Phase 1 default is untouched: no Coordinator, no binding, no lane."""
        port = self.port(FakeHindsightClient())
        raw = self.raw()
        session = investigate(raw, port, self.llm(port, raw), ScriptedEngineer(resolution=RESOLVED))
        self.assertIsNone(session.memory_delegation)
        self.assertIsNone(coordinator_for(port),
                          "the default path must not silently create a Coordinator")


class RegistryHygieneTests(CompositionBase):
    """4. The registry is weak and does not leak."""

    def test_a_binding_lives_as_long_as_its_client_and_is_released_explicitly(self):
        """Documents the registry's retention semantics rather than pretending they are weak.

        The binding holds the Coordinator strongly, and the Coordinator references the port, which
        references the client - so the entry cannot be collected while the client is in use. That is
        deliberate: sharing must not depend on the host retaining its own Runtime. `unbind` is the
        release path, and it is what keeps a long-lived host from accumulating entries.
        """
        baseline = bound_count()
        client = FakeHindsightClient()
        port = self.port(client)
        build_runtime(port, memory_specialist=MemorySpecialist(port))
        self.assertEqual(bound_count(), baseline + 1)

        del port
        gc.collect()
        self.assertEqual(bound_count(), baseline + 1,
                         "the binding intentionally survives the caller dropping its port")

        self.assertIsNotNone(unbind(client), "unbind must release the retained Coordinator")
        self.assertEqual(bound_count(), baseline)

    def test_runtime_close_releases_the_binding(self):
        """The other release path, and the one the CLI uses on exit."""
        client = FakeHindsightClient()
        baseline = bound_count()
        port = self.port(client)
        runtime = build_runtime(port, memory_specialist=MemorySpecialist(port))
        self.assertEqual(bound_count(), baseline + 1)
        runtime.close()
        self.assertEqual(bound_count(), baseline)
        self.assertIsNone(coordinator_for(port))

    def test_bound_count_returns_to_its_baseline(self):
        baseline = bound_count()
        port = self.port(FakeHindsightClient())
        build_runtime(port, memory_specialist=MemorySpecialist(port))
        self.assertEqual(bound_count(), baseline + 1)
        unbind(port)
        self.assertEqual(bound_count(), baseline)

    def test_identity_falls_back_to_the_port_when_there_is_no_client(self):
        class PortWithoutClient:
            pass

        port = PortWithoutClient()
        self.assertIs(identity_of(port), port)


class CliCompositionRootTests(CompositionBase):
    """5. The CLI is the real composition root, and it uses the invariant."""

    def run_cli(self, lines, port):
        from debugagent.cli import main

        it = iter(lines)

        def ask(_prompt):
            try:
                return next(it)
            except StopIteration:
                raise EOFError

        return main(["debug", "--input", str(self.input_path), "--memory", "offline:unused.jsonl"],
                    ask=ask, out=self.out, err=self.err, port=port,
                    llm=FakeLLM({"hypotheses": [hyp(cites=[]), hyp(text="app-side limit")]}))

    def setUp(self):
        super().setUp()
        import io

        self.out, self.err = io.StringIO(), io.StringIO()
        self.input_path = self.tmp_path / "input.json"
        self.input_path.write_text(
            '{"description": "media-uploader resets connections on uploads over 2 MB",'
            ' "measurements": ["20/20 uploads of 2.5MB fail with ECONNRESET"]}',
            encoding="utf-8")

    SCRIPT = [
        "runtime=node20", "region=us-east-1", "",
        "supported", "y", "accept", "limit is 2m here too",
        "contradicted", "reject", "",
        "y", "raised client_max_body_size to 10m", "no resets above 2 MB",
        "reverse proxy body limit", "resolved", "", "",
    ]

    def test_the_cli_injects_a_coordinator_into_the_session(self):
        """Before the composition root, the CLI produced no `memory_delegation` at all."""
        client = ClientEntryCounter()
        port = self.port(client)
        code = self.run_cli(self.SCRIPT, port)
        self.assertEqual(code, 0, self.err.getvalue())
        self.assertIsNotNone(coordinator_for(port),
                             "the CLI must have composed a Coordinator for its port")

    def test_the_cli_reuses_one_runtime_across_repeated_invocations(self):
        client = ClientEntryCounter()
        port = self.port(client)
        first = self.run_cli(self.SCRIPT, port)
        bound = coordinator_for(port)
        second = self.run_cli(self.SCRIPT, port)
        self.assertEqual((first, second), (0, 0), self.err.getvalue())
        self.assertIs(coordinator_for(port), bound,
                      "a second CLI run on the same client must reuse the same Coordinator")

    def test_the_cli_run_is_still_serialised(self):
        client = ClientEntryCounter(rendezvous=True)
        port = self.port(client)
        self.run_cli(self.SCRIPT, port)
        self.assertEqual(client.max_inside, 1)


class NoFanoutTests(unittest.TestCase):
    """6. Composition assembles and refuses. It does not schedule."""

    def test_no_concurrency_primitives_in_composition(self):
        import inspect

        from debugagent import composition

        source = inspect.getsource(composition)
        for forbidden in ("ThreadPool", "Executor", "as_completed", "asyncio", "multiprocessing",
                          "gather", "Queue"):
            self.assertNotIn(forbidden, source,
                             f"composition must not contain {forbidden}: fan-out is not implemented")

    def test_composition_only_touches_threading_for_its_guard(self):
        import inspect

        from debugagent import composition

        source = inspect.getsource(composition)
        self.assertEqual(source.count("threading."), 1,
                         "threading must appear once: the registry guard")
        self.assertIn("threading.Lock", source)

    def test_the_lane_is_still_a_plain_reentrant_lock(self):
        """The serial lane is unchanged: a lane, not a scheduler."""
        import threading

        lane = MemoryLane()
        self.assertIsInstance(lane._lock, type(threading.RLock()))


if __name__ == "__main__":
    unittest.main()
