"""P5 evaluation: ADK orchestration over the existing Coordinator, safety invariants intact.

The question this answers is whether Google ADK can become the orchestration layer for the three P6
workers without weakening anything this repository has established. The answer under test is "ADK can
own the TOPOLOGY, and must not own the safety properties" - so these tests hold both halves:

1. **The invariants still hold through ADK.** Authorisation still precedes dispatch, memory still
   serialises through the one `MemoryLane`, one worker's failure does not hide another's result, a
   refusal is still a refusal, and the result order is still the declared one.
2. **ADK really is doing the orchestration.** The graph fans out and joins, and the nodes genuinely
   overlap - including a control that proves the overlap comes from the bridge rather than from luck.

The whole module skips cleanly when `google-adk` is not installed, because it is an optional
evaluation dependency and the rest of the suite must not depend on the answer.

Two findings from building the bridge are pinned here rather than left in a report:

- **A blocking node body serialises ADK's parallel graph.** Measured 0.63s wall at a concurrency of 1
  for two blocking nodes, against 0.37s and 2 once the work is offloaded. The graph would promise
  parallelism the implementation did not deliver, which is the worst kind of orchestration bug. The
  bridge therefore offloads with `asyncio.to_thread`, and `ParallelismTests` proves it is load-bearing.
- **`FanOutResult.results` excludes refusals**, so a node recording only results would DISCARD a
  refusal and report a clean run that silently skipped a task. The collector records refusals too.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

import support
from debugagent.adk_bridge import (
    ADK_IMPORT_ERROR,
    TaskCollector,
    adk_available,
    build_investigation_workflow,
    build_worker_node,
    require_adk,
    run_investigation_workflow,
    run_p6_fan_out,
)
from debugagent.agents.code_log_verifier import CODE_LOG_VERIFIER, CodeLogVerifier, build_verifier_task
from debugagent.agents.coordinator import Coordinator
from debugagent.agents.memory_specialist import MEMORY_SPECIALIST, MemorySpecialist, build_memory_specialist_task
from debugagent.agents.patch_generator import PATCH_GENERATOR, PatchGenerator, build_patch_task
from debugagent.agents.registry import AuthorizationError, get_agent
from debugagent.agents.tasks import SubAgentResult, TaskSpec
from debugagent.composition import build_repository_runtime
from debugagent.memory.hindsight_store import HindsightMemoryStore
from debugagent.pipeline.memory_adapter import HindsightMemoryPort
from support import FakeHindsightClient, memory_config

TIMEOUT = 5.0
GATE_TIMEOUT = 1.0
LOG = "app/server.log"
SOURCE = "src/upload.py"
CURRENT = "MAX_BODY = 2 * 1024 * 1024\n"
PROPOSED = "MAX_BODY = 10 * 1024 * 1024\n"


@unittest.skipUnless(adk_available(), f"google-adk is not importable here ({ADK_IMPORT_ERROR})")
class AdkTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "repo"
        (self.root / "app").mkdir(parents=True)
        (self.root / "src").mkdir()
        (self.root / LOG).write_text("ECONNRESET above 2MB\n", encoding="utf-8")
        (self.root / SOURCE).write_text(CURRENT, encoding="utf-8")
        self.addCleanup(self._tmp.cleanup)
        self._memtmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._memtmp.cleanup)
        self.repository = build_repository_runtime(self.root)
        store = HindsightMemoryStore(
            memory_config(Path(self._memtmp.name), bank_id="adk",
                          ledger_path=Path(self._memtmp.name) / "l.json"),
            client=FakeHindsightClient())
        self.addCleanup(store.close)
        self.coordinator = self.repository.coordinator
        self.coordinator.register(MEMORY_SPECIALIST, MemorySpecialist(HindsightMemoryPort(store)))

    def groups(self, memory=1, verifier=1, patch=1, prefix=""):
        groups = {}
        if memory:
            groups[MEMORY_SPECIALIST] = [
                build_memory_specialist_task(task_id=f"{prefix}m-{i}", case_signature="uploads fail")
                for i in range(memory)]
        if verifier:
            groups[CODE_LOG_VERIFIER] = [
                build_verifier_task(task_id=f"{prefix}v-{i}", case_signature="uploads fail",
                                    targets=[LOG]) for i in range(verifier)]
        if patch:
            groups[PATCH_GENERATOR] = [
                build_patch_task(task_id=f"{prefix}p-{i}", case_signature="uploads fail",
                                 target=SOURCE, proposed=PROPOSED) for i in range(patch)]
        return groups

    def run_groups(self, groups, **kwargs):
        return run_p6_fan_out(self.coordinator, groups, **kwargs)


class AvailabilityTests(unittest.TestCase):
    def test_availability_is_reported_rather_than_assumed(self):
        self.assertIsInstance(adk_available(), bool)

    def test_the_rest_of_the_package_does_not_need_adk(self):
        """Importing the bridge with ADK absent must not break anything that imports it."""
        import debugagent.adk_bridge as bridge

        self.assertTrue(hasattr(bridge, "ADK_IMPORT_ERROR"))
        if not adk_available():
            with self.assertRaises(RuntimeError) as caught:
                require_adk()
            self.assertIn("optional evaluation dependency", str(caught.exception))


@unittest.skipUnless(adk_available(), f"google-adk is not importable here ({ADK_IMPORT_ERROR})")
class TopologyTests(AdkTestBase):
    def test_the_graph_fans_out_and_joins(self):
        workflow, _collector, _runner = build_investigation_workflow(
            coordinator=self.coordinator, groups=self.groups())
        names = [node.name for node in workflow.graph.nodes]
        self.assertEqual(names, ["__START__", MEMORY_SPECIALIST, CODE_LOG_VERIFIER,
                                 PATCH_GENERATOR, "join"])
        edges = {(edge.from_node.name, edge.to_node.name) for edge in workflow.graph.edges}
        self.assertIn(("__START__", MEMORY_SPECIALIST), edges)
        self.assertIn((MEMORY_SPECIALIST, "join"), edges,
                      "every worker must reach the join barrier")

    def test_an_empty_group_is_dropped_rather_than_dispatched(self):
        workflow, _c, _r = build_investigation_workflow(
            coordinator=self.coordinator, groups=self.groups(patch=0))
        self.assertNotIn(PATCH_GENERATOR, [n.name for n in workflow.graph.nodes],
                         "an empty node would satisfy the join without dispatching anything")

    def test_a_workflow_with_no_tasks_is_refused(self):
        with self.assertRaises(ValueError):
            build_investigation_workflow(coordinator=self.coordinator,
                                        groups={MEMORY_SPECIALIST: []})

    def test_a_non_identifier_group_name_is_refused_with_a_reason(self):
        """ADK validates node names as Python identifiers; the roster's ids already are."""
        spec = build_patch_task(task_id="p-1", case_signature="x", target=SOURCE, proposed=PROPOSED)
        with self.assertRaises(ValueError) as caught:
            build_investigation_workflow(coordinator=self.coordinator,
                                         groups={"patch-generator": [spec]})
        self.assertIn("Python identifiers", str(caught.exception))
        self.assertIn("code_log_verifier", str(caught.exception),
                      "the message should point at the registered ids that do work")


@unittest.skipUnless(adk_available(), f"google-adk is not importable here ({ADK_IMPORT_ERROR})")
class InvariantTests(AdkTestBase):
    """The safety properties, re-proved through the ADK path."""

    def test_end_to_end_all_three_workers_produce_results(self):
        collector = self.run_groups(self.groups())
        self.assertTrue(collector.all_joined())
        self.assertEqual(collector.summary()["by_status"], {"success": 3})
        self.assertEqual(collector.task_ids, ("m-0", "v-0", "p-0"))
        self.assertTrue(collector.result_for("p-0").observations[0].content.startswith(
            "PROPOSED PATCH"))

    def test_authorisation_still_precedes_dispatch(self):
        """A refused task must not reach a worker, and the refusal must be reported, not dropped."""
        bad = TaskSpec(task_id="bad", agent=CODE_LOG_VERIFIER, delegated_by="coordinator",
                       objective="inspect", context={"case_signature": "x"},
                       allowed_tools=("hindsight_recall",), model="primary", depth=1)
        collector = self.run_groups({CODE_LOG_VERIFIER: [bad]})
        self.assertEqual(collector.summary()["by_status"], {"refused": 1})
        self.assertIsInstance(collector.refusal_for("bad"), AuthorizationError)
        self.assertEqual(collector.ordered_results(), (),
                         "a refusal must not appear as a result")

    def test_a_refusal_does_not_hide_its_siblings(self):
        bad = TaskSpec(task_id="bad", agent=CODE_LOG_VERIFIER, delegated_by="coordinator",
                       objective="inspect", context={"case_signature": "x"},
                       allowed_tools=("hindsight_recall",), model="primary", depth=1)
        good = build_verifier_task(task_id="ok", case_signature="uploads fail", targets=[LOG])
        collector = self.run_groups({CODE_LOG_VERIFIER: [bad, good]})
        self.assertEqual(collector.summary()["by_status"], {"refused": 1, "success": 1})
        self.assertTrue(collector.result_for("ok").ok)
        with self.assertRaises(AuthorizationError):
            collector.raise_for_refusals()

    def test_memory_still_serialises_through_the_lane(self):
        lane_before = self.coordinator.lane.entered
        collector = self.run_groups(self.groups(memory=3, verifier=2, patch=2))
        self.assertEqual(collector.summary()["total"], 7)
        # Three memory tasks took the lane; the four non-memory tasks did not.
        self.assertEqual(self.coordinator.lane.entered - lane_before, 3)

    def test_one_worker_failure_does_not_hide_the_others(self):
        class FailingPort:
            """Makes the verifier fail while memory and patcher still work."""

            def read(self, path):
                raise RuntimeError("repository unavailable")

            def glob(self, pattern):
                return ()

            def grep(self, pattern, paths):
                return ()

        # A deliberate reconfiguration, so the swap is declared rather than silent.
        self.coordinator.unregister(CODE_LOG_VERIFIER)
        self.coordinator.register(CODE_LOG_VERIFIER, CodeLogVerifier(FailingPort()))
        collector = self.run_groups(self.groups())
        by_status = collector.summary()["by_status"]
        self.assertEqual(by_status.get("success"), 2, "memory and patcher must still succeed")
        self.assertEqual(by_status.get("failed"), 1, "the verifier failure must be visible")

    def test_result_ordering_is_the_declared_order_not_the_completion_order(self):
        """The graph completes out of order; the collector must not report it that way.

        Gating WITHIN one node is not enough to test this: `fan_out` already returns declared order, so
        a single node's tasks land in the collector in the right order regardless. The nodes themselves
        have to finish out of order, so the verifier node below waits for the patcher to complete and
        therefore records LAST, while it is declared in the middle.
        """
        patched = threading.Event()

        class SlowVerifier:
            def execute(self, spec):
                if not patched.wait(timeout=TIMEOUT):
                    raise AssertionError("the patcher never ran")
                return SubAgentResult(task_id=spec.task_id, agent=spec.agent, status="success")

        class SignallingPatcher:
            def execute(self, spec):
                patched.set()
                return SubAgentResult(task_id=spec.task_id, agent=spec.agent, status="success")

        for agent in (CODE_LOG_VERIFIER, PATCH_GENERATOR):
            self.coordinator.unregister(agent)
        self.coordinator.register(CODE_LOG_VERIFIER, SlowVerifier())
        self.coordinator.register(PATCH_GENERATOR, SignallingPatcher())

        collector = self.run_groups(self.groups(prefix="ord-"))
        self.assertEqual(collector.task_ids,
                         ("ord-m-0", "ord-v-0", "ord-p-0"),
                         "declaration order is the contract")
        self.assertEqual([r.task_id for r in collector.ordered_results()],
                         ["ord-m-0", "ord-v-0", "ord-p-0"],
                         "the verifier node finished last but must still report in declared order")
        self.assertEqual(collector.node_for("ord-v-0"), CODE_LOG_VERIFIER)

    def test_a_task_with_neither_result_nor_refusal_is_a_bug_not_a_gap(self):
        collector = TaskCollector()
        collector.declare([build_memory_specialist_task(task_id="ghost", case_signature="x")])
        with self.assertRaises(AssertionError):
            collector.ordered_results()


@unittest.skipUnless(adk_available(), f"google-adk is not importable here ({ADK_IMPORT_ERROR})")
class ParallelismTests(AdkTestBase):
    """ADK must really be orchestrating, and the bridge must not defeat it."""

    def test_nodes_genuinely_overlap(self):
        barrier = threading.Barrier(3)
        running = {"now": 0, "max": 0}
        lock = threading.Lock()

        class Counting:
            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                attribute = getattr(self._inner, name)
                if not callable(attribute):
                    return attribute

                def wrapper(*args, **kwargs):
                    with lock:
                        running["now"] += 1
                        running["max"] = max(running["max"], running["now"])
                    try:
                        barrier.wait(timeout=GATE_TIMEOUT)
                        return attribute(*args, **kwargs)
                    finally:
                        with lock:
                            running["now"] -= 1

                return wrapper

        for agent in (CODE_LOG_VERIFIER, PATCH_GENERATOR):
            self.coordinator.unregister(agent)
        self.coordinator.register(CODE_LOG_VERIFIER,
                                  CodeLogVerifier(Counting(self.repository.source_port)))
        self.coordinator.register(PATCH_GENERATOR,
                                  PatchGenerator(Counting(self.repository.patch_port)))
        self.run_groups(self.groups(memory=0, verifier=1, patch=1))
        self.assertGreaterEqual(running["max"], 2,
                                "the three ADK nodes must be able to run at the same time")

    def test_a_blocking_node_body_would_serialise_the_graph(self):
        ...
        import asyncio
        import time

        from google.adk.workflow import START as AdkStart
        from google.adk.workflow import JoinNode, Node
        from google.adk.workflow._graph import Graph
        from google.adk import Workflow
        from google.adk.events import Event
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        block_seconds = 0.20

        class BlockingNode(Node):
            async def run(self, *, ctx, node_input):
                time.sleep(block_seconds)  # blocks the loop, exactly as `fan_out` does
                yield Event(author=self.name,
                            content=types.Content(role="model",
                                                  parts=[types.Part(text="ok")]))

        async def _measure():
            nodes = (BlockingNode(name="a"), BlockingNode(name="b"))
            graph = Graph.from_edge_items([(AdkStart, nodes, JoinNode(name="join"))])
            runner = InMemoryRunner(agent=Workflow(name="w", description="d", graph=graph),
                                    app_name="adk-probe")
            session = await runner.session_service.create_session(
                app_name="adk-probe", user_id="u", state={})
            started = time.perf_counter()
            async for _ in runner.run_async(
                    user_id="u", session_id=session.id,
                    new_message=types.Content(role="user", parts=[types.Part(text="go")])):
                pass
            return time.perf_counter() - started

        elapsed = asyncio.run(_measure())
        # Genuinely parallel would be ~one block. Two blocks means the loop was held.
        self.assertGreater(elapsed, block_seconds * 1.5,
                           "expected a BLOCKING node to serialise the graph; if ADK has become "
                           "truly concurrent, the `to_thread` offload in the bridge can be revisited")


@unittest.skipUnless(adk_available(), f"google-adk is not importable here ({ADK_IMPORT_ERROR})")
class TrustBoundaryTests(AdkTestBase):
    """The boundaries must be identical with and without ADK in the path."""

    def test_capabilities_are_unchanged(self):
        self.assertTrue(get_agent(MEMORY_SPECIALIST).client_access)
        for agent in (CODE_LOG_VERIFIER, PATCH_GENERATOR):
            self.assertFalse(get_agent(agent).client_access)

    def test_the_verifier_still_cannot_reach_memory(self):
        spec = build_verifier_task(task_id="v-1", case_signature="x", targets=[LOG])
        collector = self.run_groups({CODE_LOG_VERIFIER: [spec]})
        result = collector.result_for("v-1")
        self.assertIn("not a verification", result.observations[0].content)
        self.assertEqual(result.observations[0].kind, "log",
                         "provenance must survive the ADK hop")

    def test_the_patcher_still_proposes_without_writing(self):
        before = (self.root / SOURCE).read_bytes()
        collector = self.run_groups({PATCH_GENERATOR: [
            build_patch_task(task_id="p-1", case_signature="x", target=SOURCE, proposed=PROPOSED)]})
        self.assertTrue(collector.result_for("p-1").ok)
        self.assertEqual((self.root / SOURCE).read_bytes(), before)

    def test_a_traversal_target_is_still_refused(self):
        spec = build_verifier_task(task_id="v-1", case_signature="x",
                                   targets=["../outside/secret.txt"])
        collector = self.run_groups({CODE_LOG_VERIFIER: [spec]})
        result = collector.result_for("v-1")
        self.assertEqual(result.failure_kind, "schema")
        self.assertIn("outside the authorised repository", result.failure_detail)

    def test_the_bridge_owns_no_authority_of_its_own(self):
        """The bridge must not authorise, serialise or classify anything itself."""
        import ast
        import inspect
        import textwrap

        import debugagent.adk_bridge as bridge

        tree = ast.parse(textwrap.dedent(inspect.getsource(bridge)))
        called: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                called.add(node.attr)
        for forbidden in ("authorize", "authorize_tool", "get_agent", "build_task_spec",
                          "Artifact", "SubAgentResult("):
            self.assertNotIn(forbidden, called,
                             f"the bridge must not perform {forbidden}: it delegates to the "
                             f"Coordinator, which is the only thing that authorises")

    def test_the_coordinator_still_works_without_adk(self):
        """The evaluation is additive: the non-ADK path is untouched."""
        fan = self.coordinator.fan_out([build_memory_specialist_task(
            task_id="m-direct", case_signature="uploads fail")])
        self.assertTrue(fan.outcomes[0].ok)
        self.assertEqual([o.task_id for o in fan.outcomes], ["m-direct"])

    def test_a_worker_node_dispatches_exactly_its_own_specs(self):
        collector = TaskCollector()
        specs = [build_verifier_task(task_id=f"v-{i}", case_signature="x", targets=[LOG])
                 for i in range(2)]
        node = build_worker_node(name=CODE_LOG_VERIFIER, coordinator=self.coordinator,
                                 specs=specs, collector=collector, timeout=TIMEOUT)
        self.assertEqual(node.specs, tuple(specs))
        self.assertEqual(collector.task_ids, ("v-0", "v-1"),
                         "the node declares its order at construction")


if __name__ == "__main__":
    unittest.main()
