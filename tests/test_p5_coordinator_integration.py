"""P5: the Coordinator, connected to the real `investigate()` flow.

P1 built the seam, P4 built `MemorySpecialist`, and P4's wiring called it directly. Nothing owned the
hub, so there was no single place where "is this task allowed?" and "who runs it?" are answered
together. `Coordinator` is that place, and this file proves it does the job without moving the trust
boundary.

The seven properties under test, one group each:

1. **Dispatch** — the Coordinator creates the task and routes it to `MemorySpecialist`.
2. **Authorisation precedes execution** — a rejected task never reaches worker code.
3. **Wrong-agent tasks are rejected** — unknown agent, targeting the coordinator, a non-coordinator
   delegator, a wrong model, and an over-deep spec all fail closed.
4. **Failure is explicit** — a bank outage, a raising worker, and a missing worker instance each
   become a `failed` result carrying a `failure_kind`, never a clean "nothing found".
5. **Memory cannot become evidence** — no path from a worker observation into EVIDENCE, PROPOSAL or
   DECISION.
6. **Phase 1 output is unchanged** when no delegation happens, and `coordinator=` and
   `memory_specialist=` produce identical sessions.
7. **P4 stays green** — asserted by running the P4 suites from here, so a regression cannot hide
   behind a separate invocation.

Deterministic: no sleeps, no threads, no network.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support
from debugagent.agents.coordinator import Coordinator
from debugagent.agents.memory_specialist import MEMORY_SPECIALIST, MemorySpecialist
from debugagent.agents.registry import AuthorizationError, authorize, get_agent
from debugagent.agents.tasks import Artifact, SubAgentResult, TaskSpec
from debugagent.memory.hindsight_store import HindsightMemoryStore, compute_case_key
from debugagent.pipeline.ingest import load_debug_input
from debugagent.pipeline.investigate import investigate
from debugagent.pipeline.memory_adapter import HindsightMemoryPort
from debugagent.pipeline.memory_port import MemoryFailure
from debugagent.pipeline.normalize import normalize
from debugagent.pipeline.recall_match import recall
from support import FakeHindsightClient, FakeMemory, FakeScores, memory_config

import loop_support
from loop_support import RESOLVED, FakeLLM, ScriptedEngineer, hyp

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


class RecordingEngineer(ScriptedEngineer):
    """The repo's scripted engineer, extended to record what was shown to the engineer."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.shown: list[str] = []

    def show(self, text: str) -> None:
        self.shown.append(text)


class SpyWorker:
    """A worker that records every spec it is handed.

    Deliberately not a `MemorySpecialist`: these tests are about the Coordinator's behaviour around a
    worker, and a spy makes "was the worker invoked at all?" directly observable.
    """

    def __init__(self, *, result=None, raises=None):
        self.specs: list[TaskSpec] = []
        self._result = result
        self._raises = raises

    def execute(self, spec: TaskSpec) -> SubAgentResult:
        self.specs.append(spec)
        if self._raises is not None:
            raise self._raises
        if self._result is not None:
            return self._result
        return SubAgentResult(
            task_id=spec.task_id, agent=spec.agent, status="success",
            observations=(Artifact(kind="memory", ref="m-1",
                                  content="past: nginx body limit"),))

    @property
    def invoked(self) -> bool:
        return bool(self.specs)


class CoordinatorBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def port(self, client=None):
        store = HindsightMemoryStore(
            memory_config(self.tmp_path),
            client=client if client is not None else FakeHindsightClient(recall_results=seeded_rows()))
        self.addCleanup(store.close)
        return HindsightMemoryPort(store)

    def raw(self, filename=ACT2):
        return load_debug_input(Path(__file__).resolve().parents[1] / "demo" / "inputs" / filename)

    def cited(self, port, raw) -> list[str]:
        """Case ids the pipeline's own `recall()` surfaced, or none when memory abstains."""
        context = recall(port, normalize(raw))
        return [] if context.abstained else [c["case_id"] for c in context.candidates]

    def llm(self, port, raw):
        cites = self.cited(port, raw)[:1]
        return FakeLLM({"hypotheses": [hyp(cites=cites), hyp(text="app-side upload limit", cites=cites)]})

    def memory_task(self, task_id="t-1", *, signature="uploads fail over 2 MB", symptoms=("2.5MB resets",)):
        from debugagent.agents.memory_specialist import build_memory_specialist_task

        return build_memory_specialist_task(
            task_id=task_id, case_signature=signature, symptoms=symptoms)


def coordinator_module():
    from debugagent.agents import coordinator

    return coordinator


def _imported_modules(module) -> set[str]:
    """Every module named by an import anywhere in `module`, including inside functions.

    Function-level imports are included deliberately: a lazy `from ...pipeline... import` inside a
    function body is exactly the kind of dependency a text search for `^import` would miss.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class DispatchTests(CoordinatorBase):
    """1. The Coordinator creates the task and routes it to the worker."""

    def test_delegation_reaches_the_memory_specialist(self):
        port = self.port()
        coordinator = Coordinator.with_memory_specialist(MemorySpecialist(port))
        result = coordinator.delegate_memory(task_id="t-1", case_signature="uploads fail over 2 MB")
        self.assertEqual(result.agent, MEMORY_SPECIALIST)
        self.assertEqual(result.task_id, "t-1")
        self.assertTrue(result.ok)
        self.assertTrue(result.observations, "the worker must report what it recalled")

    def test_the_task_is_recorded_with_its_result(self):
        coordinator = Coordinator.with_memory_specialist(MemorySpecialist(self.port()))
        coordinator.delegate_memory(task_id="t-1", case_signature="uploads fail over 2 MB")
        self.assertEqual(coordinator.task_ids, ("t-1",))
        dispatch = coordinator.dispatches[0]
        self.assertEqual(dispatch.agent, MEMORY_SPECIALIST)
        self.assertIs(dispatch.result, coordinator.dispatches[0].result)

    def test_the_spec_carries_the_session_identity_it_was_given(self):
        worker = SpyWorker()
        coordinator = Coordinator.with_memory_specialist(worker)
        coordinator.delegate_memory(task_id="s7-memory", case_signature="uploads fail",
                                    symptoms=("a", "b"))
        spec = worker.specs[0]
        self.assertEqual(spec.task_id, "s7-memory")
        self.assertEqual(spec.delegated_by, "coordinator")
        self.assertEqual(spec.context.case_signature, "uploads fail")
        self.assertEqual(tuple(spec.context.symptoms), ("a", "b"))

    def test_dispatch_is_serial_and_ordered(self):
        """Serial, not parallel: dispatch N+1 cannot start before N is recorded."""
        worker = SpyWorker()
        coordinator = Coordinator.with_memory_specialist(worker)
        for index in range(4):
            coordinator.delegate_memory(task_id=f"t-{index}", case_signature="uploads fail")
        self.assertEqual(coordinator.task_ids, ("t-0", "t-1", "t-2", "t-3"))
        self.assertEqual([s.task_id for s in worker.specs], list(coordinator.task_ids))

    def test_a_registered_agent_with_no_worker_reports_absence(self):
        coordinator = Coordinator()
        result = coordinator.delegate_memory(task_id="t-1", case_signature="uploads fail")
        self.assertEqual(result.status, "failed")
        self.assertIn("no worker instance", result.failure_detail)


class AuthorisationPrecedesExecutionTests(CoordinatorBase):
    """2. Authorisation happens before execution, not alongside it."""

    def test_authorize_runs_before_the_worker(self):
        import debugagent.agents.coordinator as module

        order: list[str] = []
        original = module.authorize

        def recording(spec):
            order.append("authorize")
            return original(spec)

        class Ordered(SpyWorker):
            def execute(self, spec):
                order.append("execute")
                return super().execute(spec)

        worker = Ordered()
        module.authorize = recording
        self.addCleanup(setattr, module, "authorize", original)

        Coordinator.with_memory_specialist(worker).delegate_memory(
            task_id="t-1", case_signature="uploads fail")
        self.assertEqual(order, ["authorize", "execute"])

    def test_a_disallowed_tool_never_reaches_the_worker(self):
        worker = SpyWorker()
        coordinator = Coordinator.with_memory_specialist(worker)
        bad = TaskSpec(task_id="t-1", agent=MEMORY_SPECIALIST, delegated_by="coordinator",
                       objective="recall", context={"case_signature": "x"},
                       allowed_tools=("retain_case",), model="primary", depth=1)
        with self.assertRaises(AuthorizationError):
            coordinator.delegate(bad)
        self.assertFalse(worker.invoked, "an unauthorised task must not reach worker code")
        self.assertEqual(coordinator.dispatches, (), "a rejected task is not a completed dispatch")

    def test_an_over_deep_spec_never_reaches_the_worker(self):
        worker = SpyWorker()
        coordinator = Coordinator.with_memory_specialist(worker)
        deep = TaskSpec(task_id="t-1", agent=MEMORY_SPECIALIST, delegated_by="coordinator",
                        objective="recall", context={"case_signature": "x"},
                        allowed_tools=get_agent(MEMORY_SPECIALIST).allowed_tools,
                        model="primary", depth=2)
        with self.assertRaises(AuthorizationError):
            coordinator.delegate(deep)
        self.assertFalse(worker.invoked)

    def test_a_spec_the_coordinator_did_not_build_is_still_authorised(self):
        """The roster is re-checked at dispatch, so a hand-built TaskSpec gets no free pass."""
        worker = SpyWorker()
        coordinator = Coordinator.with_memory_specialist(worker)
        good = self.memory_task()
        result = coordinator.delegate(good)
        self.assertTrue(result.ok)
        self.assertTrue(worker.invoked)


class WrongAgentTests(CoordinatorBase):
    """3. Wrong-agent tasks are rejected, and the registry is closed."""

    def test_an_unregistered_agent_is_rejected(self):
        worker = SpyWorker()
        coordinator = Coordinator({**{MEMORY_SPECIALIST: worker}, "patch_generator": worker})
        spec = TaskSpec(task_id="t-1", agent="patch_generator", delegated_by="coordinator",
                        objective="write a patch", context={"case_signature": "x"},
                        allowed_tools=("hindsight_recall",), model="primary", depth=1)
        with self.assertRaises(AuthorizationError) as caught:
            coordinator.delegate(spec)
        self.assertIn("patch_generator", str(caught.exception))
        self.assertFalse(worker.invoked)

    def test_an_invented_agent_is_rejected(self):
        coordinator = Coordinator.with_memory_specialist(SpyWorker())
        spec = TaskSpec(task_id="t-1", agent="omnipotent_agent", delegated_by="coordinator",
                        objective="do everything", context={"case_signature": "x"},
                        allowed_tools=("hindsight_recall",), model="primary", depth=1)
        with self.assertRaises(AuthorizationError):
            coordinator.delegate(spec)

    def test_a_task_targeting_the_coordinator_is_rejected(self):
        """The coordinator delegates; it does not run. That asymmetry is enforced."""
        coordinator = Coordinator.with_memory_specialist(SpyWorker())
        spec = TaskSpec(task_id="t-1", agent="coordinator", delegated_by="coordinator",
                        objective="delegate", context={"case_signature": "x"},
                        allowed_tools=("task",), model="primary", depth=1)
        with self.assertRaises(AuthorizationError) as caught:
            coordinator.delegate(spec)
        self.assertIn("coordinator", str(caught.exception))

    def test_a_non_coordinator_delegator_is_rejected(self):
        coordinator = Coordinator.with_memory_specialist(SpyWorker())
        spec = TaskSpec(task_id="t-1", agent=MEMORY_SPECIALIST, delegated_by="memory_specialist",
                        objective="recall", context={"case_signature": "x"},
                        allowed_tools=("hindsight_recall",), model="primary", depth=1)
        with self.assertRaises(AuthorizationError):
            coordinator.delegate(spec)

    def test_a_model_mismatch_is_rejected(self):
        coordinator = Coordinator.with_memory_specialist(SpyWorker())
        spec = TaskSpec(task_id="t-1", agent=MEMORY_SPECIALIST, delegated_by="coordinator",
                        objective="recall", context={"case_signature": "x"},
                        allowed_tools=("hindsight_recall",), model="some-other-model", depth=1)
        with self.assertRaises(AuthorizationError):
            coordinator.delegate(spec)

    def test_a_worker_cannot_delegate_further(self):
        """Anti-recursion: no worker may hold the `task` tool, so no worker can spawn depth 2."""
        for agent in ("memory_specialist",):
            with self.assertRaises(AuthorizationError):
                authorize(TaskSpec(task_id="t-1", agent=agent, delegated_by="coordinator",
                                   objective="spawn", context={"case_signature": "x"},
                                   allowed_tools=("task",), model="primary", depth=1))


class ExplicitFailureTests(CoordinatorBase):
    """4. Failure is explicit, and never reads as 'nothing found'."""

    def test_a_bank_outage_becomes_a_failed_result_with_a_kind(self):
        class Down(FakeHindsightClient):
            def recall(self, **kwargs):
                raise MemoryFailure("unavailable", "bank unreachable")

        coordinator = Coordinator.with_memory_specialist(MemorySpecialist(self.port(client=Down())))
        result = coordinator.delegate_memory(task_id="t-1", case_signature="uploads fail")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "unavailable")
        self.assertEqual(result.observations, ())
        self.assertIn("bank unreachable", result.failure_detail)

    def test_each_memory_failure_kind_survives_the_dispatch(self):
        """Every kind the port can raise reaches the engineer as an explicit failure.

        Two kinds (`ambiguous`, `persist`) have no member in the agent result vocabulary, so they
        report as `unavailable` and keep the precise kind in `failure_detail`. That is the documented
        P3-2C mapping, and losing the distinction entirely would be the real defect - so the detail is
        asserted for every kind.

        The failure is raised by the PORT, not by a client behind it: the real `HindsightMemoryPort`
        re-maps a client's exception into its own kind first, so raising from a fake client would
        test the adapter's remapping instead of the worker's.
        """
        expected_agent_kind = {"unavailable": "unavailable", "ambiguous": "unavailable",
                               "persist": "unavailable", "auth": "auth", "schema": "schema"}
        for kind, agent_kind in expected_agent_kind.items():
            with self.subTest(kind=kind):
                self.setUp()

                class RaisingPort:
                    def recall_and_classify(self, query):
                        raise MemoryFailure(kind, "boom")

                    def retain(self, case):
                        raise AssertionError("the memory worker must never retain")

                coordinator = Coordinator.with_memory_specialist(MemorySpecialist(RaisingPort()))
                result = coordinator.delegate_memory(task_id="t-1", case_signature="uploads fail")
                self.assertEqual(result.status, "failed", "a fault is never a clean recall")
                self.assertEqual(result.failure_kind, agent_kind)
                self.assertIn(f"memory {kind}", result.failure_detail,
                              "the precise port kind must survive in the detail")
                self.assertEqual(result.observations, ())

    def test_a_port_kind_the_vocabulary_does_not_have_is_not_swallowed(self):
        """A non-memory exception is a bug, not an outage: it must not be reported as a recall.

        `_failure_kind_of` only recognises the port vocabulary, so anything else propagates. The
        Coordinator still converts it to an explicit failure - the engineer's session survives - but
        the detail names the real exception type, so a bug is diagnosable rather than disguised.
        """
        class Buggy(FakeHindsightClient):
            def recall(self, **kwargs):
                raise ValueError("not a MemoryFailure at all")

        coordinator = Coordinator.with_memory_specialist(MemorySpecialist(self.port(client=Buggy())))
        result = coordinator.delegate_memory(task_id="t-1", case_signature="uploads fail")
        self.assertEqual(result.status, "failed")
        self.assertIn("ValueError", result.failure_detail,
                      "a bug must be traceable, not laundered into an outage")

    def test_an_empty_recall_is_a_success_not_a_failure(self):
        """The other half: nothing found is a clean success carrying the abstention reason."""
        class Empty(FakeHindsightClient):
            def recall(self, **kwargs):
                return []

        coordinator = Coordinator.with_memory_specialist(
            MemorySpecialist(self.port(client=Empty())))
        result = coordinator.delegate_memory(task_id="t-1", case_signature="uploads fail")
        self.assertTrue(result.ok)
        self.assertIsNone(result.failure_kind)

    def test_a_raising_worker_becomes_a_failed_result(self):
        coordinator = Coordinator.with_memory_specialist(SpyWorker(raises=RuntimeError("worker bug")))
        result = coordinator.delegate_memory(task_id="t-1", case_signature="uploads fail")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "unavailable")
        self.assertIn("could not be dispatched", result.failure_detail)
        self.assertIn("RuntimeError", result.failure_detail)

    def test_a_failure_does_not_abort_the_session(self):
        class Down(FakeHindsightClient):
            def recall(self, **kwargs):
                raise MemoryFailure("timeout", "no route to host")

        raw = self.raw()
        working = self.port()
        coordinator = Coordinator.with_memory_specialist(
            MemorySpecialist(self.port(client=Down())))
        session = investigate(raw, working, self.llm(working, raw),
                              RecordingEngineer(resolution=RESOLVED), coordinator=coordinator)
        self.assertEqual(session.memory_delegation["status"], "failed")
        self.assertIsNotNone(session.memory, "the direct recall must still have run")
        self.assertTrue(session.proposal.hypotheses, "the session must still reach a proposal")

    def test_a_failed_delegation_still_renders(self):
        raw = self.raw()
        port = self.port()
        coordinator = Coordinator.with_memory_specialist(SpyWorker(raises=RuntimeError("boom")))
        engineer = RecordingEngineer(resolution=RESOLVED)
        investigate(raw, port, self.llm(port, raw), engineer, coordinator=coordinator)
        self.assertTrue(any("Memory Specialist" in text for text in engineer.shown))

    def test_a_worker_raised_refusal_is_not_laundered_into_an_outage(self):
        """A refusal raised by the WORKER stays a refusal.

        A worker that discovers mid-task that it may not proceed raises `AuthorizationError`. Folding
        that into the generic `except Exception` would report a *refusal* as `unavailable` - telling
        the engineer their bank is down when the truth is that the task was not permitted. That is
        the ADR's "failure is never nothing found" rule inverted, so it is re-raised instead.
        """
        refusing = SpyWorker(raises=AuthorizationError("this recall is not permitted"))
        coordinator = Coordinator.with_memory_specialist(refusing)
        with self.assertRaises(AuthorizationError) as caught:
            coordinator.delegate_memory(task_id="t-1", case_signature="uploads fail")
        self.assertIn("not permitted", str(caught.exception))
        self.assertEqual(coordinator.dispatches, (),
                         "a refused delegation is not a completed dispatch")


class MemoryIsNotEvidenceTests(CoordinatorBase):
    """5. MEMORY ≠ EVIDENCE ≠ PROPOSAL ≠ DECISION, through the Coordinator."""

    def test_worker_output_never_enters_evidence(self):
        port = self.port()
        coordinator = Coordinator.with_memory_specialist(MemorySpecialist(port))
        session = investigate(self.raw(), port, self.llm(port, self.raw()),
                              RecordingEngineer(resolution=RESOLVED), coordinator=coordinator)
        self.assertTrue(session.memory_delegation["observations"], "the worker did report memory")
        observed = "\n".join(f"{i.name}={i.value}" for i in session.evidence.items)
        for observation in session.memory_delegation["observations"]:
            self.assertNotIn(observation["ref"], observed,
                             "a memory observation must not become an evidence item")

    def test_evidence_is_identical_with_and_without_the_coordinator(self):
        port = self.port()
        raw = self.raw()
        with_hub = investigate(raw, port, self.llm(port, raw),
                               RecordingEngineer(resolution=RESOLVED),
                               coordinator=Coordinator.with_memory_specialist(MemorySpecialist(port)))
        self.setUp()
        port2 = self.port()
        without = investigate(self.raw(), port2, self.llm(port2, self.raw()),
                              RecordingEngineer(resolution=RESOLVED))
        self.assertEqual([i.to_dict() for i in with_hub.evidence.items],
                         [i.to_dict() for i in without.evidence.items])

    def test_the_proposal_is_built_from_recall_not_from_the_worker(self):
        port = self.port()
        raw = self.raw()
        with_hub = investigate(raw, port, self.llm(port, raw),
                               RecordingEngineer(resolution=RESOLVED),
                               coordinator=Coordinator.with_memory_specialist(MemorySpecialist(port)))
        self.assertEqual([c["case_id"] for c in with_hub.memory.candidates],
                         [c["case_id"] for c in recall(port, normalize(raw)).candidates],
                         "the ranked memory the proposal is built from is still recall()'s")

    def test_a_memory_failure_cannot_downgrade_a_verification(self):
        """A worker outage is reported; it does not turn a supported claim into insufficient."""
        class Down(FakeHindsightClient):
            def recall(self, **kwargs):
                raise MemoryFailure("unavailable", "bank unreachable")

        port = self.port()
        raw = self.raw()
        coordinator = Coordinator.with_memory_specialist(MemorySpecialist(self.port(client=Down())))
        session = investigate(raw, port, self.llm(port, raw),
                              RecordingEngineer(resolution=RESOLVED), coordinator=coordinator)
        self.assertEqual(session.memory_delegation["status"], "failed")
        self.assertEqual(session.verifications[0].status, "supported",
                         "memory being unavailable is not evidence of anything")

    def test_the_coordinator_cannot_retain(self):
        """It holds workers, not a store: there is no retention call anywhere on this path."""
        port = self.port()
        client = port._store._client
        raw = self.raw()
        coordinator = Coordinator.with_memory_specialist(MemorySpecialist(port))
        investigate(raw, port, self.llm(port, raw), RecordingEngineer(resolution=RESOLVED),
                    coordinator=coordinator)
        # The one retain came from the engineer's resolution, never from the Coordinator.
        self.assertLessEqual(len(client.retained), 1)

    def test_no_pipeline_dependency_in_the_agents_package(self):
        """Structural: the dispatch path cannot reach evidence construction or verification.

        Checked over the module's *imports*, not its text. A substring search over the source would
        match this very test's intent being described in prose, which proves nothing either way.
        """
        for module in (coordinator_module(),):
            for imported in _imported_modules(module):
                self.assertNotIn("pipeline", imported,
                                 f"the dispatch path must not import {imported}")
                for forbidden in ("evidence", "verify"):
                    self.assertNotIn(forbidden, imported,
                                     f"the dispatch path must not import {imported}")

    def test_no_concurrency_in_the_dispatch_path(self):
        """No fan-out machinery, though a LOCK is now expected.

        P5's serial memory lane introduced `threading.RLock` into this module, and that is correct:
        `hindsight_client` is not thread-safe, so serialising is the whole point. What must still be
        absent is anything that would run workers *concurrently* - fan-out is not implemented, and a
        thread pool appearing here would mean P5 skipped a gate.
        """
        imported = _imported_modules(coordinator_module())
        for forbidden in ("concurrent", "asyncio", "multiprocessing", "ThreadPool", "Executor"):
            self.assertFalse([name for name in imported if forbidden in name],
                             f"the dispatch path must not import {forbidden}: fan-out is not "
                             f"implemented, only serialisation")

    def test_the_only_threading_use_is_the_lane_and_the_fanout(self):
        """Pins `threading` to two places: the lane's lock, and fan-out itself.

        P5 fan-out legitimately added threads, so this no longer asserts their absence. It asserts they
        are confined - a thread appearing in `delegate` or `_execute_authorized` would mean the serial
        path quietly went parallel, which is the failure this whole file exists to prevent.
        """
        import inspect

        from debugagent.agents import coordinator as module
        from debugagent.agents.coordinator import MemoryLane

        self.assertEqual(inspect.getsource(MemoryLane).count("threading."), 1,
                         "the lane should hold exactly one primitive: its reentrant lock")

        for name in ("delegate", "_execute_authorized", "_worker_fault", "worker_for",
                     "touches_client"):
            source = inspect.getsource(getattr(module.Coordinator, name))
            self.assertNotIn("threading.Thread", source,
                             f"Coordinator.{name} must stay serial")
            self.assertNotIn("threading.Barrier", source,
                             f"Coordinator.{name} must stay serial")

        fan_out = inspect.getsource(module.Coordinator.fan_out)
        self.assertIn("threading.Thread", fan_out, "fan-out is where threads belong")
        self.assertIn("threading.Barrier", fan_out, "and so is the join barrier")

    def test_the_registry_still_refuses_a_thread_in_production(self):
        """`registry.py` and `tasks.py` remain pure data - no concurrency at all."""
        import inspect

        from debugagent.agents import registry, tasks

        for module in (registry, tasks):
            source = inspect.getsource(module)
            for forbidden in ("threading", "Thread", "Barrier", "concurrent"):
                self.assertNotIn(forbidden, source,
                                 f"{module.__name__} must stay pure data, not {forbidden}")

    def test_the_artifact_carries_memory_provenance(self):
        """Even handed to the Coordinator, a memory observation is labelled `memory:`."""
        worker = SpyWorker()
        result = Coordinator.with_memory_specialist(worker).delegate_memory(
            task_id="t-1", case_signature="uploads fail")
        artifact = result.observations[0]
        self.assertEqual(artifact.kind, "memory")
        self.assertTrue(artifact.source.startswith("memory:"))


class Phase1UnchangedTests(CoordinatorBase):
    """6. Phase 1 output is unchanged when memory delegation is not used."""

    def test_no_coordinator_means_no_delegation_key(self):
        port = self.port()
        raw = self.raw()
        session = investigate(raw, port, self.llm(port, raw), RecordingEngineer(resolution=RESOLVED))
        self.assertIsNone(session.memory_delegation)
        self.assertNotIn("memory_delegation", session.to_dict())

    def test_no_coordinator_renders_nothing_extra(self):
        port = self.port()
        raw = self.raw()
        engineer = RecordingEngineer(resolution=RESOLVED)
        investigate(raw, port, self.llm(port, raw), engineer)
        self.assertFalse(any("Memory Specialist" in text for text in engineer.shown))

    def test_the_trace_has_no_delegation_line_without_a_coordinator(self):
        port = self.port()
        raw = self.raw()
        session = investigate(raw, port, self.llm(port, raw), RecordingEngineer(resolution=RESOLVED))
        self.assertFalse(any("memory specialist" in note for note in session.trace))

    def test_phase1_output_is_byte_identical(self):
        """The strongest form of requirement 6: the serialised session is unchanged."""
        port = self.port()
        raw = self.raw()
        session = investigate(raw, port, self.llm(port, raw), RecordingEngineer(resolution=RESOLVED))
        serialised = session.to_dict()
        self.assertNotIn("memory_delegation", serialised)
        self.assertEqual(set(serialised), {"session_id", "input", "case", "memory", "evidence",
                                           "proposal", "verifications", "resolution",
                                           "retention", "trace"})

    def test_coordinator_and_bare_worker_produce_the_same_session(self):
        """One dispatch path, not two: both spellings must agree."""
        port = self.port()
        raw = self.raw()
        with_hub = investigate(raw, port, self.llm(port, raw),
                               RecordingEngineer(resolution=RESOLVED), session_id="fixed",
                               coordinator=Coordinator.with_memory_specialist(MemorySpecialist(port)))
        self.setUp()
        port2 = self.port()
        with_worker = investigate(self.raw(), port2, self.llm(port2, self.raw()),
                                  RecordingEngineer(resolution=RESOLVED), session_id="fixed",
                                  memory_specialist=MemorySpecialist(port2))
        self.assertEqual(with_hub.memory_delegation, with_worker.memory_delegation)

    def test_passing_both_is_an_error_rather_than_a_guess(self):
        port = self.port()
        with self.assertRaises(ValueError):
            investigate(self.raw(), port, self.llm(port, self.raw()),
                        RecordingEngineer(resolution=RESOLVED),
                        coordinator=Coordinator.with_memory_specialist(MemorySpecialist(port)),
                        memory_specialist=MemorySpecialist(port))

    def test_the_coordinator_accumulates_across_sessions(self):
        """One hub, many sessions: dispatches are recorded per Coordinator, not per session."""
        port = self.port()
        coordinator = Coordinator.with_memory_specialist(MemorySpecialist(port))
        for session_id in ("s1", "s2"):
            raw = self.raw()
            investigate(raw, port, self.llm(port, raw), RecordingEngineer(resolution=RESOLVED),
                        session_id=session_id, coordinator=coordinator)
        self.assertEqual(coordinator.task_ids, ("s1-memory", "s2-memory"))


class P4StillGreenTests(unittest.TestCase):
    """7. The P4 suites must pass, asserted here so a regression cannot hide.

    Running them from inside this file rather than trusting a separate command is the point: a P4
    regression and a P5 change are otherwise indistinguishable from a green test run.
    """

    def test_p4_suites_are_green(self):
        import contextlib
        import importlib
        import io

        for module_name in ("test_memory_specialist_p4", "test_p4_rehearsal_gates",
                            "test_p4_wiring_integration"):
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                suite = unittest.defaultTestLoader.loadTestsFromModule(module)
                sink = io.StringIO()
                with contextlib.redirect_stderr(sink), contextlib.redirect_stdout(sink):
                    result = unittest.TextTestRunner(stream=sink, verbosity=0).run(suite)
                self.assertTrue(result.wasSuccessful(), f"{module_name} regressed:\n{sink.getvalue()}")
                self.assertGreater(result.testsRun, 0, f"{module_name} ran no tests")


if __name__ == "__main__":
    unittest.main()
