"""UI-1: the session view model.

Two properties matter more than the panel contents, and both are tested here rather than asserted in
prose.

**Domain neutrality is structural.** The view model imports nothing from `debugagent.domains` and reads
a session through its attributes, so the panels cannot acquire a domain opinion. Tested by scanning the
module's imports and by projecting two structurally different sessions through it.

**Absent is not empty is not "found nothing".** A stage that did not run produces no panel; a stage that
ran and reported nothing produces a panel that says so. Collapsing those three states is how a UI starts
telling an engineer things the investigation never did - and it is what would make a VLSI-shaped UI out
of a domain-neutral one.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from debugagent.application.view_model import (
    PANEL_ORDER,
    TRUST_LEVELS,
    Panel,
    PanelItem,
    SessionViewModel,
    ViewModelError,
    build_view_model,
)

from debugagent.pipeline.worker_stage import RankedFinding

APP_DIR = Path(__file__).resolve().parents[1] / "src" / "debugagent" / "application"
VIEWS = APP_DIR / "view_model.py"


class FakeSession:
    """A session-shaped object with only the attributes set.

    Built this way rather than by running an investigation, which is the point: the view model works on
    attributes alone, so a test can hand it any shape and be sure nothing domain-specific is involved.
    """

    def __init__(self, session_id="s1", *, case=None, memory=None, evidence=None, workers=None,
                 proposal=None, verifications=(), resolution=None, retention=None,
                 memory_delegation=None, trace=()):
        self.session_id = session_id
        self.case = case
        self.memory = memory
        self.evidence = evidence
        self.workers = workers
        self.proposal = proposal
        self.verifications = list(verifications)
        self.resolution = resolution
        self.retention = retention
        self.memory_delegation = memory_delegation
        self.trace = list(trace)


class Obj:
    """A minimal attribute bag for building test fixtures."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


def ranked_finding(*, ref="logs/a.log", kind="log", content="saw X", source="log:logs/a.log",
                   score=1, task_id="verify:logs/a.log"):
    """A worker finding built by the REAL `RankedFinding`, not by a dict typed here.

    This helper exists because of a defect it would have caught. The Workers panel used to read
    `finding["message"]` and `finding["provenance"]["ref"]`, neither of which is a key of
    `RankedFinding.to_dict()` - they are names from the VLSI `VlsiFinding` and from nowhere in the
    worker record. Every real finding therefore rendered as an empty row with a score and no
    provenance, and every test over that panel passed anyway, because each one spelled its fixture out
    by hand in the same wrong shape and so agreed with the bug.

    Constructing the dataclass instead means a rename on the worker side now breaks these tests loudly
    rather than silently producing a second, parallel schema that only the tests believe in. It also
    means `test_a_real_ranked_finding_reaches_the_workers_panel` cannot rot: it asserts against the
    same `to_dict()` output production consumes.

    Importing `worker_stage` here is deliberate and does not compromise domain neutrality - it is a
    generic pipeline module, and the neutrality tests below scan `application/` rather than this file.
    """
    return RankedFinding(kind=kind, ref=ref, content=content, score=score,
                         task_id=task_id, source=source).to_dict()


class DomainNeutrality(unittest.TestCase):
    """The view model must not know what a domain is."""

    def test_view_model_imports_no_domain_code(self):
        tree = ast.parse(VIEWS.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for name in sorted(imported):
            with self.subTest(imported=name):
                self.assertNotIn("domains", name)
                for forbidden in ("vlsi", "sdc", "sta", "hindsight", "llm", "coordinator"):
                    self.assertNotIn(forbidden, name.lower())

    def test_the_application_package_names_no_domain(self):
        for module in sorted(APP_DIR.glob("*.py")):
            source = module.read_text(encoding="utf-8").lower()
            for forbidden in ("vlsi", "sdc", "opensta", "primetime", "signoff", "cts"):
                with self.subTest(module=module.name, term=forbidden):
                    # Documentation may name a domain to say it is NOT here; code may not import it.
                    self.assertNotIn(f"import {forbidden}", source)
                    self.assertNotIn(f"from debugagent.domains.{forbidden}", source)

    def test_a_generic_session_and_a_vlsi_shaped_session_produce_the_same_panel_slots(self):
        """The panels are the SAME fixed slots; only which ones are populated differs.

        A VLSI session is simulated by giving the workers record a shape the generic one does not have.
        The view model must not gain a panel, rename one, or infer a domain from it - so every panel
        either session produces must be one of the seven documented slots.
        """
        generic = build_view_model(FakeSession(case=Obj(problem_signature="billing retry duplicate"),
                                              trace=["normalized"]))
        vlsi_shaped = build_view_model(FakeSession(
            case=Obj(problem_signature="setup timing violation"),
            workers={"verifier": {"findings": [ranked_finding(
                                        ref="constraints/top.sdc", kind="code",
                                        content="no create_clock for 'core_clk'",
                                        source="code:constraints/top.sdc", score=2)],
                                  "summary": {"total": 1, "joined": True},
                                  "refusals": []},
                    "patches": {"proposals": []}},
            trace=["normalized"]))
        for model, label in ((generic, "generic"), (vlsi_shaped, "vlsi-shaped")):
            with self.subTest(session=label):
                self.assertTrue(set(model.panel_names) <= set(PANEL_ORDER),
                                f"{label} produced a panel outside the fixed set: {model.panel_names}")
        # The difference is only in which slots are populated - never in what a slot is called.
        self.assertEqual(set(vlsi_shaped.panel_names) - set(generic.panel_names), {"Workers"})
        self.assertEqual(set(generic.panel_names) - set(vlsi_shaped.panel_names), set())

    def test_the_core_stays_domain_blind_and_ui_blind(self):
        """No core module may import the application layer or know a domain exists."""
        core = [Path("src/debugagent") / part
                for part in ("agents", "pipeline", "memory", "llm")]
        for package in core:
            for module in sorted(package.glob("*.py")):
                source = module.read_text(encoding="utf-8")
                with self.subTest(module=module.name):
                    self.assertNotIn("debugagent.application", source,
                                     f"{module.name} imports the application layer")
                    for forbidden in ("domains.vlsi", "vlsi", "sdc_parser", "sdc_analyzer"):
                        self.assertNotIn(f"import {forbidden}", source)


class TrustLabels(unittest.TestCase):
    """Every panel says which authority produced its contents."""

    def test_trust_vocabulary_is_closed(self):
        self.assertEqual(TRUST_LEVELS,
                         ("KNOWLEDGE", "EVIDENCE", "OBSERVATION", "PROPOSAL", "DECISION", "TRACE"))

    def test_trace_is_not_forced_into_a_claim_class(self):
        """A trace entry says the system ran a stage, not that the stage's output is true."""
        model = build_view_model(FakeSession(trace=["normalized: x", "evidence: 2 known"]))
        self.assertEqual(model.panel("Trace").trust, "TRACE")

    def test_each_panel_carries_the_expected_trust(self):
        model = build_view_model(FakeSession(
            memory=Obj(query="q", abstained=False, candidates=[], excluded=[], abstention={}),
            evidence=Obj(items=[Obj(name="proxy", value="nginx-1.25", source="engineer")],
                         unknown_fields=[]),
            proposal=Obj(hypotheses=[Obj(ref="H1", hypothesis="x", supporting_case_ids=[],
                                         relevance_state="likely", recommended_next_step="s")]),
            verifications=[Obj(hypothesis_ref="H1", status="insufficient_evidence",
                               engineer_decision="accept", relevance_confirmed=True,
                               mismatched_environment_fields=[], engineer_note="n")],
            resolution=Obj(outcome="resolved", root_cause_confirmed=True, action_taken="a",
                           observed_result="r"),
            trace=["t"]))
        self.assertEqual(model.panel("Memory").trust, "KNOWLEDGE")
        self.assertEqual(model.panel("Evidence").trust, "EVIDENCE")
        self.assertEqual(model.panel("Reasoning").trust, "PROPOSAL")
        self.assertEqual(model.panel("Validation").trust, "EVIDENCE")
        self.assertEqual(model.panel("Decision").trust, "DECISION")

    def test_a_patch_proposal_inside_the_workers_panel_is_marked_proposal(self):
        """One panel holds two kinds of content, so the item refines the panel."""
        model = build_view_model(FakeSession(workers={
            "verifier": {"findings": [ranked_finding(ref="a.py", kind="code", content="saw X",
                                                     source="code:a.py")],
                         "summary": {"total": 1, "joined": True}, "refusals": []},
            "patches": {"proposals": [{"observations": [{"ref": "a.py", "content": "diff body"}]}]}}))
        workers = model.panel("Workers")
        self.assertEqual(workers.trust, "OBSERVATION")
        # Effective trust: an item inherits its panel's level unless it states its own.
        effective = {item.trust or workers.trust for item in workers.items}
        self.assertEqual(effective, {"OBSERVATION", "PROPOSAL"})

    def test_an_item_states_trust_only_when_it_differs_from_its_panel(self):
        """A label that merely repeats its panel is noise, and noise here is corrosive."""
        model = build_view_model(FakeSession(workers={
            "verifier": {"findings": [ranked_finding(ref="a.py", kind="code", content="saw X",
                                                     source="code:a.py")],
                         "summary": {"total": 1, "joined": True}, "refusals": []}}, trace=["t"]))
        for panel in model.panels:
            for item in panel.items:
                with self.subTest(panel=panel.name, item=item.label):
                    if item.trust is not None:
                        self.assertNotEqual(item.trust, panel.trust,
                                            f"{panel.name}/{item.label} restates its panel's trust")

    def test_an_invalid_trust_is_rejected(self):
        with self.assertRaises(ViewModelError):
            Panel.parse({"name": "X", "trust": "PROBABLY_FINE"})

    def test_no_panel_makes_a_verdict_about_correctness(self):
        """A panel may not assert the design is fine; only the engineer concludes."""
        model = build_view_model(FakeSession(
            evidence=Obj(items=[Obj(name="a", value="1", source="engineer")], unknown_fields=[]),
            trace=["t"]))
        blob = str(model.to_dict()).lower()
        for verdict in ("design is correct", "timing met", "signoff", "no problems", "all clear"):
            self.assertNotIn(verdict, blob)


class FailedWorkerTasks(unittest.TestCase):
    """A task that did not complete must not read as a task that found nothing.

    This is the third instance of the same shape of defect in this panel: it read `finding["message"]`
    and `finding["provenance"]`, neither of which is a key of `RankedFinding.to_dict()`; and now it read
    `findings` and `refusals` while ignoring `outcomes`, which is where a worker's failure lives.

    The reported symptom was a Workers panel reading "code / log verifier - no observation reported (1
    task(s) completed)" for a session whose only task had failed with "target absent". Every word of that
    line was false: the task completed nothing, it did not complete, and the worker was not the code/log
    verifier. The record had the truth in `outcomes` the whole time.
    """

    @staticmethod
    def _session(outcomes, *, findings=(), summary=None, refusals=()):
        section = {
            "findings": list(findings),
            "refusals": list(refusals),
            "outcomes": list(outcomes),
            "summary": summary or {"total": len(outcomes), "joined": True,
                                   "by_status": {}, "order": []},
        }
        return build_view_model(FakeSession(workers={"verifier": section}, trace=["t"]))

    @staticmethod
    def _failed(task_id="sdc:constraints/top.sdc", agent="sdc_analyzer",
                detail="target absent: constraints/top.sdc", kind="unavailable"):
        return {"index": 0, "task_id": task_id, "agent": agent, "status": "failed",
                "result": {"task_id": task_id, "agent": agent, "status": "failed",
                           "observations": [], "failure_kind": kind, "failure_detail": detail},
                "error": None}

    def test_a_failed_task_is_surfaced_with_its_reason(self):
        panel = self._session([self._failed()]).panel("Workers")
        text = str(panel.to_dict())
        self.assertIn("did not complete", text, "a failed task must be visible")
        self.assertIn("target absent: constraints/top.sdc", text, "and must say why")
        self.assertIn("unavailable", text, "including the failure kind")

    def test_a_failed_task_does_not_render_as_found_nothing(self):
        """The exact false claim from the report."""
        text = str(self._session([self._failed()]).panel("Workers").to_dict())
        self.assertNotIn("no observation reported", text,
                         "'found nothing' and 'could not look' must not share a wording")

    def test_a_failed_task_does_not_render_as_task_s_completed(self):
        text = str(self._session([self._failed()]).panel("Workers").to_dict())
        self.assertNotIn("task(s) completed", text)

    def test_the_working_worker_is_named_rather_than_assumed(self):
        """The empty-state label used to hardcode one worker regardless of who actually ran."""
        text = str(self._session([self._failed()]).panel("Workers").to_dict())
        self.assertIn("sdc_analyzer", text, "the worker that ran must be named")
        self.assertNotIn("code / log verifier", text)

    def test_a_clean_run_still_reports_nothing_found(self):
        """The other direction: a genuinely clean run must NOT be dressed up as a failure."""
        outcome = {"index": 0, "task_id": "sdc:top.sdc", "agent": "sdc_analyzer",
                   "status": "success", "result": {"status": "success", "observations": []},
                   "error": None}
        text = str(self._session([outcome]).panel("Workers").to_dict())
        self.assertIn("no observation reported", text)
        self.assertIn("1 task(s) completed", text)
        self.assertNotIn("did not complete", text)

    def test_a_section_without_outcomes_is_unaffected(self):
        """Older and hand-built records carry no `outcomes`; they must behave exactly as before."""
        model = build_view_model(FakeSession(workers={
            "verifier": {"findings": [], "summary": {"total": 2, "joined": True}, "refusals": []},
            "patches": {"proposals": []}}, trace=["t"]))
        text = str(model.panel("Workers").to_dict())
        self.assertIn("no observation reported", text)
        self.assertIn("2 task(s) completed", text)

    def test_findings_and_a_failure_are_shown_together(self):
        """One target of three failed; the other two produced observations. Both belong in the report."""
        good = {"ref": "a.log", "content": "saw X", "score": 1, "source": "log:a.log"}
        panel = self._session([self._failed(task_id="sdc:gone.sdc"),
                               {"index": 1, "task_id": "sdc:ok.sdc", "agent": "sdc_analyzer",
                                "status": "success", "result": {"status": "success"}, "error": None}],
                              findings=[good]).panel("Workers")
        text = str(panel.to_dict())
        self.assertIn("saw X", text, "successful observations still shown")
        self.assertIn("did not complete", text, "the failure is not hidden by the successes")
        self.assertNotIn("no observation reported", text)

    def test_a_worker_that_died_reports_its_error(self):
        outcome = {"index": 0, "task_id": "v:crash", "agent": "code_log_verifier",
                   "status": "error", "result": None, "error": "RuntimeError: worker crashed"}
        text = str(self._session([outcome]).panel("Workers").to_dict())
        self.assertIn("did not complete", text)
        self.assertIn("worker crashed", text)

    def test_a_refusal_keeps_its_own_wording_and_is_not_reported_as_a_failure(self):
        """`refused` is a rule saying no; `failed` is a thing going wrong. They must stay distinct."""
        refusal = {"task_id": "v:a.log", "error": "AuthorizationError: denied"}
        text = str(self._session(
            [{"index": 0, "task_id": "v:a.log", "agent": "code_log_verifier",
              "status": "refused", "result": None, "error": "AuthorizationError: denied"}],
            refusals=[refusal]).panel("Workers").to_dict())
        self.assertIn("refused", text)
        self.assertNotIn("did not complete", text,
                         "a refusal is not a task that failed to complete")

    def test_a_partial_task_is_not_reported_as_a_failure(self):
        """`partial` ran and reported; it is milder than a task that never looked."""
        outcome = {"index": 0, "task_id": "sdc:top.sdc", "agent": "sdc_analyzer",
                   "status": "partial", "result": {"status": "partial"}, "error": None}
        text = str(self._session([outcome]).panel("Workers").to_dict())
        self.assertNotIn("did not complete", text)
        self.assertIn("only partly", text, "but it is still reported, in its own words")

    def test_a_partial_task_does_not_render_as_found_nothing(self):
        """A partial read is not a clean run, and its observations are NOT among the findings.

        `SubAgentResult.ok` is `status == "success"`, and `rank_findings` is fed only `ok` outcomes, so
        a partial task's observations never reach this panel at all. Rendering it as "no observation
        reported (1 task(s) completed)" would therefore be doubly false: it claims a clean run, and it
        hides observations that exist. This test was written after that exact case was found.
        """
        outcome = {"index": 0, "task_id": "sdc:top.sdc", "agent": "sdc_analyzer",
                   "status": "partial", "result": {"status": "partial"}, "error": None}
        text = str(self._session([outcome]).panel("Workers").to_dict())
        self.assertNotIn("no observation reported", text)
        self.assertNotIn("task(s) completed", text)

    def test_a_partial_task_is_not_given_an_invented_reason(self):
        """A partial run has nothing to explain; "reported no reason" would be a second false claim."""
        outcome = {"index": 0, "task_id": "sdc:top.sdc", "agent": "sdc_analyzer",
                   "status": "partial", "result": {"status": "partial"}, "error": None}
        text = str(self._session([outcome]).panel("Workers").to_dict())
        self.assertNotIn("reported no reason", text)
        self.assertIn("provisional", text)

    def test_partial_findings_alongside_a_clean_sibling_are_both_shown(self):
        """A successful task's findings render normally even when another task was partial."""
        good = {"ref": "a.log", "content": "saw X", "score": 1, "source": "log:a.log"}
        text = str(self._session(
            [{"index": 0, "task_id": "sdc:half", "agent": "sdc_analyzer", "status": "partial",
              "result": {"status": "partial"}, "error": None},
             {"index": 1, "task_id": "sdc:ok", "agent": "sdc_analyzer", "status": "success",
              "result": {"status": "success"}, "error": None}],
            findings=[good]).panel("Workers").to_dict())
        self.assertIn("saw X", text)
        self.assertIn("only partly", text)
        self.assertNotIn("no observation reported", text)

    def test_no_failure_reason_is_invented(self):
        outcome = {"index": 0, "task_id": "v:x", "agent": "code_log_verifier",
                   "status": "failed", "result": None, "error": None}
        text = str(self._session([outcome]).panel("Workers").to_dict())
        self.assertIn("did not complete", text)
        self.assertIn("reported no reason", text,
                      "with nothing recorded the panel must say so rather than invent a cause")


ISSUE = ("SDC timing constraints are inconsistent: an input delay names an undefined clock "
         "and core_clk has conflicting definitions\nservice=timing\nregion=local\n"
         "setup timing fails on the main clock path\n"
         "max delay violation between core_clk and the registers")


class WorkerStageTraceWording(unittest.TestCase):
    """The progress line must not report a broken stage as a clean one.

    Separate from the panel because it is a different surface with a different consumer: the trace is
    what an engineer reads to decide whether the worker stage did anything, and "0 finding(s) of 0, 0
    refusal(s)" is both true and useless when the only task died. Rendered through a real
    `run_worker_stage` rather than a hand-built dict, so the assertion cannot drift from the record the
    pipeline actually produces.
    """

    @staticmethod
    def _trace_for(files: dict, *, targets):
        """Capture the REAL line `investigate()` writes, by driving the real `step()` callback.

        Reassembling the wording here would make these tests assert a copy of the logic instead of the
        logic itself, and an earlier version did exactly that - it kept passing while `investigate()` was
        mutated. `investigate()` takes `on_step`, so the line can simply be captured rather than
        reconstructed, and the test then fails if the production string ever diverges.
        """
        import sys
        import tempfile

        if str(Path(__file__).resolve().parents[1] / "tests") not in sys.path:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

        from loop_support import RESOLVED, FakeLLM, FakeMemoryPort, ScriptedEngineer, hyp

        from debugagent.agents.coordinator import Coordinator
        from debugagent.composition import build_repository_runtime
        from debugagent.pipeline.investigate import investigate
        from debugagent.pipeline.normalize import normalize
        from debugagent.pipeline.recall_match import recall
        from debugagent.pipeline.types import DebugInput

        lines: list[str] = []
        raw = DebugInput(description=ISSUE)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, body in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body, encoding="utf-8")

            port = FakeMemoryPort()
            # The same `recall()` the flow itself uses, so the cited case ids are the ones the pipeline
            # would really see. Guessing them would make `verify()`'s schema reject the proposal and the
            # run would stop before the worker stage, which is the line under test.
            context = recall(port, normalize(raw))
            cites = [] if context.abstained else [c["case_id"] for c in context.candidates][:1]
            proposal = {"hypotheses": [
                hyp(text="an input delay names a clock that is never defined", cites=cites),
                hyp(text="core_clk is defined twice with different periods", cites=cites)]}

            # One Coordinator for the whole run: a second one over the same client would mean a second
            # MemoryLane, which `investigate()` refuses for exactly that reason.
            coordinator = Coordinator()
            session = investigate(raw, port, FakeLLM(proposal),
                                  ScriptedEngineer(resolution=RESOLVED),
                                  coordinator=coordinator,
                                  repository=build_repository_runtime(root, coordinator=coordinator),
                                  sdc_targets=targets,
                                  on_step=lambda s: lines.append(s.trace[-1]))

        verifier = [line for line in lines if line.startswith("verifier:")]
        assert len(verifier) == 1, f"expected one verifier trace line, got {verifier}"
        statuses = [o.get("status") for o in session.workers["verifier"]["outcomes"]]
        return verifier[0].removeprefix("verifier: "), statuses

    def test_a_clean_stage_trace_is_byte_identical_to_before(self):
        """The clause is purely additive, so a clean run must read exactly as it always did."""
        line, statuses = self._trace_for(
            {"constraints/top.sdc":
             "create_clock -name core_clk -period 10 [get_ports clk]\n"
             "create_clock -name core_clk -period 12 [get_ports clk]\n"},
            targets=["constraints/top.sdc"])
        self.assertEqual(statuses, ["success"])
        # The counts come from the record rather than being pinned, so the test asserts the SHAPE - no
        # clause appended, same wording as before - and does not break when the analyzer gains a check.
        self.assertRegex(
            line, r"^\d+ finding\(s\) of \d+, 0 refusal\(s\) - observations, not evidence$",
            "a clean stage must not gain a clause it does not need")
        self.assertNotIn("did not finish cleanly", line)

    def test_a_failed_task_is_named_in_the_trace(self):
        line, statuses = self._trace_for({}, targets=["constraints/top.sdc"])
        self.assertEqual(statuses, ["failed"])
        self.assertIn("1 task(s) did not finish cleanly", line)
        self.assertIn("0 finding(s) of 0", line,
                      "the finding count stays truthful; the clause is additive")

    def test_a_partial_task_is_named_in_the_trace(self):
        line, statuses = self._trace_for(
            {"constraints/top.sdc":
             "create_clock -name core_clk -period 10 [get_ports clk]\ncreate_clock -name\n"},
            targets=["constraints/top.sdc"])
        self.assertEqual(statuses, ["partial"])
        self.assertIn("1 task(s) did not finish cleanly", line)


class PanelPresence(unittest.TestCase):
    """Absent, empty, and "found nothing" are three different things."""

    def test_a_session_with_no_worker_stage_has_no_workers_panel(self):
        model = build_view_model(FakeSession(trace=["normalized"]))
        self.assertIsNone(model.panel("Workers"))
        self.assertNotIn("Workers", model.panel_names)

    def test_absent_is_not_replaced_by_an_absence_notice(self):
        """The UI must not imply every investigation has workers, or that a domain exists."""
        model = build_view_model(FakeSession(trace=["normalized"]))
        blob = str(model.to_dict()).lower()
        for phrase in ("no vlsi", "no workers available", "not available", "unsupported domain"):
            self.assertNotIn(phrase, blob)

    def test_a_worker_stage_that_reported_nothing_still_has_a_panel(self):
        """This is the distinction that matters: the stage RAN and found nothing.

        The panel says so explicitly rather than staying silent, because an empty panel and an absent
        panel are different claims and the engineer has to be able to tell them apart.
        """
        model = build_view_model(FakeSession(workers={
            "verifier": {"findings": [], "summary": {"total": 2, "joined": True}, "refusals": []},
            "patches": {"proposals": []}}, trace=["t"]))
        panel = model.panel("Workers")
        self.assertIsNotNone(panel, "a worker stage that ran produced no panel")
        text = str(panel.to_dict())
        self.assertIn("no observation reported", text)
        self.assertIn("no proposal produced", text)
        self.assertIn("2 task(s) completed", text)

    def test_memory_abstention_is_shown_not_hidden(self):
        """Abstention is a result. Omitting it would read as memory not being asked."""
        model = build_view_model(FakeSession(memory=Obj(
            query="billing retry", abstained=True, candidates=[], excluded=[],
            abstention={"reason": "below threshold"}), trace=["t"]))
        panel = model.panel("Memory")
        self.assertFalse(panel.empty)
        self.assertIn("abstained", str(panel.to_dict()))
        self.assertIn("below threshold", str(panel.to_dict()))

    def test_an_empty_session_produces_no_panels(self):
        model = build_view_model(FakeSession())
        self.assertEqual(model.panels, ())

    def test_panels_appear_in_the_documented_inspection_order(self):
        model = build_view_model(FakeSession(
            trace=["t"],
            memory=Obj(query="q", abstained=False, candidates=[], excluded=[], abstention={}),
            evidence=Obj(items=[], unknown_fields=[]),
            proposal=Obj(hypotheses=[]),
            verifications=[Obj(hypothesis_ref="H1", status="supported", engineer_decision="accept",
                               relevance_confirmed=True, mismatched_environment_fields=[], engineer_note="")],
            resolution=Obj(outcome="resolved", root_cause_confirmed=True, action_taken="a",
                           observed_result="r")))
        names = list(model.panel_names)
        positions = [PANEL_ORDER.index(name) for name in names]
        self.assertEqual(positions, sorted(positions), f"panels out of inspection order: {names}")


class Fidelity(unittest.TestCase):
    """A projection must not lose the information the engineer needs."""

    def test_evidence_items_carry_name_value_and_source(self):
        model = build_view_model(FakeSession(evidence=Obj(
            items=[Obj(name="proxy", value="nginx-1.25", source="engineer"),
                   Obj(name="runtime", value=None, source="engineer")],
            unknown_fields=["runtime"]), trace=["t"]))
        items = model.panel("Evidence").items
        self.assertEqual([item.label for item in items], ["proxy", "runtime"])
        self.assertEqual(items[0].source, "engineer")
        self.assertIsNone(items[1].value, "an unstated fact was given a value")

    def test_what_is_not_known_is_a_note_not_an_item(self):
        """An item with no source is an assertion, and this panel must not contain one.

        The summary of unknown fields belongs in the panel note; putting it in the item list would give
        the EVIDENCE panel a line that no source supports.
        """
        model = build_view_model(FakeSession(evidence=Obj(
            items=[Obj(name="proxy", value="nginx-1.25", source="engineer")],
            unknown_fields=["runtime", "region"]), trace=["t"]))
        panel = model.panel("Evidence")
        self.assertEqual([item.label for item in panel.items], ["proxy"])
        self.assertIn("region, runtime", panel.note, "the note should list unknowns in a stable order")

    def test_every_evidence_item_names_its_source(self):
        """The invariant the panel exists to keep: an evidence item is a fact plus where it came from."""
        model = build_view_model(FakeSession(evidence=Obj(
            items=[Obj(name="proxy", value="nginx-1.25", source="engineer"),
                   Obj(name="runtime", value=None, source="engineer"),
                   Obj(name="observation", value="5 retries", source="engineer")],
            unknown_fields=["runtime"]), trace=["t"]))
        for item in model.panel("Evidence").items:
            with self.subTest(item=item.label):
                self.assertTrue(item.source, f"{item.label} is an evidence item with no source")

    def test_a_real_ranked_finding_reaches_the_workers_panel_intact(self):
        """RankedFinding -> to_dict() -> _workers_panel() -> PanelItem, with nothing dropped.

        This is the regression test for a shipped defect. `_workers_panel` read `message` and
        `provenance.ref`; `RankedFinding.to_dict()` emits `content` and `source`. Every finding
        therefore rendered as an empty row with a score and no provenance, and the tests over this
        panel all passed because each spelled its fixture out by hand in the same wrong shape.

        The fixture is built from the real dataclass (see `ranked_finding`), so this asserts against
        the exact structure production consumes rather than a schema only the tests believe in. If the
        worker's record is renamed, this fails instead of going quietly blank in the browser.
        """
        finding = ranked_finding(ref="constraints/top.sdc", kind="code", score=2,
                                 content="no create_clock for 'core_clk'",
                                 source="code:constraints/top.sdc", task_id="sdc:constraints/top.sdc")
        model = build_view_model(FakeSession(workers={
            "verifier": {"findings": [finding],
                         "summary": {"total": 1, "joined": True}, "refusals": []}}, trace=["t"]))

        panel = model.panel("Workers")
        self.assertIsNotNone(panel, "a session with a worker record must have a Workers panel")
        item = panel.items[0]

        # The three fields that carry the finding, asserted against the same keys production reads.
        self.assertEqual(item.label, finding["content"], "the finding text did not reach the panel")
        self.assertEqual(item.source, finding["source"], "the provenance did not reach the panel")
        self.assertEqual(item.ref, finding["ref"], "the item identity did not reach the panel")

        # And explicitly not the names the bug used, so a reintroduction is a named failure.
        self.assertNotEqual(item.label, finding.get("message", ""), "message is not a finding key")
        self.assertNotIn("provenance", finding, "RankedFinding carries no nested provenance")
        self.assertTrue(item.label.strip(), "a finding must not render as an empty row")
        self.assertTrue(item.source.strip(), "a finding must not render without provenance")

    def test_the_source_keeps_its_artifact_prefix_rather_than_being_stripped_back_to_the_ref(self):
        """`code:` / `log:` says the observation did not come from the engineer, so it stays visible.

        `ARTIFACT_SOURCE_PREFIX` exists so a tool-derived observation can never claim to be
        engineer-stated. Displaying the bare `ref` would keep the location and drop that distinction,
        which is the one piece of provenance the engineer cannot reconstruct for themselves.
        """
        for kind, prefix in (("code", "code:"), ("log", "log:")):
            with self.subTest(kind=kind):
                finding = ranked_finding(ref="app/server.py", kind=kind,
                                         source=f"{prefix}app/server.py")
                model = build_view_model(FakeSession(workers={
                    "verifier": {"findings": [finding],
                                 "summary": {"total": 1, "joined": True}, "refusals": []}},
                    trace=["t"]))
                item = model.panel("Workers").items[0]
                self.assertEqual(item.source, f"{prefix}app/server.py")
                self.assertNotEqual(item.source, item.ref, "the prefix was stripped")

    def test_worker_refusals_are_visible(self):
        model = build_view_model(FakeSession(workers={
            "verifier": {"findings": [], "summary": {"total": 1, "joined": True},
                         "refusals": [{"task_id": "verify:a.py", "error": "denied"}]}}, trace=["t"]))
        self.assertIn("refused", str(model.panel("Workers").to_dict()))

    def test_a_broken_join_barrier_is_surfaced(self):
        model = build_view_model(FakeSession(workers={
            "verifier": {"findings": [], "summary": {"total": 2, "joined": False}, "refusals": []}},
            trace=["t"]))
        self.assertIn("join barrier", str(model.panel("Workers").to_dict()))

    def test_resolution_and_retention_appear_in_the_decision_panel(self):
        model = build_view_model(FakeSession(
            resolution=Obj(outcome="resolved", root_cause_confirmed=True, action_taken="raised limit",
                           observed_result="no failures"),
            retention={"retained": True, "reason": "case resolved"}, trace=["t"]))
        panel = model.panel("Decision")
        text = str(panel.to_dict())
        self.assertIn("raised limit", text)
        self.assertIn("retained", text)

    def test_title_falls_back_when_there_is_no_case(self):
        session = FakeSession()
        session.raw = Obj(description="the retry loop drops the invoice\nsecond line")
        self.assertEqual(build_view_model(session).title, "the retry loop drops the invoice")

    def test_trace_lines_are_numbered_in_order(self):
        """`ref` carries the position and `label` the line, so the line is selectable on its own."""
        model = build_view_model(FakeSession(trace=["first", "second", "third"]))
        items = model.panel("Trace").items
        self.assertEqual([item.ref for item in items], ["T01", "T02", "T03"])
        self.assertEqual([item.label for item in items], ["first", "second", "third"])


class SerialisationAndStatus(unittest.TestCase):
    """Deterministic serialisation, and a status that describes the run rather than its findings."""

    def test_view_model_round_trips(self):
        model = build_view_model(FakeSession(
            case=Obj(problem_signature="x"),
            evidence=Obj(items=[Obj(name="a", value="1", source="engineer")], unknown_fields=[]),
            workers={"verifier": {"findings": [], "summary": {"total": 1, "joined": True},
                                  "refusals": []}},
            trace=["t"]), status="running")
        self.assertEqual(SessionViewModel.parse(model.to_dict()), model)

    def test_repeated_projection_is_identical(self):
        session = FakeSession(
            case=Obj(problem_signature="x"),
            evidence=Obj(items=[Obj(name="a", value="1", source="engineer")], unknown_fields=[]),
            trace=["t"])
        payloads = {str(build_view_model(session).to_dict()) for _ in range(20)}
        self.assertEqual(len(payloads), 1, "the projection is not deterministic")

    def test_a_complete_run_with_no_findings_is_complete_not_failed(self):
        model = build_view_model(FakeSession(trace=["t"]), status="complete")
        self.assertEqual(model.status, "complete")

    def test_a_failed_run_is_reported_as_failed(self):
        self.assertEqual(build_view_model(FakeSession(), status="failed").status, "failed")

    def test_an_unknown_status_is_rejected(self):
        with self.assertRaises(ViewModelError):
            build_view_model(FakeSession(), status="probably_fine")

    def test_unknown_fields_are_rejected_on_both_types(self):
        with self.assertRaises(ViewModelError):
            SessionViewModel.parse({"session_id": "s", "title": "t", "panels": [], "status": "complete",
                                    "verdict": "fine"})
        with self.assertRaises(ViewModelError):
            Panel.parse({"name": "X", "trust": "TRACE", "conclusion": "good"})

    def test_duplicate_panels_are_rejected(self):
        with self.assertRaises(ViewModelError):
            SessionViewModel.parse({"session_id": "s", "title": "t", "status": "complete", "panels": [
                {"name": "Trace", "trust": "TRACE", "items": []},
                {"name": "Trace", "trust": "TRACE", "items": []}]})

    def test_a_panel_item_with_an_invalid_trust_is_rejected(self):
        with self.assertRaises(ViewModelError):
            Panel.parse({"name": "X", "trust": "TRACE", "items": [
                {"label": "a", "trust": "DEFINITELY_TRUE"}]})

    def test_panel_item_omits_empty_fields(self):
        """Serialisation stays small and stable rather than full of nulls."""
        item = PanelItem(label="a").to_dict()
        self.assertEqual(item, {"label": "a"})
        self.assertEqual(PanelItem(label="a", value="v").to_dict(), {"label": "a", "value": "v"})


if __name__ == "__main__":
    unittest.main()