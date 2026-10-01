"""VLSI-1B: deterministic SDC analysis.

The analyzer's authority is narrow on purpose. It reports observations about the constraints that were
parsed, and the tests below hold it to that: every assertion is about what the analyzer SAW, and several
assert explicitly that it did NOT judge.

Two boundaries carry most of the weight here.

**Parse/analyze separation.** These tests always parse first and then analyse the parse result, so the
two steps are visible in every case. The analyzer has no entry point that accepts SDC text, which is why
there is no test that could pass while it re-parsed.

**Observation is not verdict.** "Zero findings" means nothing was found, not that the constraints are
correct. Tests assert both halves of that: a clean file produces no findings, AND the result carries
nothing that could be read as a correctness claim.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from debugagent.domains.vlsi import VlsiContractError
from debugagent.domains.vlsi.sdc_analyzer import (
    ANALYSIS_STATUSES,
    SdcAnalysisResult,
    analyze_sdc_constraints,
    analysis_order,
)
from debugagent.domains.vlsi.sdc_parser import parse_sdc

ANALYZER = Path(__file__).resolve().parents[1] / "src" / "debugagent" / "domains" / "vlsi" / "sdc_analyzer.py"
REF = "constraints/top.sdc"

#: A delay naming a clock nothing defines - the canonical incomplete-input case.
MISSING_CLOCK = "set_input_delay -clock ghost 0.5 [get_ports d]"

CLEAN = """create_clock -name clk_sys -period 10.000 [get_ports clk_sys]
set_input_delay -clock clk_sys -max 0.500 [get_ports data_in]
set_output_delay -clock clk_sys 0.400 [get_ports data_out]
"""


def _analyze(text: str, ref: str = REF) -> SdcAnalysisResult:
    """Parse, then analyse - the boundary the analyzer is required to keep."""
    return analyze_sdc_constraints(parse_sdc(text, ref=ref))


def _kinds(result: SdcAnalysisResult) -> list[str]:
    return [finding.kind for finding in result.findings]


class ClockReferences(unittest.TestCase):
    """Check 1: a delay may only name a clock some parsed create_clock provides."""

    def test_input_delay_referencing_an_existing_clock_is_clean(self):
        result = _analyze(CLEAN)
        self.assertEqual(result.findings, ())
        self.assertTrue(result.complete)

    def test_output_delay_referencing_an_existing_clock_is_clean(self):
        result = _analyze("create_clock -name c -period 10 clk\nset_output_delay -clock c 0.4 [get_ports q]")
        self.assertEqual(result.findings, ())

    def test_input_delay_referencing_a_missing_clock_is_reported(self):
        result = _analyze("set_input_delay -clock core_clk 0.500 [get_ports data_in]")
        self.assertEqual(_kinds(result), ["missing_constraint"])
        self.assertIn("core_clk", result.findings[0].message)

    def test_output_delay_referencing_a_missing_clock_is_reported(self):
        result = _analyze("set_output_delay -clock core_clk 0.400 [get_ports data_out]")
        self.assertEqual(_kinds(result), ["missing_constraint"])

    def test_the_message_states_what_was_parsed_not_what_exists(self):
        """The wording must not claim the clock does not exist - only that none was parsed.

        With unsupported commands present the distinction is the whole correctness of the finding: a
        clock may well exist and be created by a command this reader does not cover.
        """
        result = _analyze("set_input_delay -clock core_clk 0.5 [get_ports d]")
        message = result.findings[0].message
        self.assertIn("was parsed", message)
        for forbidden in ("does not exist", "no such clock", "invalid clock", "root cause", "should"):
            self.assertNotIn(forbidden, message.lower())

    def test_a_clock_defined_without_a_name_is_found_by_its_target(self):
        """`create_clock` with no -name names a clock after each target object."""
        result = _analyze("create_clock -period 10 clk\nset_input_delay -clock clk 0.5 [get_ports d]")
        self.assertEqual(result.findings, ())

    def test_a_clock_defined_without_a_name_covers_every_target(self):
        result = _analyze("create_clock -period 10 [get_ports {clk_a clk_b}]\n"
                          "set_input_delay -clock clk_b 0.5 [get_ports d]")
        self.assertEqual(result.findings, ())

    def test_named_clock_does_not_cover_an_unrelated_target_name(self):
        """`-name vclk` names the clock `vclk`; the port it sits on is not automatically that name."""
        result = _analyze("create_clock -name vclk -period 10 [get_ports clk]\n"
                          "set_input_delay -clock clk 0.5 [get_ports d]")
        self.assertEqual(_kinds(result), ["missing_constraint"])

    def test_details_carry_the_clock_and_the_constraint_kind(self):
        finding = _analyze("set_output_delay -clock ghost 0.4 [get_ports q]").findings[0]
        self.assertEqual(finding.details_dict["clock"], "ghost")
        self.assertEqual(finding.details_dict["constraint"], "set_output_delay")
        self.assertEqual(finding.details_dict["objects"], "q")


class ClockDefinitions(unittest.TestCase):
    """Checks 2 and 3: one clock name, defined more than once."""

    def test_a_single_clock_defines_nothing_to_report(self):
        result = _analyze("create_clock -name a -period 1 clk_a\ncreate_clock -name b -period 2 clk_b")
        self.assertEqual(result.findings, ())

    def test_multiple_distinct_clocks_are_clean(self):
        result = _analyze("create_clock -name a -period 1 clk_a\n"
                          "create_clock -name b -period 2 clk_b\n"
                          "set_input_delay -clock b 0.5 [get_ports d]")
        self.assertEqual(result.findings, ())

    def test_identical_redefinition_is_a_duplicate(self):
        result = _analyze("create_clock -name clk -period 10 [get_ports clk]\n"
                          "create_clock -name clk -period 10 [get_ports clk]")
        self.assertEqual(_kinds(result), ["duplicate_constraint"])
        self.assertEqual(result.findings[0].severity, "warning")

    def test_same_name_with_a_different_period_is_a_conflict(self):
        result = _analyze("create_clock -name clk -period 10 [get_ports clk]\n"
                          "create_clock -name clk -period 20 [get_ports clk]")
        self.assertEqual(_kinds(result), ["conflicting_constraint"])
        self.assertEqual(result.findings[0].severity, "critical")

    def test_same_name_with_different_targets_is_a_conflict(self):
        result = _analyze("create_clock -name clk -period 10 [get_ports clk]\n"
                          "create_clock -name clk -period 10 [get_ports clk2]")
        self.assertEqual(_kinds(result), ["conflicting_constraint"])

    def test_same_name_with_a_different_unit_is_a_conflict(self):
        """`10` and `10ns` are not the same statement, and the analyzer does not convert to find out."""
        result = _analyze("create_clock -name clk -period 10 [get_ports clk]\n"
                          "create_clock -name clk -period 10ns [get_ports clk]")
        self.assertEqual(_kinds(result), ["conflicting_constraint"])

    def test_same_name_with_a_different_waveform_is_a_conflict(self):
        """Waveform is part of what a clock IS, so two waveforms cannot both describe one clock."""
        result = _analyze("create_clock -name clk -period 10 -waveform {0.0 5.0} [get_ports clk]\n"
                          "create_clock -name clk -period 10 -waveform {0.0 6.0} [get_ports clk]")
        self.assertEqual(_kinds(result), ["conflicting_constraint"])

    def test_same_name_with_the_same_waveform_is_a_duplicate(self):
        result = _analyze("create_clock -name clk -period 10 -waveform {0.0 5.0} [get_ports clk]\n"
                          "create_clock -name clk -period 10 -waveform {0.0 5.0} [get_ports clk]")
        self.assertEqual(_kinds(result), ["duplicate_constraint"])

    def test_a_clock_with_an_empty_name_falls_back_to_its_targets(self):
        """A blank name is not a name; treating it as one would leave the clock unreferenced.

        Built directly rather than parsed, because the parser cannot produce a blank `-name` - the point
        is that the ANALYZER's naming rule is `if name:` and not `if name is not None:`, so a
        hand-constructed constraint cannot smuggle a nameless clock into the roster under `""`.
        """
        from debugagent.domains.vlsi.provenance import Provenance
        from debugagent.domains.vlsi.sdc import CreateClock, InputDelay
        from debugagent.domains.vlsi.sdc_analyzer import analyze_sdc_constraints
        from debugagent.domains.vlsi.sdc_parser import SdcParseResult

        blank = CreateClock(name="", period="10", objects=("clk",),
                            provenance=Provenance("file", REF, 1))
        delay = InputDelay(clock="clk", value="0.5", objects=("d",),
                           provenance=Provenance("file", REF, 2))
        result = analyze_sdc_constraints(SdcParseResult(constraints=(blank, delay)))
        self.assertEqual(result.findings, (),
                         "a clock with a blank name did not provide its target name")

    def test_an_identical_duplicate_is_located_at_the_first_repeat(self):
        """The duplicate is not visible at the first definition, so that is not where it belongs."""
        finding = _analyze("create_clock -name c -period 1 p\ncreate_clock -name c -period 1 p"
                           ).findings[0]
        self.assertEqual(finding.kind, "duplicate_constraint")
        self.assertEqual(finding.provenance.line, 2,
                         "a duplicate was located at its first definition, which is not the repeat")
        self.assertEqual(finding.details_dict["lines"], "1,2")

    def test_duplicate_detection_does_not_depend_on_constraint_input_order(self):
        """Identical definitions differ only by line, so line order is the only ordering available."""
        from debugagent.domains.vlsi.sdc_analyzer import analyze_sdc_constraints
        from debugagent.domains.vlsi.sdc_parser import SdcParseResult

        parsed = parse_sdc("create_clock -name c -period 1 p\ncreate_clock -name c -period 1 p\n"
                           "create_clock -name c -period 1 p", ref=REF)
        forwards = analyze_sdc_constraints(parsed).to_dict()
        shuffled = SdcParseResult(constraints=tuple(reversed(parsed.constraints)))
        self.assertEqual(analyze_sdc_constraints(shuffled).to_dict(), forwards,
                         "reordering the constraints moved the duplicate's provenance")

    def test_missing_clock_severity_is_critical(self):
        """A delay whose clock cannot be interpreted is worth attention now."""
        finding = _analyze(MISSING_CLOCK).findings[0]
        self.assertEqual(finding.kind, "missing_constraint")
        self.assertEqual(finding.severity, "critical")

    def test_a_duplicate_created_without_a_name_is_detected_by_target(self):
        result = _analyze("create_clock -period 10 [get_ports clk]\n"
                          "create_clock -period 10 [get_ports clk]")
        self.assertEqual(_kinds(result), ["duplicate_constraint"])

    def test_three_definitions_produce_one_finding_not_three(self):
        result = _analyze("create_clock -name c -period 1 [get_ports p]\n"
                          "create_clock -name c -period 1 [get_ports p]\n"
                          "create_clock -name c -period 1 [get_ports p]")
        self.assertEqual(_kinds(result), ["duplicate_constraint"])
        self.assertEqual(result.findings[0].details_dict["occurrences"], 3)

    def test_one_duplicate_and_one_conflict_are_both_reported(self):
        result = _analyze("create_clock -name a -period 1 [get_ports pa]\n"
                          "create_clock -name a -period 1 [get_ports pa]\n"
                          "create_clock -name b -period 1 [get_ports pb]\n"
                          "create_clock -name b -period 2 [get_ports pb]")
        self.assertEqual(sorted(_kinds(result)), ["conflicting_constraint", "duplicate_constraint"])

    def test_a_clock_defined_twice_does_not_also_report_a_missing_clock(self):
        result = _analyze("create_clock -name c -period 1 [get_ports p]\n"
                          "create_clock -name c -period 2 [get_ports p]\n"
                          "set_input_delay -clock c 0.5 [get_ports d]")
        self.assertEqual(_kinds(result), ["conflicting_constraint"])

    def test_detail_lines_list_every_occurrence(self):
        finding = _analyze("create_clock -name c -period 1 p\ncreate_clock -name c -period 1 p\n"
                           "create_clock -name c -period 1 p").findings[0]
        self.assertEqual(finding.details_dict["lines"], "1,2,3")


class CleanSemantics(unittest.TestCase):
    """Zero findings is not a verdict, and an incomplete read is not a clean one."""

    def test_a_valid_file_produces_zero_findings(self):
        result = _analyze(CLEAN)
        self.assertEqual(result.findings, ())
        self.assertEqual(result.status, "complete")
        self.assertTrue(result.clean)

    def test_zero_findings_carries_no_correctness_claim(self):
        """Nothing in a clean result may be read as the design being correct."""
        payload = _analyze(CLEAN).to_dict()
        self.assertEqual(payload["findings"], [])
        self.assertEqual(payload["status"], "complete")
        blob = str(payload).lower()
        for verdict in ("correct", "valid", "safe", "signoff", "clean_design", "timing_met",
                        "no_problems", "pass"):
            self.assertNotIn(verdict, blob, f"the result implies a verdict: {verdict!r}")

    def test_an_empty_file_is_complete_and_clean(self):
        result = _analyze("")
        self.assertTrue(result.complete)
        self.assertTrue(result.clean)

    def test_unsupported_commands_make_the_result_incomplete(self):
        """A clock may exist in a command this reader cannot see, so findings become provisional."""
        result = _analyze("set_false_path -from a -to b\nset_input_delay -clock c 0.5 [get_ports d]")
        self.assertEqual(result.status, "incomplete")
        self.assertFalse(result.complete)
        self.assertFalse(result.clean, "an incomplete read was reported as clean")
        self.assertEqual(len(result.parser_issues), 1)

    def test_malformed_commands_make_the_result_incomplete(self):
        result = _analyze("create_clock -name broken\ncreate_clock -name c -period 1 p")
        self.assertEqual(result.status, "incomplete")

    def test_parser_issues_are_carried_not_dropped(self):
        result = _analyze("set_false_path -from a\nset_clock_uncertainty -setup 0.1 c")
        self.assertEqual(len(result.parser_issues), 2)
        self.assertEqual([issue.command for issue in result.parser_issues],
                         ["set_false_path", "set_clock_uncertainty"])
        self.assertEqual(len(result.to_dict()["parser_issues"]), 2)

    def test_findings_survive_alongside_incompleteness(self):
        """An incomplete result still reports what it did see - it is not suppressed wholesale."""
        result = _analyze("set_false_path -from a\nset_input_delay -clock ghost 0.5 [get_ports d]")
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(_kinds(result), ["missing_constraint"])


class ProvenanceBehaviour(unittest.TestCase):
    """Every finding points at a real source constraint."""

    def test_a_finding_points_at_the_constraint_it_is_about(self):
        result = _analyze("create_clock -name c -period 1 p\nset_input_delay -clock ghost 0.5 [get_ports d]")
        finding = result.findings[0]
        self.assertEqual(finding.provenance.ref, REF)
        self.assertEqual(finding.provenance.line, 2)

    def test_a_multi_source_finding_uses_a_real_line_not_a_synthesised_one(self):
        finding = _analyze("create_clock -name c -period 1 p\ncreate_clock -name c -period 2 p").findings[0]
        self.assertEqual(finding.provenance.line, 2, "the duplicate was not located at a real line")
        self.assertEqual(finding.details_dict["lines"], "1,2")

    def test_no_fabricated_line_numbers(self):
        result = _analyze("set_false_path -from a\ncreate_clock -name c -period 1 p\n"
                          "create_clock -name c -period 2 p\nset_input_delay -clock ghost 0.5 [get_ports d]")
        self.assertTrue(result.findings)
        for finding in result.findings:
            self.assertIsNotNone(finding.provenance.line)
            self.assertGreaterEqual(finding.provenance.line, 1)

    def test_findings_of_kind_can_be_selected(self):
        result = _analyze("create_clock -name c -period 1 p\ncreate_clock -name c -period 2 p\n"
                          "set_input_delay -clock ghost 0.5 [get_ports d]")
        self.assertEqual(len(result.findings_of_kind("conflicting_constraint")), 1)
        self.assertEqual(len(result.findings_of_kind("missing_constraint")), 1)
        self.assertEqual(result.findings_of_kind("parse_error"), ())


class Ordering(unittest.TestCase):
    """Source order, deterministic and total."""

    SOURCE = ("set_input_delay -clock ghost_b 0.5 [get_ports d1]\n"
              "create_clock -name c -period 1 p\n"
              "set_output_delay -clock ghost_a 0.4 [get_ports q1]\n"
              "create_clock -name c -period 2 p\n")

    def test_findings_come_back_in_source_order(self):
        result = _analyze(self.SOURCE)
        lines = [finding.provenance.line for finding in result.findings]
        self.assertEqual(lines, sorted(lines), "findings are not in source order")
        self.assertEqual(lines, [1, 3, 4])

    def test_repeated_analysis_is_identical(self):
        results = [_analyze(self.SOURCE).to_dict() for _ in range(25)]
        for result in results[1:]:
            self.assertEqual(result, results[0])

    def test_ordering_does_not_depend_on_constraint_input_order(self):
        """Feeding the same constraints in a different order must not reorder the findings."""
        parsed = parse_sdc(self.SOURCE, ref=REF)
        forwards = analyze_sdc_constraints(parsed).to_dict()
        shuffled = type(parsed)(constraints=tuple(reversed(parsed.constraints)),
                                issues=tuple(reversed(parsed.issues)))
        self.assertEqual(analyze_sdc_constraints(shuffled).to_dict(), forwards)

    def test_the_order_key_is_total(self):
        result = _analyze(self.SOURCE)
        keys = [analysis_order(finding) for finding in result.findings]
        self.assertEqual(len(set(keys)), len(keys), "two findings tie, so the order is arbitrary")

    def test_analysis_order_differs_from_report_order_by_design(self):
        """Source order and severity order are both intentional, and they are not the same thing."""
        from debugagent.domains.vlsi.findings import sort_findings

        result = _analyze(self.SOURCE)
        by_source = [f.kind for f in result.findings]
        by_severity = [f.kind for f in sort_findings(result.findings)]
        self.assertEqual(by_source, ["missing_constraint", "missing_constraint", "conflicting_constraint"])
        self.assertEqual(by_severity[0], "conflicting_constraint",
                         "severity ordering should lead with the critical finding")


class ResultContract(unittest.TestCase):
    """The result is minimal, explicit, and cannot claim more than it knows."""

    def test_status_vocabulary_is_closed(self):
        self.assertEqual(ANALYSIS_STATUSES, ("complete", "incomplete"))

    def test_result_round_trips_through_serialisation(self):
        for text in (CLEAN, MISSING_CLOCK, "set_false_path -from a"):
            with self.subTest(text=text[:30]):
                result = _analyze(text)
                self.assertEqual(SdcAnalysisResult.parse(result.to_dict()), result)

    def test_a_serialised_result_cannot_claim_complete_with_issues(self):
        payload = _analyze("set_false_path -from a").to_dict()
        payload["status"] = "complete"
        with self.assertRaises(VlsiContractError) as caught:
            SdcAnalysisResult.parse(payload)
        self.assertIn("complete", str(caught.exception))

    def test_a_serialised_result_cannot_claim_incomplete_without_issues(self):
        payload = _analyze(CLEAN).to_dict()
        payload["status"] = "incomplete"
        with self.assertRaises(VlsiContractError):
            SdcAnalysisResult.parse(payload)

    def test_an_invalid_status_is_rejected(self):
        payload = _analyze(CLEAN).to_dict()
        payload["status"] = "probably_fine"
        with self.assertRaises(VlsiContractError):
            SdcAnalysisResult.parse(payload)

    def test_unknown_result_fields_are_rejected(self):
        payload = _analyze(CLEAN).to_dict()
        payload["verdict"] = "looks good"
        with self.assertRaises(VlsiContractError):
            SdcAnalysisResult.parse(payload)

    def test_findings_use_only_the_existing_foundation_vocabulary(self):
        from debugagent.domains.vlsi.findings import FINDING_KINDS

        result = _analyze(MISSING_CLOCK + "\ncreate_clock -name c -period 1 p\ncreate_clock -name c -period 2 p")
        self.assertTrue(result.findings)
        for finding in result.findings:
            self.assertIn(finding.kind, FINDING_KINDS,
                          "the analyzer invented a finding kind outside the foundation vocabulary")



class AnalyzerBoundary(unittest.TestCase):
    """What the analyzer must not be able to do."""

    def _imports(self) -> set[str]:
        tree = ast.parse(ANALYZER.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        return imported

    def test_no_memory_no_dispatch_no_evidence_access(self):
        for name in self._imports():
            with self.subTest(imported=name):
                self.assertFalse(name.startswith("debugagent.memory"))
                self.assertFalse(name.startswith("debugagent.pipeline"))
                self.assertFalse(name.startswith("debugagent.agents"))
                self.assertFalse(name.startswith("debugagent.llm"))

    def test_no_network_no_clock_no_randomness(self):
        for name in self._imports():
            with self.subTest(imported=name):
                for forbidden in ("random", "time", "uuid", "socket", "urllib", "subprocess", "os"):
                    self.assertFalse(name == forbidden or name.startswith(forbidden + "."),
                                     f"the analyzer imports {name!r}")

    def test_the_analyzer_has_no_entry_point_that_accepts_raw_sdc(self):
        """It cannot re-parse, because nothing in it takes source text."""
        tree = ast.parse(ANALYZER.read_text(encoding="utf-8"))
        public = [node.name for node in tree.body
                  if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")]
        self.assertIn("analyze_sdc_constraints", public)
        signature = ANALYZER.read_text(encoding="utf-8")
        self.assertIn("def analyze_sdc_constraints(parsed: SdcParseResult)", signature)
        self.assertNotIn("parse_sdc(", signature,
                         "the analyzer calls the parser instead of consuming its result")
        self.assertNotIn("def analyze_sdc(", signature)

    def test_no_repair_no_recommendation_no_patch(self):
        source = ANALYZER.read_text(encoding="utf-8").lower()
        for forbidden in ("def repair", "def propose", "def recommend", "def patch", "def fix",
                          "def apply", "hypothesis", "root_cause", "recommendation"):
            self.assertNotIn(forbidden, source, f"the analyzer contains {forbidden!r}")

    def test_no_finding_message_makes_a_judgement(self):
        forbidden = ("root cause", "should ", "recommend", "fix ", "change this", "will solve",
                     "is safe", "signoff", "correct", "invalid design")
        for text in (CLEAN,
                     "set_input_delay -clock ghost 0.5 [get_ports d]",
                     "create_clock -name c -period 1 p\ncreate_clock -name c -period 2 p",
                     "set_false_path -from a\nset_output_delay -clock x 0.4 [get_ports q]"):
            with self.subTest(text=text[:40]):
                for finding in _analyze(text).findings:
                    lowered = finding.message.lower()
                    for phrase in forbidden:
                        self.assertNotIn(phrase, lowered,
                                         f"finding makes a judgement: {finding.message!r}")


if __name__ == "__main__":
    unittest.main()