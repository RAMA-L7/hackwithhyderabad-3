"""P4 regression tests: the Memory Specialist worker.

ADR 001 phase P4: "Memory Specialist only, single task, serial."

The tests pin the three properties the worker exists to guarantee:

  - MEMORY informs, never verifies: no path from this worker into evidence construction, and
    observations are labelled as past cases rather than current-system facts.
  - Failure is never "nothing found": a clean empty recall is a SUCCESS, an unreachable backend is a
    FAILURE carrying a kind. Collapsing them would tell the engineer memory is empty when it is down.
  - Nothing authorizes: retention is refused by the worker itself, because an engineering outcome is
    the engineer's decision, not a worker's.

Plus the P4 scope boundary: single task, serial. No scheduler, no pool, no fan-out.

Deterministic: no sleeps, no threads, no network. A scripted fake port drives every case.
"""

from __future__ import annotations

import unittest

import support
from debugagent.agents.memory_specialist import (
    MAX_CASE_EXCERPT_CHARS,
    MEMORY_SPECIALIST,
    MemorySpecialist,
    build_memory_specialist_task,
)
from debugagent.agents.registry import authorize
from debugagent.agents.tasks import SubAgentResult, TaskSpec, TaskSpecError
from debugagent.pipeline.memory_port import MemoryFailure

VIEW = {
    "candidates": [
        {"case_id": "seed-001", "relevance_class": "relevant", "outcome": "resolved",
         "text": "orders-api 502 above 2MB was an upstream proxy body limit"},
    ],
    "abstention": {"reason": "none"},
}

MULTI_VIEW = {
    "candidates": [
        {"case_id": "seed-001", "relevance_class": "relevant", "outcome": "resolved",
         "text": "proxy body limit"},
        {"case_id": "seed-002", "relevance_class": "contradictory", "outcome": "workaround",
         "text": "a client bug instead"},
    ],
    "abstention": {"reason": "none"},
}

EMPTY_VIEW = {"candidates": [], "abstention": {"reason": "no case above the usable floor"}}


class FakePort:
    """Scripted MemoryPort. Records calls so a test can assert what did and did not happen."""

    def __init__(self, view=None, *, fail=None):
        self.view = view if view is not None else VIEW
        self.fail = fail
        self.queries: list[str] = []
        self.retained: list[dict] = []

    def recall_and_classify(self, query: str) -> dict:
        self.queries.append(query)
        if self.fail is not None:
            raise self.fail
        return self.view

    def retain(self, case: dict) -> dict:
        self.retained.append(case)
        return {"retained": True, "reason": "should not be reached", "validated": True,
                "memory_case_id": "x", "case_key": "k"}


def spec(task_id="t1", signature="orders api 502", objective=None, **overrides):
    return build_memory_specialist_task(
        task_id=task_id, case_signature=signature,
        objective=objective or "recall similar past debugging cases", **overrides)


class ConstructionTests(unittest.TestCase):
    def test_tool_surface_comes_from_the_registry(self):
        worker = MemorySpecialist(FakePort())
        self.assertEqual(worker.agent_id, MEMORY_SPECIALIST)
        self.assertEqual(worker.allowed_tools, ("hindsight_recall", "hindsight_get_facts"))
        self.assertEqual(worker.model, "primary")

    def test_unknown_agent_is_refused_at_construction(self):
        with self.assertRaises(Exception):
            MemorySpecialist(FakePort(), agent="not_a_registered_agent")

    def test_worker_holds_no_evidence_dependency(self):
        """Structural, not instructional: the module must not be able to build evidence."""
        import inspect

        from debugagent.agents import memory_specialist

        source = inspect.getsource(memory_specialist)
        for forbidden in ("build_evidence", "add_facts", "from debugagent.pipeline.evidence",
                          "import evidence", "verify("):
            self.assertNotIn(forbidden, source, f"worker must not reach evidence: {forbidden}")

    def test_worker_is_constructed_with_a_port_not_a_store(self):
        import inspect

        from debugagent.agents import memory_specialist

        source = inspect.getsource(memory_specialist)
        self.assertIn("MemoryPort", source)
        self.assertNotIn("HindsightMemoryStore", source,
                         "the worker must use the seam, not the vendor adapter")


class SuccessfulRecallTests(unittest.TestCase):
    def test_recall_returns_success_with_observations(self):
        worker = MemorySpecialist(FakePort())
        result = worker.execute(spec())
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "success")
        self.assertIsNone(result.failure_kind)
        self.assertEqual(len(result.observations), 1)

    def test_result_is_a_subagent_result(self):
        result = MemorySpecialist(FakePort()).execute(spec())
        self.assertIsInstance(result, SubAgentResult)
        self.assertEqual(result.task_id, "t1")
        self.assertEqual(result.agent, MEMORY_SPECIALIST)

    def test_observations_carry_memory_provenance(self):
        result = MemorySpecialist(FakePort()).execute(spec())
        for observation in result.observations:
            self.assertEqual(observation.kind, "memory")
            self.assertTrue(observation.source.startswith("memory:"), observation.source)

    def test_multiple_candidates_become_multiple_observations(self):
        result = MemorySpecialist(FakePort(MULTI_VIEW)).execute(spec())
        self.assertEqual(len(result.observations), 2)
        refs = {o.ref for o in result.observations}
        self.assertEqual(refs, {"seed-001", "seed-002"})

    def test_the_case_signature_is_queried(self):
        port = FakePort()
        MemorySpecialist(port).execute(spec(signature="payments worker oom"))
        self.assertEqual(port.queries, ["payments worker oom"])

    def test_result_serialises_and_revalidates(self):
        """The Coordinator receives structured data, so it must survive the P1 schema."""
        result = MemorySpecialist(FakePort()).execute(spec())
        again = SubAgentResult.from_dict(result.to_dict())
        self.assertEqual(again.to_dict(), result.to_dict())

    def test_worker_never_retains(self):
        port = FakePort()
        MemorySpecialist(port).execute(spec())
        self.assertEqual(port.retained, [], "a recall task must not retain anything")


class TrustBoundaryTests(unittest.TestCase):
    """Proposal != Evidence != Knowledge != Authorization, enforced structurally."""

    def test_observation_is_labelled_as_past_not_current(self):
        result = MemorySpecialist(FakePort()).execute(spec())
        content = result.observations[0].content
        self.assertIn("MEMORY", content)
        self.assertIn("not current-system evidence", content)

    def test_recalled_environment_is_not_restated_as_current(self):
        """A past environment is not the current environment, so it must not be asserted here."""
        view = {"candidates": [{"case_id": "seed-001", "relevance_class": "relevant",
                                "outcome": "resolved", "text": "t",
                                "environment": {"service": "orders-api", "region": "eu-west-1"}}],
                "abstention": {"reason": "none"}}
        content = MemorySpecialist(FakePort(view)).execute(spec()).observations[0].content
        self.assertNotIn("eu-west-1", content)
        self.assertNotIn("environment", content.lower())

    def test_worker_returns_a_proposal_not_a_decision(self):
        result = MemorySpecialist(FakePort()).execute(spec())
        self.assertNotIn("verified", result.to_dict().get("status", ""))
        self.assertEqual(result.status, "success", "a memory finding is never a verification")

    def test_excerpt_is_bounded_and_marked(self):
        view = {"candidates": [{"case_id": "seed-001", "relevance_class": "relevant",
                                "outcome": "resolved", "text": "x" * 5000}],
                "abstention": {"reason": "none"}}
        content = MemorySpecialist(FakePort(view)).execute(spec()).observations[0].content
        self.assertLessEqual(len(content), MAX_CASE_EXCERPT_CHARS)
        self.assertIn("MEMORY", content, "a clipped excerpt must still say what it is")


class EmptyResultTests(unittest.TestCase):
    """Failure is never 'nothing found' - so a clean empty recall must be a SUCCESS."""

    def test_empty_recall_is_success_not_failure(self):
        result = MemorySpecialist(FakePort(EMPTY_VIEW)).execute(spec())
        self.assertEqual(result.status, "success")
        self.assertIsNone(result.failure_kind)

    def test_empty_recall_still_reports_an_observation(self):
        result = MemorySpecialist(FakePort(EMPTY_VIEW)).execute(spec())
        self.assertEqual(len(result.observations), 1)
        self.assertIn("no relevant past case", result.observations[0].content)

    def test_empty_recall_carries_the_abstention_reason(self):
        result = MemorySpecialist(FakePort(EMPTY_VIEW)).execute(spec())
        self.assertIn("usable floor", result.observations[0].content)

    def test_empty_and_unreachable_are_distinguishable(self):
        empty = MemorySpecialist(FakePort(EMPTY_VIEW)).execute(spec(task_id="a"))
        broken = MemorySpecialist(
            FakePort(EMPTY_VIEW, fail=MemoryFailure("unavailable", "bank down"))
        ).execute(spec(task_id="b"))
        self.assertEqual(empty.status, "success")
        self.assertEqual(broken.status, "failed")


class MemoryFailureTests(unittest.TestCase):
    """A memory failure is reported explicitly, never swallowed into an empty result."""

    def test_backend_failure_becomes_a_failed_result(self):
        worker = MemorySpecialist(FakePort(fail=MemoryFailure("unavailable", "bank unreachable")))
        result = worker.execute(spec())
        self.assertEqual(result.status, "failed")
        self.assertFalse(result.ok)

    def test_failure_carries_a_kind(self):
        worker = MemorySpecialist(FakePort(fail=MemoryFailure("unavailable", "down")))
        self.assertEqual(worker.execute(spec()).failure_kind, "unavailable")

    def test_auth_failure_maps_to_auth(self):
        worker = MemorySpecialist(FakePort(fail=MemoryFailure("auth", "bad key")))
        self.assertEqual(worker.execute(spec()).failure_kind, "auth")

    def test_schema_failure_maps_to_schema(self):
        worker = MemorySpecialist(FakePort(fail=MemoryFailure("schema", "bad view")))
        self.assertEqual(worker.execute(spec()).failure_kind, "schema")

    def test_ambiguous_maps_into_the_agent_vocabulary(self):
        """P3-2C's `ambiguous` has no agent-vocabulary member, so it maps to `unavailable`."""
        worker = MemorySpecialist(FakePort(fail=MemoryFailure("ambiguous", "outcome unknown")))
        result = worker.execute(spec())
        self.assertEqual(result.failure_kind, "unavailable")
        self.assertIn("ambiguous", result.failure_detail,
                      "the precise kind must survive in the detail")

    def test_persist_maps_into_the_agent_vocabulary(self):
        worker = MemorySpecialist(FakePort(fail=MemoryFailure("persist", "ledger write failed")))
        result = worker.execute(spec())
        self.assertEqual(result.failure_kind, "unavailable")
        self.assertIn("persist", result.failure_detail)

    def test_failure_detail_names_the_underlying_kind(self):
        worker = MemorySpecialist(FakePort(fail=MemoryFailure("unavailable", "bank unreachable")))
        self.assertIn("memory unavailable", worker.execute(spec()).failure_detail)

    def test_a_failed_result_carries_no_observations(self):
        worker = MemorySpecialist(FakePort(fail=MemoryFailure("unavailable", "down")))
        self.assertEqual(worker.execute(spec()).observations, ())

    def test_the_real_pipeline_memory_failure_is_caught(self):
        """The worker must handle the exception the actual pipeline port raises.

        It cannot import that class - the P1 rule forbids the agents package importing the Phase 1
        pipeline - so failures are matched structurally on `kind`. This test uses the real
        `HindsightMemoryPort` so the structural match is verified against the real type, not a fake.
        """
        import tempfile
        from pathlib import Path

        from debugagent.memory.hindsight_store import HindsightMemoryStore
        from debugagent.pipeline.memory_adapter import HindsightMemoryPort
        from support import FakeHindsightClient, memory_config

        class Down(FakeHindsightClient):
            def recall(self, **kwargs):
                raise RuntimeError("bank unreachable")

        port = HindsightMemoryPort(
            HindsightMemoryStore(memory_config(Path(tempfile.mkdtemp())), client=Down()))
        result = MemorySpecialist(port).execute(spec())
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "unavailable")

    def test_a_non_memory_exception_propagates(self):
        """A bug must not be reported to the engineer as a memory outage."""

        class Buggy:
            def recall_and_classify(self, query):
                raise KeyError("a bug, not a memory failure")

            def retain(self, case):
                return {}

        with self.assertRaises(KeyError):
            MemorySpecialist(Buggy()).execute(spec())

    def test_execute_never_raises_for_a_memory_failure(self):
        """The Coordinator must receive a result it can act on, not an exception."""
        worker = MemorySpecialist(FakePort(fail=MemoryFailure("unavailable", "down")))
        try:
            result = worker.execute(spec())
        except MemoryFailure:  # pragma: no cover - the failure mode being prevented
            self.fail("execute() must not propagate a MemoryFailure")
        self.assertEqual(result.status, "failed")

    def test_repeated_failures_are_reported_each_time(self):
        worker = MemorySpecialist(FakePort(fail=MemoryFailure("unavailable", "down")))
        for index in range(3):
            self.assertEqual(worker.execute(spec(task_id=f"t{index}")).status, "failed")


class RetentionIsNotAWorkerActionTests(unittest.TestCase):
    """Retention is an engineer decision, so the worker refuses it rather than performing it."""

    def test_retain_objective_is_refused(self):
        worker = MemorySpecialist(FakePort())
        result = worker.execute(spec(task_id="t9", objective="retain this resolved case"))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "schema")

    def test_retain_refusal_names_the_boundary(self):
        worker = MemorySpecialist(FakePort())
        detail = worker.execute(spec(task_id="t9", objective="store the outcome")).failure_detail
        self.assertIn("engineer decision", detail)

    def test_retain_refusal_never_touches_the_port(self):
        port = FakePort()
        MemorySpecialist(port).execute(spec(task_id="t9", objective="retain this case"))
        self.assertEqual(port.retained, [])
        self.assertEqual(port.queries, [], "a refused task must not call the backend")

    def test_unrecognised_objective_is_a_schema_failure(self):
        worker = MemorySpecialist(FakePort())
        result = worker.execute(spec(task_id="t8", objective="do something unspecified"))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "schema")


class AuthorisationTests(unittest.TestCase):
    """P1's seam still governs every task this worker runs."""

    def test_spec_goes_through_the_p1_build_path(self):
        task = spec()
        self.assertIsInstance(task, TaskSpec)
        self.assertEqual(task.depth, 1)
        self.assertEqual(task.agent, MEMORY_SPECIALIST)

    def test_unauthorised_tool_is_refused_before_any_port_call(self):
        port = FakePort()
        worker = MemorySpecialist(port)
        bad = TaskSpec(task_id="x", agent=MEMORY_SPECIALIST, delegated_by="coordinator",
                       objective="recall", context=spec().context,
                       allowed_tools=("read",), model="primary", depth=1)
        with self.assertRaises(Exception):
            worker.execute(bad)
        self.assertEqual(port.queries, [], "authorisation must run before the port")

    def test_task_tool_cannot_appear_in_a_spec(self):
        with self.assertRaises(TaskSpecError):
            spec_with_task = {"task_id": "t", "agent": MEMORY_SPECIALIST,
                              "delegated_by": "coordinator", "objective": "recall",
                              "context": {"case_signature": "x"},
                              "allowed_tools": ["task"], "model": "primary", "depth": 1}
            from debugagent.agents.registry import build_task_spec
            build_task_spec(spec_with_task)

    def test_depth_above_one_is_refused(self):
        with self.assertRaises(Exception):
            from debugagent.agents.registry import build_task_spec
            build_task_spec({"task_id": "t", "agent": MEMORY_SPECIALIST,
                             "delegated_by": "coordinator", "objective": "recall",
                             "context": {"case_signature": "x"},
                             "allowed_tools": ["hindsight_recall"], "model": "primary",
                             "depth": 2})

    def test_task_requesting_a_tool_this_agent_lacks_is_refused(self):
        """The P1 guarantee that does hold: tools are checked against the agent definition."""
        bad = TaskSpec(task_id="t", agent=MEMORY_SPECIALIST, delegated_by="coordinator",
                       objective="recall", context=spec().context,
                       allowed_tools=("read",), model="primary", depth=1)
        with self.assertRaises(Exception):
            authorize(bad)

    def test_worker_refuses_a_task_addressed_to_another_agent(self):
        """The registry authorises a spec against whichever agent it names; the worker must
        still refuse a task the Coordinator sent to the wrong worker, rather than run a
        Patch Generator objective against the memory port."""
        port = FakePort()
        worker = MemorySpecialist(port)
        misplaced = TaskSpec(task_id="t", agent="patch_generator", delegated_by="coordinator",
                             objective="recall", context=spec().context,
                             allowed_tools=("read", "diff"), model="primary", depth=1)
        self.assertEqual(authorize(misplaced).agent, "patch_generator")  # the seam allows it
        with self.assertRaises(PermissionError):
            worker.execute(misplaced)
        self.assertEqual(port.queries, [], "nothing may reach the port")

    def test_registry_authorises_any_registered_agent_not_just_this_one(self):
        """Documents the seam's actual boundary, so it is not over-claimed later.

        `authorize()` validates a TaskSpec against whichever agent it names; it does not bind a
        spec to a worker instance. Binding a task to THIS worker is the Coordinator's job when it
        dispatches, and is why `MemorySpecialist.execute` also refuses any spec whose agent is not
        the one it was constructed for.
        """
        other = TaskSpec(task_id="t", agent="patch_generator", delegated_by="coordinator",
                         objective="draft a patch", context=spec().context,
                         allowed_tools=("read", "diff"), model="primary", depth=1)
        self.assertEqual(authorize(other).agent, "patch_generator")


class SerialSingleTaskTests(unittest.TestCase):
    """P4 is single-task and serial. P5 fan-out must not have leaked in here."""

    def test_no_concurrency_primitives_in_the_worker(self):
        import inspect

        from debugagent.agents import memory_specialist

        source = inspect.getsource(memory_specialist)
        for forbidden in ("ThreadPool", "threading", "concurrent.futures", "asyncio",
                          "as_completed", "multiprocessing"):
            self.assertNotIn(forbidden, source, f"P5 fan-out must not be in P4: {forbidden}")

    def test_tasks_run_one_at_a_time_in_order(self):
        port = FakePort()
        worker = MemorySpecialist(port)
        for index in range(3):
            worker.execute(spec(task_id=f"t{index}"))
        self.assertEqual(port.queries, ["orders api 502"] * 3)

    def test_execute_returns_one_result_per_task(self):
        worker = MemorySpecialist(FakePort())
        results = [worker.execute(spec(task_id=f"t{i}")) for i in range(3)]
        self.assertEqual([r.task_id for r in results], ["t0", "t1", "t2"])
        self.assertTrue(all(isinstance(r, SubAgentResult) for r in results))


if __name__ == "__main__":
    unittest.main()
