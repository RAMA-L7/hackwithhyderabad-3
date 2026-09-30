"""P6: the Patch Generator worker.

The Memory Specialist answers "what happened before", the Code/Log Verifier answers "what does the
CURRENT system say", and this worker answers "what change would address it" - then stops. The tests pin
the boundaries that matter:

1. **It proposes; it never applies.** The seam is read-only and has no mutating method, so there is no
   path from this worker to a modified working tree. Proven structurally, not by reading the calls.
2. **A proposal is not a verification.** Every artifact is prefixed `PROPOSED PATCH` and says it is
   unapplied and unverified - inside the content, so a caller carrying only `content` keeps it.
3. **It never touches memory.** No memory import, no memory seam, and a recalled case in the task context
   is REFUSED rather than used as a basis for changing the repository.
4. **Refusal, failure, insufficient input and success are four different things.** In particular a
   missing input is never reported as "no patch needed" - that phrase belongs only to a real comparison
   that found the proposal identical to the current file.

Then the integration half: dispatch through `Coordinator.fan_out`, and prove it overlaps the Code/Log
Verifier while memory work stays serial.

Determinism: an in-memory read-only port; no filesystem, no VCS, no network, no sleeps.
"""

from __future__ import annotations

import difflib
import inspect
import tempfile
import threading
import unittest
from pathlib import Path

import support
from debugagent.agents.code_log_verifier import CODE_LOG_VERIFIER, CodeLogVerifier
from debugagent.agents.coordinator import Coordinator
from debugagent.agents.memory_specialist import MemorySpecialist, build_memory_specialist_task
from debugagent.agents.patch_generator import (
    DIFF_TOOL,
    MAX_DIFF_CHARS,
    PROPOSED_KEY,
    READ_TOOL,
    PATCH_GENERATOR,
    PatchGenerator,
    SourceNotFound,
    SourceUnavailable,
    build_patch_task,
)
from debugagent.agents.registry import AuthorizationError, authorize, get_agent
from debugagent.agents.tasks import Artifact, SubAgentResult, TaskSpec, TaskSpecError, WorkerContext
from debugagent.memory.hindsight_store import HindsightMemoryStore
from debugagent.pipeline.memory_adapter import HindsightMemoryPort
from support import FakeHindsightClient, memory_config

GATE_TIMEOUT = 1.0
MEMORY = "memory_specialist"
CURRENT = "MAX_BODY = 2 * 1024 * 1024\n"
PROPOSED = "MAX_BODY = 10 * 1024 * 1024\n"


class FakePatchPort:
    """A read-only stand-in for the repository. It cannot write, by construction."""

    def __init__(self, files: dict[str, str] | None = None, *, raises: Exception | None = None,
                 no_difference: bool = False):
        self.files = dict(files if files is not None else {"src/upload.py": CURRENT})
        self._raises = raises
        self._no_difference = no_difference
        self.reads: list[str] = []
        self.diffs: list[tuple[str, str]] = []

    def read(self, path: str) -> str:
        if self._raises is not None:
            raise self._raises
        self.reads.append(path)
        if path not in self.files:
            raise SourceNotFound(path)
        return self.files[path]

    def diff(self, path: str, proposed: str) -> tuple[str, ...]:
        if self._raises is not None:
            raise self._raises
        self.diffs.append((path, proposed))
        if self._no_difference:
            return ()
        return tuple(difflib.unified_diff(self.files.get(path, "").splitlines(),
                                          proposed.splitlines(), fromfile=path, tofile=path))


def generator(port: FakePatchPort | None = None) -> PatchGenerator:
    return PatchGenerator(port if port is not None else FakePatchPort())


def task(task_id: str = "t-1", *, target: str = "src/upload.py", proposed: str = PROPOSED,
         case: str = "uploads reset above 2MB", tools=None, objective: str | None = None) -> TaskSpec:
    spec = build_patch_task(task_id=task_id, case_signature=case, target=target, proposed=proposed,
                            **({"objective": objective} if objective else {}))
    if tools is None:
        return spec
    return authorize(TaskSpec(task_id=spec.task_id, agent=spec.agent, delegated_by=spec.delegated_by,
                              objective=spec.objective, context=spec.context,
                              allowed_tools=tuple(tools), model=spec.model, depth=spec.depth))


def context_task(task_id: str, *, artifacts=(), environment=(), objective: str = "propose a patch",
                 tools=None) -> TaskSpec:
    """A patch task built by hand, for the malformed-input cases `build_patch_task` will not produce."""
    return authorize(TaskSpec(
        task_id=task_id, agent=PATCH_GENERATOR, delegated_by="coordinator", objective=objective,
        context=WorkerContext(case_signature="uploads reset above 2MB", symptoms=(),
                              environment=tuple(environment), artifacts=tuple(artifacts)),
        allowed_tools=("read", "diff") if tools is None else tuple(tools),
        model="primary", depth=1))


def code_artifact(ref: str = "src/upload.py") -> Artifact:
    return Artifact(kind="code", ref=ref, content=f"requested for patching: {ref}")


class ConstructionTests(unittest.TestCase):
    def test_the_roster_identity_and_capability_are_used(self):
        worker = generator()
        self.assertEqual(worker.agent_id, PATCH_GENERATOR)
        self.assertEqual(worker.allowed_tools, get_agent(PATCH_GENERATOR).allowed_tools)
        self.assertEqual(worker.model, get_agent(PATCH_GENERATOR).model)

    def test_the_roster_records_this_agent_as_non_client(self):
        self.assertFalse(get_agent(PATCH_GENERATOR).client_access,
                         "the Patch Generator must not be serialised through the memory lane")

    def test_the_worker_declares_no_capability_of_its_own(self):
        self.assertFalse(hasattr(PatchGenerator, "client_access"))

    def test_an_unknown_agent_id_is_refused_at_construction(self):
        with self.assertRaises(AuthorizationError):
            PatchGenerator(FakePatchPort(), agent="omnipotent_agent")

    def test_it_uses_only_the_registered_tools(self):
        self.assertEqual(set(generator().allowed_tools), {"read", "diff"})


class NoWritePathTests(unittest.TestCase):
    """1: it proposes; it never applies."""

    def test_the_seam_offers_no_mutating_operation(self):
        """Structural, in the type: a port with `write` would be a path to a changed tree."""
        from debugagent.agents.patch_generator import PatchSourcePort

        members = {name for name in dir(PatchSourcePort) if not name.startswith("_")}
        self.assertEqual(members, {"read", "diff"},
                         "the seam must expose exactly the two authorised operations")
        for forbidden in ("write", "apply", "commit", "push", "replace", "patch", "save"):
            self.assertNotIn(forbidden, members,
                             f"the seam must not offer {forbidden}")

    def test_the_module_calls_no_mutating_operation(self):
        """Scanned over attribute accesses, which is what reachability actually depends on."""
        import ast
        import textwrap

        module = inspect.getmodule(PatchGenerator)
        tree = ast.parse(textwrap.dedent(inspect.getsource(module)))
        attributes: set[str] = set()
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                attributes.add(node.attr)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for forbidden in ("write_text", "write_bytes", "open", "remove", "unlink", "rename",
                          "commit", "push", "checkout", "apply_patch", "run", "system", "Popen"):
            self.assertNotIn(forbidden, attributes,
                             f"the Patch Generator must not call {forbidden}")
        for forbidden in ("subprocess", "os", "shutil", "pathlib"):
            self.assertFalse([name for name in imported if forbidden in name],
                             f"the Patch Generator must not import {forbidden}")

    def test_producing_a_patch_leaves_the_repository_untouched(self):
        port = FakePatchPort()
        result = generator(port).execute(task())
        self.assertTrue(result.ok)
        self.assertEqual(port.files, {"src/upload.py": CURRENT},
                         "the worker must not have modified the port's contents")

    def test_the_worker_records_no_applied_change(self):
        worker = generator()
        worker.execute(task())
        self.assertEqual(worker._proposed, ["src/upload.py"],
                         "the worker records what it PROPOSED, not what it changed")


class ProposalNotVerificationTests(unittest.TestCase):
    """2: a proposal is not a verification."""

    def test_every_artifact_is_labelled_a_proposal(self):
        result = generator().execute(task())
        self.assertTrue(result.ok)
        for observation in result.observations:
            self.assertTrue(observation.content.startswith("PROPOSED PATCH"),
                            "every artifact must be labelled a proposed patch")
            self.assertIn("unapplied", observation.content)
            self.assertIn("unverified", observation.content)
            self.assertIn("NOT been written, applied, committed or run", observation.content)

    def test_an_artifact_never_claims_the_change_is_correct(self):
        result = generator().execute(task())
        for observation in result.observations:
            content = observation.content.lower()
            for forbidden in ("this fixes", "root cause is", "proven", "this is correct",
                              "safe to apply", "guarantees"):
                self.assertNotIn(forbidden, content,
                                 "a proposal must not assert engineering truth")

    def test_provenance_is_carried_by_the_artifact(self):
        result = generator().execute(task())
        self.assertEqual(result.observations[0].kind, "code")
        self.assertTrue(result.observations[0].source.startswith("code:"),
                        "a tool-derived proposal must never claim an engineer source")

    def test_the_target_and_issue_are_named(self):
        result = generator().execute(task(target="src/upload.py", case="uploads reset above 2MB"))
        content = result.observations[0].content
        self.assertIn("src/upload.py", content)
        self.assertIn("uploads reset above 2MB", content)

    def test_a_long_diff_is_clipped_and_labelled_partial(self):
        port = FakePatchPort(files={"big.py": "a\n"})
        long_proposal = "".join(f"line {i}\n" for i in range(2_000))
        result = generator(port).execute(task(target="big.py", proposed=long_proposal))
        content = result.observations[0].content
        self.assertIn("PARTIAL", content)
        self.assertLess(len(content), MAX_DIFF_CHARS + 800)

    def test_it_cannot_retain_a_case(self):
        """It has no memory seam, so this is a refusal rather than a silent no-op."""
        result = generator().execute(task(objective="retain this resolved case"))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "schema")


class NoMemoryTests(unittest.TestCase):
    """3: it never touches memory."""

    def test_the_module_imports_nothing_memory_related(self):
        import ast
        import textwrap

        module = inspect.getmodule(PatchGenerator)
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
                             f"the Patch Generator must not import anything from {forbidden}")
        for forbidden in ("recall_and_classify", "retain", "create_bank"):
            self.assertNotIn(forbidden, attributes,
                             f"the Patch Generator must not call {forbidden}")

    def test_a_recalled_case_in_the_context_is_refused(self):
        contaminated = context_task(
            "t-1",
            artifacts=(code_artifact(),
                       Artifact(kind="memory", ref="case-abc", content="past case: raise the limit")))
        result = generator().execute(contaminated)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "schema")
        self.assertIn("past case is not evidence", result.failure_detail)
        self.assertEqual(result.observations, ())

    def test_the_worker_holds_no_memory_seam(self):
        port = generator()._port
        for forbidden in ("recall_and_classify", "retain", "recall", "get_bank_config"):
            self.assertFalse(hasattr(port, forbidden))


class AuthorityTests(unittest.TestCase):
    """Refusal, tool grants and depth."""

    def test_a_task_addressed_to_another_agent_is_refused(self):
        verifier_task = TaskSpec(task_id="t-1", agent=CODE_LOG_VERIFIER, delegated_by="coordinator",
                                 objective="inspect", context={"case_signature": "x"},
                                 allowed_tools=("read",), model="primary", depth=1)
        with self.assertRaises(PermissionError) as caught:
            generator().execute(verifier_task)
        self.assertIn("wrong worker", str(caught.exception))

    def test_an_unauthorised_tool_is_refused_before_any_seam_call(self):
        port = FakePatchPort()
        bad = TaskSpec(task_id="t-1", agent=PATCH_GENERATOR, delegated_by="coordinator",
                       objective="propose", context={"case_signature": "x"},
                       allowed_tools=("hindsight_recall",), model="primary", depth=1)
        with self.assertRaises((AuthorizationError, TaskSpecError)):
            generator(port).execute(bad)
        self.assertEqual(port.reads, [], "a refused task must not touch the seam")
        self.assertEqual(port.diffs, [])

    def test_a_task_without_the_read_tool_is_refused(self):
        port = FakePatchPort()
        result = generator(port).execute(task(tools=(DIFF_TOOL,)))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "schema")
        self.assertIn(READ_TOOL, result.failure_detail)
        self.assertEqual(port.reads, [], "no read may happen without the grant")

    def test_a_task_without_the_diff_tool_is_refused(self):
        port = FakePatchPort()
        result = generator(port).execute(task(tools=(READ_TOOL,)))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "schema")
        self.assertIn(DIFF_TOOL, result.failure_detail)
        self.assertEqual(port.diffs, [], "no diff may happen without the grant")

    def test_both_granted_tools_are_actually_used(self):
        port = FakePatchPort()
        generator(port).execute(task())
        self.assertEqual(port.reads, ["src/upload.py"], "the read tool must be used as the diff base")
        self.assertEqual(port.diffs, [("src/upload.py", PROPOSED)],
                         "the diff tool must be used to render the proposal")

    def test_an_over_deep_spec_is_refused(self):
        spec = task()
        deep = TaskSpec(task_id=spec.task_id, agent=spec.agent, delegated_by=spec.delegated_by,
                        objective=spec.objective, context=spec.context,
                        allowed_tools=spec.allowed_tools, model=spec.model, depth=2)
        with self.assertRaises((AuthorizationError, PermissionError)):
            generator().execute(deep)

    def test_the_worker_may_not_use_the_task_tool(self):
        from debugagent.agents.registry import authorize_tool

        with self.assertRaises(AuthorizationError):
            authorize_tool(PATCH_GENERATOR, "task")

    def test_the_worker_may_not_use_the_verbs_it_does_not_own(self):
        for tool in ("hindsight_recall", "hindsight_get_facts", "grep", "glob"):
            with self.subTest(tool=tool):
                with self.assertRaises(AuthorizationError):
                    authorize(TaskSpec(task_id="t", agent=PATCH_GENERATOR, delegated_by="coordinator",
                                       objective="propose", context={"case_signature": "x"},
                                       allowed_tools=(tool,), model="primary", depth=1))


class InsufficientInputTests(unittest.TestCase):
    """4: missing input is never 'no patch needed'."""

    def test_no_target_is_a_schema_failure(self):
        result = generator().execute(context_task("t-1"))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "schema")
        self.assertIn("no target supplied", result.failure_detail)
        self.assertIn("not a finding that no patch is needed", result.failure_detail)
        self.assertEqual(result.observations, ())

    def test_more_than_one_target_is_refused_rather_than_guessed(self):
        result = generator().execute(context_task(
            "t-1", artifacts=(code_artifact("a.py"), code_artifact("b.py"))))
        self.assertEqual(result.status, "failed")
        self.assertIn("exactly one file", result.failure_detail)

    def test_no_proposed_content_is_a_schema_failure(self):
        result = generator().execute(context_task("t-1", artifacts=(code_artifact(),)))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "schema")
        self.assertIn(PROPOSED_KEY, result.failure_detail)
        self.assertIn("not a finding that no patch is needed", result.failure_detail)

    def test_empty_proposed_content_is_refused_not_called_no_change(self):
        """A blank proposal is insufficient input, never a successful "no change needed"."""
        for blank in ("", "   ", "\n  "):
            with self.subTest(blank=repr(blank)):
                result = generator().execute(task(proposed=blank))
                self.assertEqual(result.status, "failed",
                                 "a blank proposal must not be a success")
                self.assertEqual(result.failure_kind, "schema")
                self.assertIn(PROPOSED_KEY, result.failure_detail)
                self.assertNotIn("no change proposed", result.failure_detail.lower())

    def test_a_missing_file_is_partial_not_a_clean_success(self):
        result = generator().execute(task(target="missing.py"))
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.failure_kind, "unavailable")
        self.assertIn("does not exist", result.observations[0].content)
        self.assertIn("Contents are not", result.observations[0].content)

    def test_a_genuine_no_difference_is_a_clean_success_that_says_so(self):
        """The distinction the whole set exists for: a comparison ran and found nothing."""
        port = FakePatchPort(no_difference=True)
        result = generator(port).execute(task())
        self.assertTrue(result.ok)
        self.assertIn("NO CHANGE PROPOSED", result.observations[0].content)
        self.assertIn("The comparison ran", result.observations[0].content)
        self.assertEqual(port.diffs, [("src/upload.py", PROPOSED)],
                         "the comparison must actually have been made")

    def test_a_repository_fault_is_a_failure(self):
        worker = generator(FakePatchPort(raises=SourceUnavailable("unavailable", "worktree gone")))
        result = worker.execute(task())
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "unavailable")
        self.assertIn("worktree gone", result.failure_detail)

    def test_all_four_outcomes_are_distinguishable(self):
        """refusal, failure, insufficient input and success must not collapse into each other."""
        with self.assertRaises(PermissionError) as refusal:
            generator().execute(
                TaskSpec(task_id="t", agent=CODE_LOG_VERIFIER, delegated_by="coordinator",
                         objective="inspect", context={"case_signature": "x"},
                         allowed_tools=("read",), model="primary", depth=1))
        insufficient = generator().execute(context_task("t-2"))
        fault = generator(FakePatchPort(raises=SourceUnavailable("unavailable", "gone"))).execute(task())
        success = generator().execute(task(task_id="t-4"))
        self.assertIsInstance(refusal.exception, PermissionError)
        # The two failures share `status="failed"` by design - the AGENT VOCABULARY has no separate
        # status for them. What must distinguish them is the KIND, so that is what is compared.
        self.assertEqual(insufficient.failure_kind, "schema")
        self.assertEqual(fault.failure_kind, "unavailable")
        self.assertTrue(success.ok)
        self.assertIsNone(success.failure_kind)
        self.assertEqual(len({(o.status, o.failure_kind) for o in (insufficient, fault, success)}), 3,
                         "insufficient input, a fault and a success must be three distinct outcomes")


class FanOutIntegrationTests(unittest.TestCase):
    """Through the real Coordinator, alongside the Verifier and the Memory Specialist."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.patch_port = FakePatchPort({"src/upload.py": CURRENT, "src/other.py": "OTHER = 1\n"})
        self.verify_port = _SimpleVerifyPort()

    def memory_port(self):
        store = HindsightMemoryStore(
            memory_config(self.tmp_path, bank_id="p6b", ledger_path=self.tmp_path / "l.json"),
            client=FakeHindsightClient())
        self.addCleanup(store.close)
        return HindsightMemoryPort(store)

    def coordinator(self):
        return Coordinator({
            PATCH_GENERATOR: PatchGenerator(self.patch_port),
            CODE_LOG_VERIFIER: CodeLogVerifier(self.verify_port),
        })

    def test_dispatch_through_fan_out(self):
        fan = self.coordinator().fan_out([
            build_patch_task(task_id="p-1", case_signature="body limit", target="src/upload.py",
                             proposed=PROPOSED),
            build_patch_task(task_id="p-2", case_signature="body limit", target="src/other.py",
                             proposed=PROPOSED),
        ])
        self.assertTrue(fan.joined)
        self.assertTrue(all(o.ok for o in fan), f"{fan.summary()}")
        self.assertEqual([o.task_id for o in fan.outcomes], ["p-1", "p-2"])
        self.assertEqual(sorted(self.patch_port.diffs), [("src/other.py", PROPOSED),
                                                         ("src/upload.py", PROPOSED)])

    def test_single_delegation_through_the_coordinator(self):
        coordinator = self.coordinator()
        result = coordinator.delegate(build_patch_task(
            task_id="p-1", case_signature="body limit", target="src/upload.py", proposed=PROPOSED))
        self.assertTrue(result.ok)
        self.assertEqual(len(coordinator.dispatches), 1)

    def test_it_does_not_take_the_memory_lane(self):
        coordinator = self.coordinator()
        self.assertFalse(coordinator.touches_client(build_patch_task(
            task_id="p-1", case_signature="x", target="src/upload.py", proposed=PROPOSED)))
        before = coordinator.lane.entered
        coordinator.fan_out([build_patch_task(task_id="p-1", case_signature="x",
                                              target="src/upload.py", proposed=PROPOSED)])
        self.assertEqual(coordinator.lane.entered, before)

    def test_it_runs_concurrently_with_verifier_work(self):
        """Both non-client agents must be inside their workers at the same time."""
        barrier = threading.Barrier(2)
        running = {"now": 0, "max": 0}
        lock = threading.Lock()

        def gate() -> None:
            try:
                barrier.wait(timeout=GATE_TIMEOUT)
            except threading.BrokenBarrierError:
                pass

        class GatedPatchPort(FakePatchPort):
            def read(self, path):
                gate()
                return super().read(path)

        class GatedVerifyPort(_SimpleVerifyPort):
            def read(self, path):
                gate()
                return super().read(path)

        class Counting:
            """Wraps either port so overlap is counted across BOTH workers."""

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
                        return attribute(*args, **kwargs)
                    finally:
                        with lock:
                            running["now"] -= 1

                return wrapper

        coordinator = Coordinator({
            PATCH_GENERATOR: PatchGenerator(Counting(GatedPatchPort())),
            CODE_LOG_VERIFIER: CodeLogVerifier(Counting(GatedVerifyPort())),
        })
        fan = coordinator.fan_out([
            build_patch_task(task_id="p-1", case_signature="body limit", target="src/upload.py",
                             proposed=PROPOSED),
            _verify_task("v-1"),
        ], timeout=2.0)
        self.assertTrue(all(o.ok for o in fan), f"{fan.summary()}")
        self.assertEqual(running["max"], 2,
                         "the Patch Generator and the Verifier must be able to overlap")

    def test_a_mixed_fan_out_keeps_memory_serial_and_the_rest_ordered(self):
        memory = self.memory_port()
        coordinator = Coordinator({
            PATCH_GENERATOR: PatchGenerator(self.patch_port),
            CODE_LOG_VERIFIER: CodeLogVerifier(self.verify_port),
            MEMORY: MemorySpecialist(memory),
        })
        lane_before = coordinator.lane.entered
        fan = coordinator.fan_out([
            build_memory_specialist_task(task_id="m-1", case_signature="uploads fail"),
            build_patch_task(task_id="p-1", case_signature="body limit", target="src/upload.py",
                             proposed=PROPOSED),
            _verify_task("v-1"),
            build_memory_specialist_task(task_id="m-2", case_signature="uploads fail"),
        ], timeout=2.0)
        self.assertTrue(all(o.ok for o in fan), f"{fan.summary()}")
        self.assertEqual([o.task_id for o in fan.outcomes], ["m-1", "p-1", "v-1", "m-2"])
        self.assertEqual(coordinator.lane.entered - lane_before, 2,
                         "only the two memory tasks may take the lane")

    def test_a_patch_failure_does_not_hide_the_other_results(self):
        coordinator = Coordinator({PATCH_GENERATOR: PatchGenerator(self.patch_port)})
        fan = coordinator.fan_out([
            build_patch_task(task_id="p-1", case_signature="body limit", target="src/upload.py",
                             proposed=PROPOSED),
            context_task("p-2"),
        ], timeout=2.0)
        self.assertTrue(fan.outcomes[0].ok)
        self.assertEqual(fan.outcomes[1].status, "failed")
        self.assertEqual(fan.summary()["by_status"], {"success": 1, "failed": 1})

    def test_a_refused_patch_task_is_explicit(self):
        coordinator = Coordinator({PATCH_GENERATOR: PatchGenerator(self.patch_port)})
        bad = TaskSpec(task_id="bad", agent=PATCH_GENERATOR, delegated_by="coordinator",
                       objective="propose", context={"case_signature": "x"},
                       allowed_tools=("grep",), model="primary", depth=1)
        fan = coordinator.fan_out([build_patch_task(task_id="p-1", case_signature="x",
                                                    target="src/upload.py", proposed=PROPOSED), bad],
                                  timeout=2.0)
        self.assertTrue(fan.outcomes[1].refused, "an unauthorised tool is a refusal, not a fault")
        self.assertIn("grep", str(fan.outcomes[1].error))


class _SimpleVerifyPort:
    """A minimal read-only port for the Verifier, so this file needs no filesystem."""

    def __init__(self, files=None):
        self.files = dict(files or {"app/server.log": "ECONNRESET above 2MB"})

    def read(self, path):
        if path not in self.files:
            raise SourceNotFound(path)
        return self.files[path]

    def glob(self, pattern):
        return tuple(sorted(p for p in self.files if pattern in p))

    def grep(self, pattern, paths):
        return tuple((p, 1, self.files[p]) for p in paths
                     if p in self.files and pattern in self.files[p])


def _verify_task(task_id: str) -> TaskSpec:
    from debugagent.agents.code_log_verifier import build_verifier_task

    return build_verifier_task(task_id=task_id, case_signature="uploads fail",
                               targets=["app/server.log"])


if __name__ == "__main__":
    unittest.main()
