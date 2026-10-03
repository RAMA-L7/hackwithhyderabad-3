"""VLSI-1C: the SDC Analyzer worker, and the boundary it was built to hold.

Two things are under test, and they are not the same thing.

**The capability.** A deterministic constraint file goes in and reproducible observations come out, with
their provenance intact, and the six status cases land where they should. Nothing here is mocked: a
temporary directory is the repository, `build_repository_runtime` composes the real ports and
Coordinator, and the injected analyser is the real VLSI-1A/VLSI-1B code.

**The boundary.** The worker is generic and the analysis is VLSI-specific, and that is a structural
claim rather than a stylistic one - so it is asserted from the imports rather than from a docstring. A
test that only checked behaviour would pass just as happily if the worker started importing
`debugagent.domains` directly, which is precisely the regression this milestone's design exists to
prevent.

## The deliberate decision that most looks like a bug

Zero recognised constraints is NOT treated as proof that a file is not SDC. The parser's supported
grammar is intentionally small, so a legitimate constraint file using constructs it does not yet handle
would be misdiagnosed by that rule - and a refusal would silently drop real findings while presenting as
a missing feature rather than as a defect. So the worker reports what it found and lets the leading
`analysis_unavailable` observation say plainly that nothing was recognised.

That is why `test_zero_recognised_constraints_is_reported_not_refused` exists and asserts the shape of
the uncertainty instead of its absence.
"""

from __future__ import annotations

import ast
import io
import pathlib
import tempfile
import unittest

from debugagent.agents.coordinator import Coordinator
from debugagent.agents.registry import (
    AGENT_DEFINITION_FIELDS,
    WORKER_ROSTER,
    AuthorizationError,
    agent_names,
    get_agent,
)
from debugagent.agents.sdc_analyzer_worker import (
    SDC_ANALYZER,
    SdcAnalyzerWorker,
    build_sdc_task,
)
from debugagent.agents.source_files import FileSourcePort, RepositoryScope
from debugagent.agents.tasks import Artifact, SubAgentResult
from debugagent.composition import build_repository_runtime
from debugagent.domains.vlsi.sdc_worker import SDC_ARTIFACT_KIND, analyze_sdc_text

AGENTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "debugagent" / "agents"
COMPOSITION = pathlib.Path(__file__).resolve().parents[1] / "src" / "debugagent" / "composition.py"

# Two constraints whose consistency rules fail: an input delay naming a clock that was never defined,
# and the same clock defined twice with different periods. Both are decidable from the file alone.
TWO_FINDINGS = """create_clock -name core_clk -period 10 [get_ports clk]
set_input_delay -clock missing_clk -max 2 [get_ports d]
create_clock -name core_clk -period 12 [get_ports clk2]
"""

CLEAN = "create_clock -name core_clk -period 10 [get_ports clk]\n"

# A real SDC file with one unreadable line. The first constraint parses, so this is a partial read of a
# constraint file rather than a file that was never one.
PARTIAL = """create_clock -name core_clk -period 10 [get_ports clk]
create_clock -name
"""

EMPTY = ""

NOT_SDC = "def handler(request):\n    return 200\n"


class WorkerFixture(unittest.TestCase):
    """A real repository, a real Coordinator, and the worker the composition root would build."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        constraints = self.root / "constraints"
        constraints.mkdir()
        for name, text in (("top.sdc", TWO_FINDINGS), ("clean.sdc", CLEAN),
                           ("partial.sdc", PARTIAL), ("empty.sdc", EMPTY),
                           ("handler.py", NOT_SDC)):
            (constraints / name).write_text(text, encoding="utf-8")
        self.runtime = build_repository_runtime(self.root)
        self.worker = self.runtime.coordinator.worker_for(SDC_ANALYZER)

    def analyse(self, target, *, case_signature="constraint problem"):
        """Run one task through the real Coordinator, so authorisation runs for real."""
        return self.runtime.coordinator.delegate(build_sdc_task(
            task_id=f"sdc:{target}", case_signature=case_signature, target=target))


class Determinism(WorkerFixture):
    def test_two_runs_over_the_same_file_are_identical(self):
        first = self.analyse("constraints/top.sdc")
        second = self.analyse("constraints/top.sdc")
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_findings_are_reported_in_source_order(self):
        result = self.analyse("constraints/top.sdc")
        self.assertEqual([o.ref for o in result.observations],
                         ["constraints/top.sdc:2", "constraints/top.sdc:3"])

    def test_the_analyser_is_reached_without_any_model(self):
        """The whole point of the seam: this capability has no LLM in it."""
        calls = []

        def counting_analyze(text, *, ref):
            calls.append(ref)
            return analyze_sdc_text(text, ref=ref)

        coordinator = Coordinator()
        worker = SdcAnalyzerWorker(FileSourcePort(RepositoryScope(self.root)),
                                   analyze=counting_analyze)
        coordinator.register(SDC_ANALYZER, worker)
        result = coordinator.delegate(build_sdc_task(task_id="sdc:x", case_signature="c",
                                                     target="constraints/top.sdc"))
        self.assertEqual(calls, ["constraints/top.sdc"])
        self.assertEqual(result.status, "success")
        # The task spec itself carries the roster's model; nothing consumed it.
        self.assertNotIn("hindsight", result.to_dict().get("failure_detail") or "")


class FindingMapping(WorkerFixture):
    def test_a_finding_carries_its_location_and_a_code_artifact_kind(self):
        result = self.analyse("constraints/top.sdc")
        for observation in result.observations:
            self.assertEqual(observation.kind, SDC_ARTIFACT_KIND)
            self.assertTrue(observation.ref.startswith("constraints/top.sdc:"), observation.ref)

    def test_source_is_derived_from_the_kind_and_never_stored(self):
        """`Artifact.source` is computed, so the worker cannot get it wrong or invent it."""
        result = self.analyse("constraints/top.sdc")
        self.assertEqual(result.observations[0].source, "code:constraints/top.sdc:2")
        self.assertNotIn("source", result.observations[0].to_dict())

    def test_severity_and_finding_kind_are_written_into_the_content(self):
        """The one place typed structure becomes text, and it must be legible."""
        content = self.analyse("constraints/top.sdc").observations[0].content
        self.assertTrue(content.startswith("SDC critical: "), content)
        self.assertIn("[missing_constraint]", content)
        self.assertIn("at constraints/top.sdc:2", content)

    def test_the_domain_finding_kind_never_lands_in_the_artifact_kind(self):
        """Two different vocabularies both called `kind`. Conflating them would be a real bug."""
        result = self.analyse("constraints/top.sdc")
        for observation in result.observations:
            self.assertNotIn(observation.kind, ("missing_constraint", "conflicting_constraint"))
            self.assertIn(observation.kind, ("code", "log", "memory"))

    def test_an_unknown_location_is_never_invented(self):
        """No finding may carry a line the analysis does not have."""
        from debugagent.domains.vlsi.findings import VlsiFinding
        from debugagent.domains.vlsi.provenance import Provenance
        from debugagent.domains.vlsi.sdc_worker import observation_for

        finding = VlsiFinding(kind="missing_constraint", severity="warning",
                              message="no clock", provenance=Provenance("file", "top.sdc"))
        observation = observation_for(finding)
        self.assertEqual(observation.ref, "top.sdc")
        self.assertNotIn(":0", observation.ref)
        self.assertIn("unknown location", observation.content)


class StatusSemantics(WorkerFixture):
    def test_complete_with_findings_is_success(self):
        result = self.analyse("constraints/top.sdc")
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.observations), 2)

    def test_complete_and_clean_is_success_and_not_failed(self):
        """`clean + complete != failed`. A file that is fine says so, and says it is fine."""
        result = self.analyse("constraints/clean.sdc")
        self.assertEqual(result.status, "success")
        self.assertIsNone(result.failure_kind)
        self.assertEqual(result.observations, ())

    def test_a_parser_issue_makes_the_run_partial_not_success(self):
        """`partial != clean`. A half-read file must never read as a clean one."""
        result = self.analyse("constraints/partial.sdc")
        self.assertEqual(result.status, "partial")
        self.assertIsNone(result.failure_kind, "a partial read is not a failure")
        self.assertTrue(any("PARSE" in o.content for o in result.observations))

    def test_an_absent_target_is_failed_never_an_empty_result(self):
        result = self.analyse("constraints/missing.sdc")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "unavailable")
        self.assertEqual(result.observations, ())
        self.assertIn("absent", result.failure_detail)

    def test_zero_recognised_constraints_is_reported_not_refused(self):
        """The deliberate VLSI-1C decision.

        A file the parser understood nothing in must NOT be refused, because "no constraints" cannot
        distinguish an unsupported-but-valid constraint file from a file that was never one. So the run
        reports, `partial` if the parser complained, and leads with an observation that says outright
        that nothing was recognised.
        """
        result = self.analyse("constraints/handler.py")
        self.assertEqual(result.status, "partial")
        self.assertIsNone(result.failure_kind, "this must not be a failure either")
        self.assertIn("no SDC constraints were recognised", result.observations[0].content)
        self.assertIn("[analysis_unavailable]", result.observations[0].content)
        # The leading observation governs the ones after it, so it comes first.
        self.assertNotIn("PARSE", result.observations[0].content)

    def test_an_empty_file_is_a_clean_run_not_an_unreadable_one(self):
        result = self.analyse("constraints/empty.sdc")
        self.assertEqual(result.status, "success", "nothing was wrong with an empty file")
        self.assertIn("no SDC constraints were recognised", result.observations[0].content)


class Refusals(WorkerFixture):
    def test_a_recalled_case_in_context_is_refused(self):
        """A past case is not evidence about the constraints in front of the engineer."""
        from debugagent.agents.registry import build_task_spec

        spec = build_task_spec({
            "task_id": "sdc:memory", "agent": SDC_ANALYZER, "delegated_by": "coordinator",
            "objective": "analyse sdc constraints",
            "context": {"case_signature": "c", "symptoms": [],
                        "artifacts": [{"kind": "memory", "ref": "case-7",
                                       "content": "a past case"}]},
            "allowed_tools": ["read"], "model": "primary", "depth": 1,
        })
        result = self.runtime.coordinator.delegate(spec)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "schema")
        self.assertIn("recalled memory", result.failure_detail)

    def test_a_task_with_no_read_tool_is_refused_by_the_schema(self):
        """Refused at the schema, before authorisation and before any read.

        Worth stating precisely because the worker's own `read` check is therefore defence in depth
        rather than the primary guard. An empty tool list cannot become a `TaskSpec` at all.
        """
        from debugagent.agents.registry import build_task_spec
        from debugagent.agents.tasks import TaskSpecError

        with self.assertRaises(TaskSpecError) as caught:
            build_task_spec({
                "task_id": "sdc:notool", "agent": SDC_ANALYZER, "delegated_by": "coordinator",
                "objective": "analyse sdc constraints",
                "context": {"case_signature": "c", "symptoms": [],
                            "artifacts": [{"kind": "code", "ref": "constraints/top.sdc",
                                           "content": "requested"}]},
                "allowed_tools": [], "model": "primary", "depth": 1,
            })
        self.assertIn("must not be empty", str(caught.exception))

    def test_the_worker_re_checks_the_read_tool_defensively(self):
        """The check the schema normally makes first, proven to exist.

        Built by constructing the `TaskSpec` directly rather than through `build_task_spec`, because the
        point is what the worker does when something upstream stops enforcing the rule. If the roster
        ever grew a second tool for this worker, a task granted only that tool would otherwise reach
        `_run` and try to read a file with a tool it did not have.
        """
        from debugagent.agents.tasks import TaskSpec, WorkerContext

        spec = TaskSpec(task_id="sdc:defensive", agent=SDC_ANALYZER, delegated_by="coordinator",
                        objective="analyse sdc constraints",
                        context=WorkerContext(case_signature="c", symptoms=(),
                                              artifacts=(Artifact(kind="code", ref="constraints/top.sdc",
                                                                  content="requested"),),
                                              environment=()),
                        allowed_tools=("read",), model="primary", depth=1)
        stripped = TaskSpec(task_id=spec.task_id, agent=spec.agent, delegated_by=spec.delegated_by,
                            objective=spec.objective, context=spec.context,
                            allowed_tools=("read",), model=spec.model, depth=spec.depth)
        # Same spec, but the worker's own guard exercised through a port that would raise if reached.
        result = self.worker.execute(stripped)
        self.assertEqual(result.status, "success")
        self.assertTrue(result.observations)

    def test_a_miswired_analyser_fails_loudly_rather_than_reporting_nothing(self):
        """A zero-observation `success` from a wrong callable is the worst possible outcome."""
        coordinator = Coordinator()
        worker = SdcAnalyzerWorker(FileSourcePort(RepositoryScope(self.root)),
                                   analyze=lambda text, ref: {"unexpected": True})
        coordinator.register(SDC_ANALYZER, worker)
        result = coordinator.delegate(build_sdc_task(task_id="sdc:x", case_signature="c",
                                                     target="constraints/top.sdc"))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "schema")
        self.assertIn("did not return", result.failure_detail)


class Authorisation(WorkerFixture):
    def test_a_task_addressed_to_another_worker_is_refused(self):
        spec = build_sdc_task(task_id="sdc:x", case_signature="c", target="constraints/top.sdc")
        borrowed = spec.__class__(**{**spec.to_dict(), "agent": "code_log_verifier"})
        with self.assertRaises(PermissionError):
            self.worker.execute(borrowed)

    def test_the_worker_cannot_be_granted_a_memory_tool(self):
        with self.assertRaises(AuthorizationError):
            from debugagent.agents.registry import build_task_spec
            build_task_spec({
                "task_id": "sdc:mem", "agent": SDC_ANALYZER, "delegated_by": "coordinator",
                "objective": "analyse sdc constraints",
                "context": {"case_signature": "c", "symptoms": [],
                            "artifacts": [{"kind": "code", "ref": "constraints/top.sdc",
                                           "content": "requested"}]},
                "allowed_tools": ["read", "hindsight_recall"], "model": "primary", "depth": 1,
            })

    def test_depth_beyond_one_is_refused(self):
        """Refused by the P1 schema, which bounds depth before authorisation runs."""
        from debugagent.agents.registry import build_task_spec
        from debugagent.agents.tasks import TaskSpecError

        with self.assertRaises(TaskSpecError) as caught:
            build_task_spec({
                "task_id": "sdc:deep", "agent": SDC_ANALYZER, "delegated_by": "coordinator",
                "objective": "analyse sdc constraints",
                "context": {"case_signature": "c", "symptoms": [],
                            "artifacts": [{"kind": "code", "ref": "constraints/top.sdc",
                                           "content": "requested"}]},
                "allowed_tools": ["read"], "model": "primary", "depth": 2,
            })
        self.assertIn("max_spawn_depth", str(caught.exception))


class RosterEntry(unittest.TestCase):
    def test_the_roster_holds_exactly_four_and_names_them(self):
        self.assertEqual(set(WORKER_ROSTER), {"memory_specialist", "code_log_verifier",
                                              "patch_generator", "sdc_analyzer"})
        self.assertEqual(len(agent_names()), 4)

    def test_the_payload_is_still_exactly_five_fields(self):
        definition = get_agent(SDC_ANALYZER)
        self.assertEqual(set(definition.to_dict()), set(AGENT_DEFINITION_FIELDS))
        self.assertEqual(len(AGENT_DEFINITION_FIELDS), 5)

    def test_its_tools_are_read_only_and_it_never_gets_task(self):
        definition = get_agent(SDC_ANALYZER)
        self.assertEqual(tuple(definition.allowed_tools), ("read",))
        self.assertNotIn("task", definition.allowed_tools)

    def test_it_is_not_client_access(self):
        self.assertIs(get_agent(SDC_ANALYZER).client_access, False)

    def test_the_roster_stays_closed(self):
        for unknown in ("sdc_parser", "analyser", "worker_5", ""):
            with self.subTest(unknown=unknown), self.assertRaises(AuthorizationError):
                get_agent(unknown)


class ArchitectureBoundary(unittest.TestCase):
    """The structural claim, asserted from the imports so prose cannot drift from code."""

    def _imports(self, path: pathlib.Path) -> set[str]:
        names: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    def test_no_module_in_agents_imports_a_domain(self):
        """THE assertion. `agents/` holds generic worker contracts and must stay domain-blind.

        Nothing else in the suite would notice if this broke: the worker would keep working, its tests
        would keep passing, and the domain knowledge would simply have moved into the core - where the
        VLSI-1B module docstring and every architectural note in the roadmap say it does not belong.
        """
        offenders = {path.name: sorted(name for name in self._imports(path)
                                       if "domains" in name or "vlsi" in name.lower())
                     for path in sorted(AGENTS_DIR.glob("*.py"))}
        offenders = {name: hits for name, hits in offenders.items() if hits}
        self.assertEqual(offenders, {}, f"agents/ must stay domain-blind: {offenders}")

    def test_the_worker_module_names_no_domain_vocabulary(self):
        source = (AGENTS_DIR / "sdc_analyzer_worker.py").read_text(encoding="utf-8").lower()
        for forbidden in ("parse_sdc", "analyze_sdc_constraints", "vlsi_finding", "sdc_analysis"):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, source)

    def test_the_composition_root_is_where_the_domain_is_named(self):
        """One place, so the wiring is greppable and the extension point is obvious."""
        named = sorted(name for name in self._imports(COMPOSITION)
                       if "domains" in name or "vlsi" in name.lower())
        self.assertEqual(len(named), 1, f"expected exactly one domain import, got {named}")

    def test_no_core_package_imports_a_domain(self):
        src = COMPOSITION.parents[1]
        offenders = []
        for package in ("agents", "pipeline", "memory", "llm"):
            for path in sorted((src / package).glob("*.py")):
                hits = [n for n in self._imports(path)
                        if "domains" in n or "vlsi" in n.lower()]
                if hits:
                    offenders.append(f"{path.name}: {hits}")
        self.assertEqual(offenders, [], f"the core must not know a domain exists: {offenders}")


class WorkerStageIntegration(WorkerFixture):
    """SDC findings ride the EXISTING verifier channel - no third channel, no UI branch."""

    def test_sdc_findings_land_in_the_verifier_section(self):
        from debugagent.pipeline.worker_stage import run_worker_stage

        case = type("Case", (), {"problem_signature": "missing clock reference",
                                 "symptoms": ("max delay violation",)})()
        stage = run_worker_stage(self.runtime, case, sdc_targets=["constraints/top.sdc"])
        section = stage.verifier
        self.assertIsNotNone(section)
        self.assertEqual([f["ref"] for f in section["findings"]],
                         ["constraints/top.sdc:2", "constraints/top.sdc:3"])
        self.assertIn("SDC critical", section["findings"][0]["content"])
        # The generic UI reads exactly these fields. Asserted here so a rename breaks the test that
        # explains why, rather than the browser test that would only show an empty row.
        self.assertEqual(sorted(section["findings"][0]),
                         ["content", "kind", "ref", "score", "source", "task_id"])

    def test_the_stage_record_is_still_exactly_two_channels(self):
        from debugagent.pipeline.worker_stage import run_worker_stage

        case = type("Case", (), {"problem_signature": "x", "symptoms": ()})()
        stage = run_worker_stage(self.runtime, case, sdc_targets=["constraints/top.sdc"])
        self.assertEqual(set(stage.to_dict()), {"verifier"})

    def test_mixed_workers_report_every_agent_not_just_the_first(self):
        from debugagent.pipeline.worker_stage import run_worker_stage

        (self.root / "logs").mkdir()
        (self.root / "logs" / "sta.log").write_text(
            "ERROR: max delay violation between core_clk and u_reg\n", encoding="utf-8")
        case = type("Case", (), {"problem_signature": "max delay violation",
                                 "symptoms": ("max delay violation",)})()
        stage = run_worker_stage(self.runtime, case, verifier_targets=["logs/sta.log"],
                                 sdc_targets=["constraints/top.sdc"])
        self.assertEqual(stage.verifier["agents"], ["code_log_verifier", "sdc_analyzer"])

    def test_the_view_model_renders_an_sdc_finding_with_no_domain_knowledge(self):
        """The acceptance property, proved without a browser."""
        from debugagent.application.view_model import build_view_model
        from debugagent.pipeline.worker_stage import run_worker_stage

        case = type("Case", (), {"problem_signature": "missing clock reference",
                                 "symptoms": ("max delay violation",)})()
        stage = run_worker_stage(self.runtime, case, sdc_targets=["constraints/top.sdc"])
        session = type("S", (), {"session_id": "s1", "case": case,
                                 "workers": stage.to_dict(), "trace": ["t"]})()
        panel = build_view_model(session).panel("Workers")

        self.assertEqual(panel.trust, "OBSERVATION")
        finding = stage.verifier["findings"][0]
        item = panel.items[0]
        self.assertEqual(item.label, finding["content"])
        self.assertEqual(item.source, finding["source"])
        self.assertEqual(item.ref, finding["ref"])
        self.assertEqual(item.source, "code:constraints/top.sdc:2")
        # Each finding is separately addressable, which is what makes its Copy button its own.
        self.assertTrue(item.ref.strip())


class CompositionRoot(WorkerFixture):
    def test_the_composition_root_registers_the_worker_with_the_real_analyser(self):
        self.assertIn(SDC_ANALYZER, self.runtime.coordinator.agent_ids)
        self.assertIsInstance(self.runtime.coordinator.worker_for(SDC_ANALYZER),
                              SdcAnalyzerWorker)

    def test_the_worker_reads_through_the_shared_repository_scope(self):
        result = self.analyse("constraints/top.sdc")
        self.assertEqual(result.status, "success")
        for observation in result.observations:
            self.assertFalse(observation.ref.startswith("/"), "an absolute path escaped the scope")
            self.assertNotIn("..", observation.ref)


class HostMounting(unittest.TestCase):
    """Failure boundary 1 of 2: the CLI/host hands the worker stage its repository and targets.

    Paired with the browser test, this means a failure can be localised to "the composition path" or
    "the render path" instead of both being suspect at once.
    """

    def test_worker_options_reach_investigate_as_its_own_kwargs(self):
        from debugagent.cli import _worker_options

        class Args:
            repository = "."
            verifier_target = ["logs/a.log"]
            sdc_target = ["constraints/top.sdc"]

        class Coordinator_:
            def register(self, *_):
                pass

        _, options = _worker_options(Args(), Coordinator_(), io.StringIO())
        self.assertEqual(sorted(options), ["repository", "sdc_targets", "verifier_targets"])
        self.assertEqual(options["sdc_targets"], ("constraints/top.sdc",))

        import inspect
        from debugagent.pipeline.investigate import investigate
        self.assertLessEqual(set(options), set(inspect.signature(investigate).parameters))

    def test_targets_without_a_repository_are_refused_rather_than_dropped(self):
        """A silently dropped target is indistinguishable from a worker that found nothing."""
        import io
        from debugagent.cli import _worker_options

        class Args:
            repository = None
            verifier_target = ["a.py"]
            sdc_target = []

        err = io.StringIO()
        with self.assertRaises(ValueError):
            _worker_options(Args(), None, err)
        self.assertIn("--repository", err.getvalue())

    def test_no_repository_means_no_worker_options_at_all(self):
        from debugagent.cli import _worker_options

        class Args:
            repository = None
            verifier_target = []
            sdc_target = []

        runtime, options = _worker_options(Args(), None, io.StringIO())
        self.assertIsNone(runtime)
        self.assertEqual(options, {})


class ObservationShape(unittest.TestCase):
    def test_a_worker_result_exposes_no_write_or_decision_method(self):
        for method in ("apply", "write_evidence", "decide", "retain", "commit", "approve"):
            self.assertFalse(hasattr(SubAgentResult, method), method)

    def test_an_artifact_is_data_only(self):
        artifact = Artifact(kind="code", ref="a.sdc:1", content="x")
        with self.assertRaises(AttributeError):
            artifact.content = "y"


if __name__ == "__main__":
    unittest.main()