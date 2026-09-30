"""P6: the Code/Log Verifier worker.

The Memory Specialist answers "what happened before". This one answers "what does the CURRENT system
actually say" - and that is where its authority stops. The tests below pin the boundaries that matter:

1. **It observes; it does not verify.** No result may claim a root cause, a confirmation, or a verdict.
2. **It never touches memory.** No Hindsight import, no memory port, and a recalled case injected into
   the task context is REFUSED rather than quietly read.
3. **It uses only the tools it was granted.** A task granting `grep` alone cannot make it open a file,
   even though the worker is capable of reading.
4. **Failure is never "nothing found".** No targets is a `schema` failure; a missing file is an
   observation saying so; a batch where nothing could be read is `partial`, not `success`.
5. **Refusals stay refusals.** Wrong agent, unauthorised tool and over-deep specs are refused before any
   port call, and the P1 distinction between a refusal and a fault survives.

Then the integration half: dispatch through `Coordinator.fan_out`, prove it runs concurrently with
memory work, and prove memory is unaffected.

Determinism: an in-memory `SourcePort`; no filesystem, no network, no sleeps.
"""

from __future__ import annotations

import inspect
import tempfile
import threading
import unittest
from pathlib import Path

import support
from debugagent.agents.code_log_verifier import (
    CODE_LOG_VERIFIER,
    MAX_EXCERPT_CHARS,
    CodeLogVerifier,
    SourceNotFound,
    SourceUnavailable,
    build_verifier_task,
)
from debugagent.agents.coordinator import Coordinator
from debugagent.agents.memory_specialist import MemorySpecialist, build_memory_specialist_task
from debugagent.agents.registry import AuthorizationError, authorize, get_agent
from debugagent.agents.tasks import Artifact, SubAgentResult, TaskSpec, TaskSpecError
from debugagent.memory.hindsight_store import HindsightMemoryStore
from debugagent.pipeline.memory_adapter import HindsightMemoryPort
from support import FakeHindsightClient, memory_config

TIMEOUT = 5.0
GATE_TIMEOUT = 1.0
MEMORY = "memory_specialist"


class FakeSourcePort:
    """An in-memory stand-in for the filesystem, so no test touches a real path."""

    def __init__(self, files: dict[str, str] | None = None, *, glob_result=None,
                 grep_result=None, raises: Exception | None = None):
        self.files = dict(files or {})
        self._glob_result = glob_result
        self._grep_result = grep_result
        self._raises = raises
        self.reads: list[str] = []
        self.globs: list[str] = []
        self.greps: list[tuple[str, tuple[str, ...]]] = []

    def read(self, path: str) -> str:
        if self._raises is not None:
            raise self._raises
        self.reads.append(path)
        if path not in self.files:
            raise SourceNotFound(path)
        return self.files[path]

    def glob(self, pattern: str) -> tuple[str, ...]:
        if self._raises is not None:
            raise self._raises
        self.globs.append(pattern)
        if self._glob_result is not None:
            return tuple(self._glob_result)
        return tuple(sorted(p for p in self.files if pattern in p))

    def grep(self, pattern: str, paths) -> tuple[tuple[str, int, str], ...]:
        if self._raises is not None:
            raise self._raises
        self.greps.append((pattern, tuple(paths)))
        if self._grep_result is not None:
            return tuple(self._grep_result)
        return tuple((path, 1, text) for path in paths
                     if path in self.files and pattern in self.files[path] for text in [self.files[path]])


FILES = {
    "app/server.log": "2026-09-28 ECONNRESET uploads over 2MB\nclient_max_body_size 2m",
    "src/upload.py": "MAX_BODY = 2 * 1024 * 1024",
}


def verifier(**kwargs) -> CodeLogVerifier:
    files = kwargs.pop("files", FILES)
    return CodeLogVerifier(FakeSourcePort(files, **kwargs))


def task(task_id: str = "t-1", *, targets=("app/server.log",), objective: str = "inspect the current system",
         case: str = "uploads fail over 2MB", tools=None) -> TaskSpec:
    spec = build_verifier_task(task_id=task_id, case_signature=case, targets=targets,
                               objective=objective)
    if tools is None:
        return spec
    # Re-authorise with a narrowed tool set, so the spec is legal rather than hand-built.
    return authorize(TaskSpec(task_id=spec.task_id, agent=spec.agent, delegated_by=spec.delegated_by,
                              objective=spec.objective, context=spec.context,
                              allowed_tools=tuple(tools), model=spec.model, depth=spec.depth))


def env_task(task_id: str, environment, *, tools=None, **kwargs) -> TaskSpec:
    """A verifier task carrying tool parameters in its environment, as `WorkerContext` expects."""
    from debugagent.agents.tasks import WorkerContext

    spec = build_verifier_task(task_id=task_id, case_signature="uploads fail over 2MB", **kwargs)
    return authorize(TaskSpec(
        task_id=spec.task_id, agent=spec.agent, delegated_by=spec.delegated_by, objective=spec.objective,
        context=WorkerContext(case_signature=spec.context.case_signature,
                              symptoms=spec.context.symptoms,
                              environment=tuple(environment), artifacts=spec.context.artifacts),
        allowed_tools=spec.allowed_tools if tools is None else tuple(tools),
        model=spec.model, depth=spec.depth))


class ConstructionTests(unittest.TestCase):
    def test_the_roster_identity_and_capability_are_used(self):
        worker = verifier()
        self.assertEqual(worker.agent_id, CODE_LOG_VERIFIER)
        self.assertEqual(worker.allowed_tools, get_agent(CODE_LOG_VERIFIER).allowed_tools)
        self.assertEqual(worker.model, get_agent(CODE_LOG_VERIFIER).model)

    def test_the_roster_records_this_agent_as_non_client(self):
        self.assertFalse(get_agent(CODE_LOG_VERIFIER).client_access,
                         "the Verifier must not be serialised through the memory lane")

    def test_the_worker_declares_no_capability_of_its_own(self):
        self.assertFalse(hasattr(CodeLogVerifier, "client_access"))

    def test_an_unknown_agent_id_is_refused_at_construction(self):
        with self.assertRaises(AuthorizationError):
            CodeLogVerifier(FakeSourcePort(FILES), agent="omnipotent_agent")

    def test_it_uses_only_the_registered_tools(self):
        self.assertEqual(set(verifier().allowed_tools), {"read", "grep", "glob"})


class AuthorityTests(unittest.TestCase):
    """1 and 2: it observes, and it never touches memory."""

    def test_the_module_cannot_reach_memory(self):
        """Structural: this module's IMPORTS name nothing memory-related.

        Scanned over import statements and attribute accesses rather than raw source text, because the
        module's prose deliberately discusses the Hindsight boundary in order to assert it - and a
        substring scan fails on the very sentence stating the property. Imports are the real question
        anyway: a module cannot reach a port it never imported.
        """
        import ast
        import textwrap

        module = inspect.getmodule(CodeLogVerifier)
        tree = ast.parse(textwrap.dedent(inspect.getsource(module)))

        imported: set[str] = set()
        attributes: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Attribute):
                attributes.add(node.attr)

        for forbidden in ("hindsight", "memory_port", "memory_specialist", "memory_adapter"):
            self.assertFalse([name for name in imported if forbidden in name],
                             f"the Verifier must not import anything from {forbidden}")
        for forbidden in ("recall_and_classify", "retain", "get_bank_config", "create_bank"):
            self.assertNotIn(forbidden, attributes,
                             f"the Verifier must not call {forbidden}")
        self.assertIn("debugagent.agents.registry", imported,
                      "sanity: the scan does see this module's real imports")

    def test_it_holds_no_memory_seam(self):
        """The only seam it holds is read-only: no recall, no retain, no client."""
        worker = verifier()
        for forbidden in ("recall_and_classify", "retain", "recall", "get_bank_config"):
            self.assertFalse(hasattr(worker._port, forbidden),
                             f"the Verifier's port must not expose {forbidden}")

    def test_an_observation_never_claims_a_verdict(self):
        """The strongest form of "it observes, it does not verify".

        Checks for the CLAIM rather than one exact disclaimer phrase, so rewording the disclaimer cannot
        quietly remove the property, and adding a verdict word anywhere is caught.
        """
        result = verifier().execute(task())
        self.assertTrue(result.ok)
        for observation in result.observations:
            content = observation.content.lower()
            for forbidden in ("root cause is", "confirms", "confirmed the root cause", "this verifies",
                              "proves that", "the cause is", "verdict", "conclusion:"):
                self.assertNotIn(forbidden, content,
                                 "an observation must not read as a verification")
            self.assertIn("not a verification", content,
                          "the disclaimer must be IN the content, not a renderer convention")
            self.assertIn("current-system observation", content)

    def test_a_recalled_case_in_the_context_is_refused(self):
        """Property 2, enforced in code rather than trusted to the prompt."""
        from debugagent.agents.tasks import WorkerContext

        spec = build_verifier_task(task_id="t-1", case_signature="uploads fail",
                                   targets=["app/server.log"])
        contaminated = authorize(TaskSpec(
            task_id=spec.task_id, agent=spec.agent, delegated_by=spec.delegated_by,
            objective=spec.objective,
            context=WorkerContext(
                case_signature=spec.context.case_signature,
                symptoms=spec.context.symptoms,
                artifacts=spec.context.artifacts + (Artifact(kind="memory", ref="case-abc",
                                                             content="past case: body limit"),)),
            allowed_tools=spec.allowed_tools, model=spec.model, depth=spec.depth))
        result = verifier().execute(contaminated)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "schema")
        self.assertIn("past case is not evidence", result.failure_detail)
        self.assertEqual(result.observations, (),
                         "a refused task must produce no observations at all")

    def test_the_worker_cannot_retain(self):
        """It has no retention verb, and asking for one is a schema failure rather than a silent no-op."""
        result = verifier().execute(task(objective="retain this resolved case"))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "schema")


class ToolAuthorityTests(unittest.TestCase):
    """3: only the tools this task was granted."""

    def test_a_task_without_the_read_tool_does_not_read(self):
        port = FakeSourcePort(FILES)
        worker = CodeLogVerifier(port)
        result = worker.execute(task(objective="inspect the current system", tools=("grep",)))
        self.assertEqual(port.reads, [], "the worker must not read a file it was not granted")
        self.assertEqual(result.status, "partial",
                         "nothing was inspected, so this is not a clean success")
        self.assertIn("did not grant", result.observations[0].content)
        self.assertIn("read", result.failure_detail)

    def test_a_grep_task_without_a_pattern_fails_explicitly(self):
        result = verifier().execute(task(objective="grep for the error"))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "schema")
        self.assertIn("grep_pattern", result.failure_detail)

    def test_grep_requires_the_grep_tool(self):
        result = verifier().execute(env_task("t-1", [("grep_pattern", "ECONNRESET")],
                                            objective="grep", tools=("read",)))
        self.assertEqual(result.status, "failed")
        self.assertIn("did not grant", result.failure_detail)

    def test_glob_requires_the_glob_tool(self):
        result = verifier().execute(env_task("t-1", [("glob_pattern", "*.log")],
                                            objective="glob", tools=("read",)))
        self.assertEqual(result.status, "failed")
        self.assertIn("did not grant", result.failure_detail)

    def test_a_granted_tool_is_actually_used(self):
        port = FakeSourcePort(FILES, glob_result=["app/server.log"])
        CodeLogVerifier(port).execute(env_task("t-1", [("glob_pattern", "*.log")],
                                               objective="glob the logs"))
        self.assertEqual(port.globs, ["*.log"], "the glob tool must actually be called")

        port = FakeSourcePort(FILES)
        CodeLogVerifier(port).execute(env_task("t-2", [("grep_pattern", "ECONNRESET")],
                                               targets=["app/server.log"], objective="grep"))
        self.assertEqual(port.greps, [("ECONNRESET", ("app/server.log",))],
                         "the grep tool must actually be called")


class InsufficientInputTests(unittest.TestCase):
    """4: empty or insufficient input is explicit; success is never "nothing found"."""

    def test_no_targets_is_a_schema_failure_not_an_empty_success(self):
        result = verifier().execute(task(targets=()))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "schema")
        self.assertIn("no inspection target", result.failure_detail)
        self.assertEqual(result.observations, ())

    def test_a_missing_file_is_an_observation_saying_so(self):
        result = verifier().execute(task(targets=["does/not/exist.log"]))
        self.assertEqual(result.status, "partial", "nothing was read, so this is not a clean success")
        self.assertIn("DOES NOT EXIST", result.observations[0].content)
        self.assertIn("not inferred", result.observations[0].content)

    def test_contents_are_never_inferred_for_a_missing_file(self):
        result = verifier().execute(task(targets=["missing.log"]))
        self.assertNotIn("OBSERVED in missing.log", result.observations[0].content)

    def test_a_mixed_batch_is_partial_and_names_what_was_absent(self):
        result = verifier().execute(task(targets=["app/server.log", "missing.log"]))
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.failure_kind, "unavailable")
        self.assertIn("1 of 2", result.failure_detail)
        self.assertEqual(len(result.observations), 2)

    def test_every_target_absent_is_partial(self):
        result = verifier().execute(task(targets=["a.log", "b.log"]))
        self.assertEqual(result.status, "partial")
        self.assertIn("all 2", result.failure_detail)

    def test_a_grep_that_matches_nothing_is_a_clean_success_that_says_so(self):
        result = verifier().execute(env_task("t-1", [("grep_pattern", "NOSUCHTOKEN")],
                                            targets=["app/server.log"], objective="grep"))
        self.assertTrue(result.ok, "the search ran and found nothing: that is a real answer")
        self.assertIn("matched no lines", result.observations[0].content)
        self.assertIn("The search ran; it found nothing", result.observations[0].content)

    def test_a_glob_that_matches_nothing_is_a_clean_success_that_says_so(self):
        result = verifier().execute(env_task("t-1", [("glob_pattern", "*.nothing")],
                                            objective="glob"))
        self.assertTrue(result.ok)
        self.assertIn("matched no paths", result.observations[0].content)

    def test_a_full_read_is_a_clean_success(self):
        result = verifier().execute(task(targets=["app/server.log"]))
        self.assertTrue(result.ok)
        self.assertIsNone(result.failure_kind)
        self.assertIn("ECONNRESET", result.observations[0].content)

    def test_an_unreadable_source_is_a_failure_not_a_partial(self):
        worker = verifier(raises=SourceUnavailable("unavailable", "disk gone"))
        result = worker.execute(task())
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "unavailable")
        self.assertIn("disk gone", result.failure_detail)

    def test_a_port_failure_never_reaches_the_engineer_as_a_clean_result(self):
        worker = verifier(raises=SourceUnavailable("invalid_pattern", "bad regex"))
        result = worker.execute(task())
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "failed")


class RefusalTests(unittest.TestCase):
    """5: refusals stay refusals."""

    def test_a_task_addressed_to_another_agent_is_refused(self):
        memory_task = build_memory_specialist_task(task_id="t-1", case_signature="x")
        with self.assertRaises(PermissionError) as caught:
            verifier().execute(memory_task)
        self.assertIn("wrong worker", str(caught.exception))

    def test_an_unauthorised_tool_is_refused_before_any_read(self):
        port = FakeSourcePort(FILES)
        bad = TaskSpec(task_id="t-1", agent=CODE_LOG_VERIFIER, delegated_by="coordinator",
                       objective="inspect", context={"case_signature": "x"},
                       allowed_tools=("hindsight_recall",), model="primary", depth=1)
        with self.assertRaises((AuthorizationError, TaskSpecError)):
            CodeLogVerifier(port).execute(bad)
        self.assertEqual(port.reads, [], "a refused task must not touch the port")

    def test_an_over_deep_spec_is_refused(self):
        """`authorize()` refuses it first; the worker's own depth guard is defence in depth behind it."""
        spec = build_verifier_task(task_id="t-1", case_signature="x", targets=["app/server.log"])
        deep = TaskSpec(task_id=spec.task_id, agent=spec.agent, delegated_by=spec.delegated_by,
                        objective=spec.objective, context=spec.context,
                        allowed_tools=spec.allowed_tools, model=spec.model, depth=2)
        with self.assertRaises((AuthorizationError, PermissionError)) as caught:
            verifier().execute(deep)
        self.assertIn("depth", str(caught.exception).lower())

    def test_the_worker_may_not_use_the_task_tool(self):
        from debugagent.agents.registry import authorize_tool

        with self.assertRaises(AuthorizationError):
            authorize_tool(CODE_LOG_VERIFIER, "task")

    def test_a_refusal_and_a_fault_stay_distinguishable(self):
        """A refusal is a rule saying no; a fault is something going wrong."""
        refused = verifier().execute(task(targets=()))
        faulted = verifier(raises=SourceUnavailable("unavailable", "disk gone")).execute(task())
        self.assertEqual(refused.status, "failed")
        self.assertEqual(faulted.status, "failed")
        self.assertEqual(refused.failure_kind, "schema")
        self.assertEqual(faulted.failure_kind, "unavailable")
        self.assertIn("no inspection target", refused.failure_detail)
        self.assertIn("disk gone", faulted.failure_detail)


class ObservationTests(unittest.TestCase):
    def test_provenance_is_derived_from_the_reference(self):
        log = verifier().execute(task(targets=["app/server.log"]))
        self.assertEqual(log.observations[0].kind, "log")
        self.assertTrue(log.observations[0].source.startswith("log:"),
                        "a tool-derived observation must never claim an engineer source")
        code = verifier().execute(task(targets=["src/upload.py"]))
        self.assertEqual(code.observations[0].kind, "code")
        self.assertTrue(code.observations[0].source.startswith("code:"))

    def test_a_long_file_is_clipped_and_labelled_partial(self):
        worker = verifier(files={"big.log": "x" * (MAX_EXCERPT_CHARS + 500)})
        result = worker.execute(task(targets=["big.log"]))
        content = result.observations[0].content
        self.assertIn("PARTIAL", content)
        self.assertLessEqual(len(content), MAX_EXCERPT_CHARS + 400)

    def test_a_short_file_is_not_labelled_partial(self):
        result = verifier().execute(task(targets=["src/upload.py"]))
        self.assertNotIn("PARTIAL", result.observations[0].content)

    def test_the_issue_signature_is_carried_into_the_observation(self):
        result = verifier().execute(task(case="uploads reset above 2MB"))
        self.assertIn("uploads reset above 2MB", result.observations[0].content)

    def test_the_result_carries_no_authority_to_retain(self):
        result = verifier().execute(task())
        self.assertTrue(result.ok)
        self.assertEqual(result.agent, CODE_LOG_VERIFIER)
        self.assertEqual(result.task_id, "t-1")

    def test_the_worker_never_constructs_its_own_tasks(self):
        """Tasks come from the seam; the worker executes what it is given."""
        spec = build_verifier_task(task_id="t-1", case_signature="x", targets=["app/server.log"])
        self.assertEqual(spec.agent, CODE_LOG_VERIFIER)
        self.assertEqual(set(spec.allowed_tools), {"read", "grep", "glob"})


class FanOutIntegrationTests(unittest.TestCase):
    """Through the real Coordinator: dispatch, concurrency, and memory unaffected."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.port = FakeSourcePort(FILES)

    def memory_port(self):
        store = HindsightMemoryStore(
            memory_config(self.tmp_path, bank_id="p6", ledger_path=self.tmp_path / "l.json"),
            client=FakeHindsightClient())
        self.addCleanup(store.close)
        return HindsightMemoryPort(store)

    def coordinator(self, **extra):
        workers = {CODE_LOG_VERIFIER: CodeLogVerifier(self.port)}
        workers.update(extra)
        return Coordinator(workers)

    def test_dispatch_through_fan_out(self):
        coordinator = self.coordinator()
        fan = coordinator.fan_out([
            build_verifier_task(task_id="v-1", case_signature="uploads fail",
                                targets=["app/server.log"]),
            build_verifier_task(task_id="v-2", case_signature="uploads fail",
                                targets=["src/upload.py"]),
        ])
        self.assertTrue(fan.joined)
        self.assertTrue(all(o.ok for o in fan), f"{fan.summary()}")
        self.assertEqual([o.task_id for o in fan.outcomes], ["v-1", "v-2"])
        self.assertEqual(sorted(self.port.reads), ["app/server.log", "src/upload.py"])

    def test_it_runs_concurrently_with_other_non_client_work(self):
        """Two verifiers plus a gated third: all three must be inside their workers at once."""
        class GatedPort(FakeSourcePort):
            def __init__(self):
                super().__init__(FILES)
                self.gate = threading.Barrier(3)
                self._running = 0
                self.max_running = 0
                self._lock = threading.Lock()

            def read(self, path):
                with self._lock:
                    self._running += 1
                    self.max_running = max(self.max_running, self._running)
                try:
                    self.gate.wait(timeout=GATE_TIMEOUT)
                    return super().read(path)
                finally:
                    with self._lock:
                        self._running -= 1

        port = GatedPort()
        coordinator = Coordinator({CODE_LOG_VERIFIER: CodeLogVerifier(port)})
        fan = coordinator.fan_out([
            build_verifier_task(task_id=f"v-{i}", case_signature="uploads fail",
                                targets=["app/server.log", "src/upload.py"])
            for i in range(3)], timeout=2.0)
        self.assertTrue(all(o.ok for o in fan), f"{fan.summary()}")
        self.assertEqual(port.max_running, 3,
                         "the verifier's tasks must be able to overlap one another")

    def test_the_verifier_does_not_take_the_memory_lane(self):
        coordinator = self.coordinator()
        self.assertFalse(coordinator.touches_client(
            build_verifier_task(task_id="v-1", case_signature="x", targets=["app/server.log"])))
        before = coordinator.lane.entered
        coordinator.fan_out([build_verifier_task(task_id="v-1", case_signature="x",
                                                 targets=["app/server.log"])])
        self.assertEqual(coordinator.lane.entered, before)

    def test_it_runs_concurrently_with_memory_work(self):
        """The point of the capability model: verifier work overlaps, memory stays serial."""
        memory = self.memory_port()
        coordinator = Coordinator({
            CODE_LOG_VERIFIER: CodeLogVerifier(self.port),
            MEMORY: MemorySpecialist(memory),
        })
        lane_before = coordinator.lane.entered
        fan = coordinator.fan_out([
            build_memory_specialist_task(task_id="m-1", case_signature="uploads fail"),
            build_verifier_task(task_id="v-1", case_signature="uploads fail",
                                targets=["app/server.log"]),
            build_memory_specialist_task(task_id="m-2", case_signature="uploads fail"),
            build_verifier_task(task_id="v-2", case_signature="uploads fail",
                                targets=["src/upload.py"]),
        ], timeout=2.0)
        self.assertTrue(all(o.ok for o in fan), f"{fan.summary()}")
        self.assertEqual([o.task_id for o in fan.outcomes], ["m-1", "v-1", "m-2", "v-2"],
                         "ordering must stay positional across both agents")
        self.assertEqual(coordinator.lane.entered - lane_before, 2,
                         "both memory tasks must have taken the lane, and the verifier tasks none")

    def test_a_verifier_failure_does_not_hide_memory_results(self):
        memory = self.memory_port()
        coordinator = Coordinator({
            CODE_LOG_VERIFIER: CodeLogVerifier(self.port),
            MEMORY: MemorySpecialist(memory),
        })
        fan = coordinator.fan_out([
            build_memory_specialist_task(task_id="m-1", case_signature="uploads fail"),
            build_verifier_task(task_id="v-1", case_signature="uploads fail", targets=()),
        ], timeout=2.0)
        self.assertTrue(fan.outcomes[0].ok, "the memory task must be unaffected")
        self.assertEqual(fan.outcomes[1].status, "failed")
        self.assertEqual(fan.summary()["by_status"], {"success": 1, "failed": 1})

    def test_single_delegation_through_the_coordinator(self):
        coordinator = self.coordinator()
        result = coordinator.delegate(build_verifier_task(
            task_id="v-1", case_signature="uploads fail", targets=["app/server.log"]))
        self.assertTrue(result.ok)
        self.assertEqual(len(coordinator.dispatches), 1)


if __name__ == "__main__":
    unittest.main()
