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