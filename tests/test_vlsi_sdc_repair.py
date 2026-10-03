"""VLSI-1D.3b: semantic SDC repair, and deterministic rendering into proposed content.

Every test runs against the REAL parser and analyzer over real SDC text. A renderer fed hand-built
findings would only prove that string formatting works, whereas the thing under test is precisely
whether the *evidence* supports the repair - so the evidence has to be real.

The suite is organised around the increment's safety rule:

    **never invent an engineering value**

which shows up as refusal tests far more often than as rendering tests. That ratio is the finding, not
an accident of test coverage: of the three finding kinds the analyzer currently emits, exactly one is
renderable from deterministic evidence alone.

    duplicate_constraint    renderable - the repeats are identical, so one is removed whole
    conflicting_constraint  REFUSED   - evidence lists the periods but establishes no basis to choose
    missing_constraint      conditional - only the clock NAME is established; period and target are not

`TestNoInventedValues` is the negative-control group: each test there removes one required fact and
asserts that nothing is produced rather than something plausible.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

from debugagent.agents.patch_generator import PROPOSAL_HEADER, build_patch_task
from debugagent.domains.vlsi.provenance import VlsiContractError
from debugagent.domains.vlsi.repair import (
    REPAIR_OPERATIONS,
    REPAIR_UNAVAILABLE_REASONS,
    RenderedSdcRepair,
    RepairUnavailable,
    SdcRepair,
    render_sdc_repair,
)
from debugagent.domains.vlsi.sdc_analyzer import analyze_sdc_constraints
from debugagent.domains.vlsi.sdc_parser import parse_sdc

REF = "constraints/top.sdc"
CLOCK = "create_clock -name core_clk -period 10 [get_ports clk]\n"
CONFLICT = "create_clock -name core_clk -period 12 [get_ports clk]\n"
DELAY = "set_input_delay -clock pll_clk 0.4 [get_ports d]\n"

#: A file with a clock defined twice identically, a comment, and a blank line - so any rewriting of
#: unrelated content shows up immediately.
DUPLICATE_SOURCE = (CLOCK + CLOCK + "# keep this comment\n" + "\n" + DELAY)
#: A file whose only fault is an undefined clock reference.
MISSING_SOURCE = CLOCK + DELAY
#: A file where one clock name carries two different periods.
CONFLICT_SOURCE = CLOCK + CONFLICT


#: Modules the renderer imports RELATIVELY. The AST reports a relative import without the
#: package prefix, so they are listed here rather than pattern-matched.
RELATIVE_DOMAIN_MODULES = {"findings", "provenance", "sdc", "sdc_analyzer", "sdc_parser",
                           "identity", "comparison", "repair"}


def analyse(text: str, ref: str = REF):
    return analyze_sdc_constraints(parse_sdc(text, ref=ref))


def render(repair: SdcRepair, source: str, ref: str = REF):
    return render_sdc_repair(repair, analyse(source, ref), source)


class SemanticRepairContract(unittest.TestCase):
    """1, 2: the record is structured, closed, and round-trips."""

    def test_a_repair_round_trips(self):
        repair = SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                           period="10", unit="ns", objects=("pll", "pll_b"))
        self.assertEqual(SdcRepair.parse(repair.to_dict()), repair)

    def test_a_minimal_repair_round_trips(self):
        repair = SdcRepair(operation="remove_duplicate_clock", clock="core_clk", target=REF)
        self.assertEqual(SdcRepair.parse(repair.to_dict()), repair)

    def test_an_unknown_field_is_rejected(self):
        """The mechanism that stops a repair carrying instructions the renderer would not see."""
        for extra in ({"tcl": "create_clock -period 2"}, {"recommendation": "add a clock"},
                      {"text": "whole file"}, {"diff": "-+++/n"}, {"proposed_content": "x"},
                      {"root_cause": "y"}, {"hypothesis": "z"}):
            with self.subTest(extra=sorted(extra)):
                payload = SdcRepair(operation="define_clock", clock="pll_clk", target=REF).to_dict()
                payload.update(extra)
                with self.assertRaises(VlsiContractError) as caught:
                    SdcRepair.parse(payload)
                self.assertIn("unknown field", str(caught.exception))

    def test_an_unknown_operation_is_rejected(self):
        with self.assertRaises(VlsiContractError):
            SdcRepair.parse({"operation": "rewrite_the_file", "clock": "pll_clk"})

    def test_a_missing_clock_is_rejected(self):
        for payload in ({"operation": "define_clock"}, {"operation": "define_clock", "clock": ""},
                        {"operation": "define_clock", "clock": "   "}):
            with self.subTest(payload=payload):
                with self.assertRaises(VlsiContractError):
                    SdcRepair.parse(payload)

    def test_a_blank_object_is_rejected_rather_than_dropped(self):
        with self.assertRaises(VlsiContractError):
            SdcRepair.parse({"operation": "define_clock", "clock": "c", "objects": ["a", ""]})

    def test_a_non_string_period_is_rejected(self):
        with self.assertRaises(VlsiContractError):
            SdcRepair.parse({"operation": "define_clock", "clock": "c", "period": 10})

    def test_every_declared_operation_parses(self):
        for operation in REPAIR_OPERATIONS:
            with self.subTest(operation=operation):
                self.assertEqual(SdcRepair.parse({"operation": operation, "clock": "c"}).operation,
                                 operation)

    def test_the_repair_is_frozen(self):
        repair = SdcRepair(operation="define_clock", clock="c")
        with self.assertRaises(AttributeError):
            repair.clock = "other"

    def test_the_repair_carries_no_field_that_could_hold_sdc_text(self):
        """Structural, not a naming convention: nothing in the record can carry Tcl or a file body."""
        for field in SdcRepair.FIELDS:
            with self.subTest(field=field):
                self.assertNotIn(field, ("text", "content", "tcl", "diff", "patch", "proposed",
                                         "proposed_content", "line", "body"))


class RenderingIsDeterministic(unittest.TestCase):
    """3, 22: identical inputs, identical bytes."""

    def test_rendering_twice_gives_identical_content(self):
        repair = SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                           period="10", objects=("pll",))
        first, second = render(repair, MISSING_SOURCE), render(repair, MISSING_SOURCE)
        self.assertEqual(first.proposed, second.proposed)
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_rendering_a_duplicate_twice_gives_identical_content(self):
        repair = SdcRepair(operation="remove_duplicate_clock", clock="core_clk", target=REF)
        self.assertEqual(render(repair, DUPLICATE_SOURCE).proposed,
                         render(repair, DUPLICATE_SOURCE).proposed)

    def test_two_renders_of_different_findings_are_both_stable(self):
        duplicate = SdcRepair(operation="remove_duplicate_clock", clock="core_clk", target=REF)
        define = SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                           period="10", objects=("pll",))
        for _ in range(3):
            self.assertEqual(render(duplicate, DUPLICATE_SOURCE).proposed,
                             render(duplicate, DUPLICATE_SOURCE).proposed)
            self.assertEqual(render(define, MISSING_SOURCE).proposed,
                             render(define, MISSING_SOURCE).proposed)

    def test_insertion_ordering_is_deterministic(self):
        """The clock goes before the FIRST reference, and that holds however many references exist."""
        source = CLOCK + DELAY + DELAY + DELAY
        repair = SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                           period="10", objects=("pll",))
        proposed = render(repair, source).proposed
        lines = proposed.splitlines()
        clock_line = next(i for i, line in enumerate(lines) if "pll_clk -period" in line)
        first_use = next(i for i, line in enumerate(lines) if "-clock pll_clk 0.4" in line)
        self.assertLess(clock_line, first_use, "the clock must be defined before it is used")
        self.assertEqual(proposed, render(repair, source).proposed)


class ValidRepairsRender(unittest.TestCase):
    """7, 8, 9: the renderable cases, and preservation of everything else."""

    def test_a_fully_specified_repair_renders_expected_content(self):
        repair = SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                           period="10", objects=("pll",))
        result = render(repair, MISSING_SOURCE)
        self.assertIsInstance(result, RenderedSdcRepair)
        self.assertEqual(result.proposed, CLOCK + "create_clock -name pll_clk -period 10 "
                                                "[get_ports pll]\n" + DELAY)
        self.assertEqual(result.inserted, ("create_clock -name pll_clk -period 10 [get_ports pll]",))
        self.assertEqual(result.removed, ())

    def test_the_rendered_repair_actually_resolves_the_finding(self):
        """Rendered content re-analysed must no longer report the condition it addressed."""
        from debugagent.domains.vlsi.comparison import compare_sdc_findings

        repair = SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                           period="10", objects=("pll",))
        result = render(repair, MISSING_SOURCE)
        outcome = compare_sdc_findings(analyse(MISSING_SOURCE), analyse(result.proposed))
        self.assertEqual(outcome.status, "verified")
        self.assertTrue(outcome.verified)

    def test_a_duplicate_is_removed_and_the_rest_survives(self):
        repair = SdcRepair(operation="remove_duplicate_clock", clock="core_clk", target=REF)
        result = render(repair, DUPLICATE_SOURCE)
        self.assertEqual(result.proposed.count("create_clock -name core_clk"), 1)
        self.assertEqual(result.removed, (CLOCK.strip(),))
        self.assertEqual(result.inserted, ())

    def test_removing_a_duplicate_resolves_the_finding(self):
        from debugagent.domains.vlsi.comparison import compare_sdc_findings

        repair = SdcRepair(operation="remove_duplicate_clock", clock="core_clk", target=REF)
        result = render(repair, DUPLICATE_SOURCE)
        outcome = compare_sdc_findings(analyse(DUPLICATE_SOURCE), analyse(result.proposed))
        self.assertEqual(outcome.identities("resolved"), ("duplicate_constraint:clock=core_clk",))
        # Not verified: this fixture also references an undefined pll_clk, which the duplicate
        # removal neither fixes nor is responsible for. Asserting the aggregate here would be asserting
        # something about the fixture rather than about the repair.
        self.assertEqual([i for i in outcome.identities("still_present")
                         if i.startswith("missing_constraint:clock=pll_clk")],
                         ["missing_constraint:clock=pll_clk,constraint_kind=set_input_delay,objects=d"])

    def test_unrelated_lines_are_unchanged(self):
        repair = SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                           period="10", objects=("pll",))
        proposed = render(repair, MISSING_SOURCE).proposed.splitlines(keepends=True)
        for line in proposed:
            if "pll_clk -period" not in line:
                self.assertIn(line, MISSING_SOURCE.splitlines(keepends=True),
                              "a line unrelated to the repair was altered")

    def test_comments_and_blank_lines_survive_a_removal(self):
        repair = SdcRepair(operation="remove_duplicate_clock", clock="core_clk", target=REF)
        proposed = render(repair, DUPLICATE_SOURCE).proposed
        self.assertIn("# keep this comment\n", proposed)
        self.assertIn("\n\n", proposed)
        self.assertEqual(proposed.count("# keep this comment"), 1)

    def test_every_other_line_survives_a_removal_byte_for_byte(self):
        repair = SdcRepair(operation="remove_duplicate_clock", clock="core_clk", target=REF)
        before = DUPLICATE_SOURCE.splitlines(keepends=True)
        after = render(repair, DUPLICATE_SOURCE).proposed.splitlines(keepends=True)
        self.assertEqual(len(after), len(before) - 1)
        removed_index = next(i for i, (a, b) in enumerate(zip(before, after)) if a != b)
        self.assertEqual(before[removed_index], CLOCK)
        self.assertEqual([line for i, line in enumerate(before) if i != removed_index], after)

    def test_a_rendered_result_reanalyses_as_complete(self):
        repair = SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                           period="10", objects=("pll",))
        result = render(repair, MISSING_SOURCE)
        self.assertTrue(analyse(result.proposed).complete)


class NoInventedValues(unittest.TestCase):
    """4, 5, 6, 21: the core safety rule."""

    def test_a_missing_clock_repair_without_a_period_is_unavailable(self):
        repair = SdcRepair(operation="define_clock", clock="pll_clk", target=REF)
        result = render(repair, MISSING_SOURCE)
        self.assertIsInstance(result, RepairUnavailable)
        self.assertEqual(result.reason, "missing_required_value")
        self.assertFalse(hasattr(result, "proposed"), "a refusal must carry nothing usable")

    def test_a_missing_clock_repair_without_objects_is_unavailable(self):
        repair = SdcRepair(operation="define_clock", clock="pll_clk", target=REF, period="10")
        self.assertEqual(render(repair, MISSING_SOURCE).reason, "missing_required_value")

    def test_the_renderer_never_invents_a_period(self):
        """Every way of omitting the period yields a refusal, never a default."""
        source = CLOCK + DELAY
        for repair in (SdcRepair(operation="define_clock", clock="pll_clk", target=REF, objects=("pll",)),
                       SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                                 period="", objects=("pll",)),
                       SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                                 period="   ", objects=("pll",))):
            with self.subTest(repair=repair.to_dict()):
                result = render(repair, source)
                self.assertIsInstance(result, RepairUnavailable)
                self.assertEqual(result.reason, "missing_required_value")
                self.assertNotIn("period 2", str(result.to_dict()))
                self.assertNotIn("period 10", str(result.to_dict()))

    def test_the_renderer_never_invents_a_target_port(self):
        source = CLOCK + DELAY
        for repair in (SdcRepair(operation="define_clock", clock="pll_clk", target=REF, period="10"),
                       SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                                 period="10", objects=())):
            with self.subTest(repair=repair.to_dict()):
                result = render(repair, source)
                self.assertIsInstance(result, RepairUnavailable)
                self.assertEqual(result.reason, "missing_required_value")

    def test_a_non_numeric_period_is_refused_rather_than_emitted(self):
        """`create_clock -period` accepts any token, so the value is checked against a delay probe."""
        source = CLOCK + DELAY
        for period in ("abc", "ten", "--10", "10 10"):
            with self.subTest(period=period):
                repair = SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                                   period=period, objects=("pll",))
                result = render(repair, source)
                self.assertIsInstance(result, RepairUnavailable)
                self.assertEqual(result.reason, "unrenderable_value")

    def test_a_valid_period_with_a_unit_is_accepted(self):
        for period, unit, expected in (("10", "ns", "-period 10ns"),
                                       ("500", "ps", "-period 500ps"),
                                       ("1.2e-3", "ns", "-period 1.2e-3ns")):
            with self.subTest(period=period, unit=unit):
                repair = SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                                   period=period, unit=unit, objects=("pll",))
                result = render(repair, MISSING_SOURCE)
                self.assertIsInstance(result, RenderedSdcRepair)
                self.assertIn(expected, result.inserted[0])

    def test_an_object_that_cannot_round_trip_is_refused(self):
        for objects in (("a b",), ("d]e",), ("a{b",)):
            with self.subTest(objects=objects):
                repair = SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                                   period="10", objects=objects)
                result = render(repair, MISSING_SOURCE)
                self.assertIsInstance(result, RepairUnavailable)
                self.assertEqual(result.reason, "unrenderable_value")

    def test_no_default_constant_appears_in_the_renderer(self):
        """A belt-and-braces check that no magic timing value has been written into the module."""
        import ast as _ast

        tree = _ast.parse(Path(sys.modules[render_sdc_repair.__module__].__file__).read_text(encoding="utf-8"))
        literals = [node.value for node in _ast.walk(tree)
                    if isinstance(node, _ast.Constant) and isinstance(node.value, str)]
        for forbidden in ("2.0", "10.0", "1.0", "DEFAULT_PERIOD", "DEFAULT_CLOCK", "FALLBACK"):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, literals,
                                 "no timing constant may be baked into the renderer")
        self.assertFalse([name for name in dir() if False])


class OnlySupportedOperations(unittest.TestCase):
    """19, 20: distinct conditions stay distinct, and unsupported work is refused."""

    def test_an_unsupported_operation_is_refused_by_the_renderer(self):
        repair = SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                           period="10", objects=("pll",))
        object.__setattr__(repair, "operation", "rewrite_the_whole_file")
        result = render_sdc_repair(repair, analyse(MISSING_SOURCE), MISSING_SOURCE)
        self.assertIsInstance(result, RepairUnavailable)
        self.assertEqual(result.reason, "unsupported_operation")

    def test_removing_a_duplicate_is_refused_for_a_conflicting_clock(self):
        """The two conditions must never be treated as one.

        A duplicate's repeats are identical, so removing one restates a fact. A conflict's differ, so
        removing one CHOOSES a period - and evidence listing `10,12` establishes no basis for the choice.
        """
        repair = SdcRepair(operation="remove_duplicate_clock", clock="core_clk", target=REF)
        result = render(repair, CONFLICT_SOURCE)
        self.assertIsInstance(result, RepairUnavailable)
        self.assertEqual(result.reason, "conflicting_finding")
        self.assertIn("10,12", result.detail)

    def test_duplicate_and_conflicting_are_still_distinct_identities(self):
        duplicate = analyse(DUPLICATE_SOURCE).findings[0]
        conflicting = analyse(CONFLICT_SOURCE).findings[0]
        self.assertEqual(duplicate.kind, "duplicate_constraint")
        self.assertEqual(conflicting.kind, "conflicting_constraint")
        self.assertNotEqual(duplicate.identity, conflicting.identity)

    def test_a_repair_with_no_supporting_finding_is_refused(self):
        """A repair invented for a condition the analysis never reported cannot be rendered."""
        repair = SdcRepair(operation="define_clock", clock="no_such_clk", target=REF,
                           period="10", objects=("pll",))
        result = render(repair, MISSING_SOURCE)
        self.assertIsInstance(result, RepairUnavailable)
        self.assertEqual(result.reason, "no_supporting_finding")

    def test_a_repair_for_a_healthy_clock_is_refused(self):
        repair = SdcRepair(operation="remove_duplicate_clock", clock="core_clk", target=REF)
        result = render(repair, CLOCK)
        self.assertIsInstance(result, RepairUnavailable)
        self.assertEqual(result.reason, "no_supporting_finding")

    def test_a_repair_naming_a_different_file_is_refused(self):
        repair = SdcRepair(operation="define_clock", clock="pll_clk", target="constraints/other.sdc",
                           period="10", objects=("pll",))
        result = render(repair, MISSING_SOURCE)
        self.assertIsInstance(result, RepairUnavailable)
        self.assertEqual(result.reason, "target_mismatch")

    def test_incomplete_evidence_blocks_every_repair(self):
        """An analysis that did not understand the file cannot support a claim about a specific line."""
        repair = SdcRepair(operation="remove_duplicate_clock", clock="core_clk", target=REF)
        result = render(repair, "set_false_path -from [get_clocks a] -to [get_clocks b]\n")
        self.assertIsInstance(result, RepairUnavailable)
        self.assertEqual(result.reason, "incomplete_evidence")

    def test_a_repair_with_no_clock_is_refused(self):
        repair = SdcRepair(operation="define_clock", clock="", target=REF, period="10",
                           objects=("pll",))
        result = render(repair, MISSING_SOURCE)
        self.assertEqual(result.reason, "missing_required_value")

    def test_every_declared_reason_is_reachable(self):
        reached = set()

        def record(result):
            self.assertIsInstance(result, RepairUnavailable)
            reached.add(result.reason)

        record(render(SdcRepair(operation="define_clock", clock="pll_clk", target=REF), MISSING_SOURCE))
        record(render(SdcRepair(operation="define_clock", clock="pll_clk", target=REF, period="zz",
                                objects=("p",)), MISSING_SOURCE))
        record(render(SdcRepair(operation="remove_duplicate_clock", clock="core_clk", target=REF),
                      CONFLICT_SOURCE))
        record(render(SdcRepair(operation="define_clock", clock="nope", target=REF, period="10",
                                objects=("p",)), MISSING_SOURCE))
        record(render(SdcRepair(operation="define_clock", clock="pll_clk", target="x/y.sdc",
                                period="10", objects=("p",)), MISSING_SOURCE))
        record(render(SdcRepair(operation="remove_duplicate_clock", clock="core_clk", target=REF),
                      "set_multicycle_path -setup 2 -from [get_clocks c]\n"))

        drifted = SdcRepair(operation="define_clock", clock="pll_clk", target=REF, period="10",
                            objects=("p",))
        object.__setattr__(drifted, "operation", "delete_everything")
        record(render_sdc_repair(drifted, analyse(MISSING_SOURCE), MISSING_SOURCE))
        record(render_sdc_repair(SdcRepair(operation="define_clock", clock="", target=REF),
                                 analyse(MISSING_SOURCE), MISSING_SOURCE))

        # unknown_location and definition_not_single_line / source_line_mismatch need hand-built
        # evidence, because the analyzer cannot produce them for a file it understood.
        from dataclasses import replace as dc_replace

        baseline = analyse(MISSING_SOURCE)
        orphan = dc_replace(baseline.findings[0], provenance=dc_replace(
            baseline.findings[0].provenance, line=None))
        record(render_sdc_repair(SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                                           period="10", objects=("p",)),
                                 dc_replace(baseline, findings=(orphan,)), MISSING_SOURCE))

        multiline = "create_clock -name core_clk -period 10 \\\n  [get_ports clk]\n"
        wrapped = "create_clock -name core_clk -period 10 \\\n  [get_ports clk]\n" \
                  "create_clock -name core_clk -period 10 \\\n  [get_ports clk]\n"
        record(render(SdcRepair(operation="remove_duplicate_clock", clock="core_clk", target=REF),
                      wrapped))

        # `source_line_mismatch`: a recorded definition line that is a valid command for a DIFFERENT
        # clock. The analyzer cannot produce this - it derives `lines` from the definitions themselves -
        # so it is built by hand, which is the only honest way to reach the guard.
        crossed = analyse(CLOCK + CLOCK + "create_clock -name other_clk -period 10 "
                              "[get_ports clk]\n")
        crossed_finding = dc_replace(
            crossed.findings[0],
            details=(("clock", "clock", "core_clk"), ("lines", "lines", "1,3"),
                     ("occurrences", "occurrences", 3)))
        record(render_sdc_repair(
            SdcRepair(operation="remove_duplicate_clock", clock="core_clk", target=REF),
            dc_replace(crossed, findings=(crossed_finding,)),
            CLOCK + CLOCK + "create_clock -name other_clk -period 10 [get_ports clk]\n"))

        self.assertEqual(reached, set(REPAIR_UNAVAILABLE_REASONS),
                         f"declared but unreachable: {sorted(set(REPAIR_UNAVAILABLE_REASONS) - reached)}")


class SourceTextIsPreserved(unittest.TestCase):
    """11, 12: the renderer is an editor of exactly one thing."""

    def test_the_input_string_is_not_modified(self):
        source = MISSING_SOURCE
        before = str(source)
        render(SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                         period="10", objects=("pll",)), source)
        self.assertEqual(source, before)

    def test_line_endings_are_preserved(self):
        crlf = (CLOCK + DELAY).replace("\n", "\r\n")
        repair = SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                           period="10", objects=("pll",))
        result = render(repair, crlf)
        self.assertIn("\r\n", result.proposed)
        # Every LF belongs to a CRLF: no bare newline was introduced.
        self.assertEqual(result.proposed.count("\n"), result.proposed.count("\r\n"))
        self.assertNotIn(result.proposed.replace("\r\n", ""), "\n")

    def test_an_lf_file_gains_no_crlf(self):
        repair = SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                           period="10", objects=("pll",))
        self.assertNotIn("\r", render(repair, MISSING_SOURCE).proposed)

    def test_a_crlf_removal_preserves_crlf(self):
        crlf = (CLOCK + CLOCK).replace("\n", "\r\n")
        repair = SdcRepair(operation="remove_duplicate_clock", clock="core_clk", target=REF)
        proposed = render(repair, crlf).proposed
        self.assertIn("\r\n", proposed)
        self.assertEqual(proposed.count("\n"), proposed.count("\r\n"))

    def test_a_missing_trailing_newline_is_not_invented(self):
        repair = SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                           period="10", objects=("pll",))
        source = (CLOCK + DELAY).rstrip("\n")
        proposed = render(repair, source).proposed
        self.assertFalse(proposed.endswith("\n"))

    def test_a_multiline_definition_is_refused_rather_than_half_removed(self):
        source = ("create_clock -name core_clk -period 10 \\\n  [get_ports clk]\n"
                  "create_clock -name core_clk -period 10 \\\n  [get_ports clk]\n")
        repair = SdcRepair(operation="remove_duplicate_clock", clock="core_clk", target=REF)
        result = render(repair, source)
        self.assertIsInstance(result, RepairUnavailable)
        self.assertEqual(result.reason, "definition_not_single_line")

    def test_a_self_contained_line_the_parser_rejects_is_also_refused(self):
        """The second guard, independent of the first.

        `_is_self_contained` rejects a line that continues; this rejects one that IS whole but that the
        parser will not read - here an option outside `create_clock`'s subset.

        The baseline is taken from a CLEAN two-line file so the analysis is `complete`, and the source
        passed alongside it carries the third line. Those two disagree on purpose: it is the situation
        the guard exists for, where recorded evidence names a line that is not the constraint it
        described. Deriving the baseline from the three-line file instead would not reach this branch at
        all - the `-bogus` option makes that file's analysis `incomplete`, and `incomplete_evidence`
        refuses first, which is itself the correct answer.
        """
        from dataclasses import replace as dc_replace

        baseline = analyse(CLOCK + CLOCK)
        self.assertTrue(baseline.complete)
        finding = dc_replace(
            baseline.findings[0],
            details=(("clock", "clock", "core_clk"), ("lines", "lines", "1,3"),
                     ("occurrences", "occurrences", 3)))
        source = CLOCK + CLOCK + "create_clock -name core_clk -period 10 -bogus [get_ports clk]\n"
        result = render_sdc_repair(
            SdcRepair(operation="remove_duplicate_clock", clock="core_clk", target=REF),
            dc_replace(baseline, findings=(finding,)), source)
        self.assertIsInstance(result, RepairUnavailable)
        self.assertEqual(result.reason, "definition_not_single_line")

    def test_the_insertion_point_does_not_depend_on_finding_order(self):
        """`min` of the recorded lines, not the first or last one seen.

        The analyzer emits findings in source order, so a caller passing them through unchanged would
        never distinguish `min` from "the first one". Handing the renderer the same findings REVERSED
        separates them - and pins that the baseline's ordering is not load-bearing.
        """
        from dataclasses import replace as dc_replace

        source = CLOCK + DELAY + DELAY
        baseline = analyse(source)
        self.assertEqual(len(baseline.findings), 2)
        self.assertEqual([f.provenance.line for f in baseline.findings], [2, 3])

        reversed_baseline = dc_replace(baseline, findings=tuple(reversed(baseline.findings)))
        repair = SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                           period="10", objects=("pll",))
        from_reversed = render_sdc_repair(repair, reversed_baseline, source)
        self.assertIsInstance(from_reversed, RenderedSdcRepair)
        self.assertEqual(from_reversed.proposed, render_sdc_repair(repair, baseline, source).proposed,
                         "reversing the findings changed the rendered output")
        lines = from_reversed.proposed.splitlines()
        clock_line = next(i for i, line in enumerate(lines) if "pll_clk -period" in line)
        self.assertLess(clock_line, next(i for i, line in enumerate(lines) if "-clock pll_clk 0.4" in line))

    def test_the_source_is_never_shortened_when_a_removal_is_refused(self):
        source = DUPLICATE_SOURCE
        before = source
        render(SdcRepair(operation="remove_duplicate_clock", clock="core_clk",
                         target="constraints/elsewhere.sdc"), source)
        self.assertEqual(source, before)


class TheRendererIsInert(unittest.TestCase):
    """13, 14, 15, 18: no filesystem, no writer, no model, no outcome claim."""

    #: Standard library modules the renderer is allowed to use for pure computation.
    ALLOWED_STDLIB = {"__future__", "dataclasses", "typing"}

    @property
    def _source(self) -> str:
        return Path(sys.modules[render_sdc_repair.__module__].__file__).read_text(encoding="utf-8")

    def _tree(self) -> ast.Module:
        return ast.parse(self._source)

    def _code(self) -> str:
        """The module with every docstring removed.

        Prose is excluded on purpose. This module's own docstring says "no LLM, no RepositoryWriter" -
        precisely because it uses neither - so scanning raw text would report the documentation of the
        boundary as a violation of it. What must be clean is the CODE: identifiers, imports, attributes
        and string literals.
        """
        tree = self._tree()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
        return ast.unparse(tree)

    def test_the_renderer_has_no_filesystem_access(self):
        imported = set()
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for forbidden in ("os", "io", "shutil", "subprocess", "pathlib", "tempfile", "socket",
                          "urllib", "requests", "http"):
            with self.subTest(module=forbidden):
                self.assertFalse([name for name in imported if name.split(".")[-1] == forbidden],
                                 f"the renderer must not import {forbidden}")
        attributes = {node.attr for node in ast.walk(self._tree()) if isinstance(node, ast.Attribute)}
        for forbidden in ("open", "read_text", "write_text", "read_bytes", "write_bytes",
                          "unlink", "rename", "mkdir", "exists"):
            with self.subTest(attribute=forbidden):
                self.assertNotIn(forbidden, attributes)

    def test_the_renderer_does_not_reference_the_writer(self):
        code = self._code()
        for forbidden in ("RepositoryWriter", "repository_writer", "RepairApproval", "content_hash",
                          "Approval", "write", "apply", "commit"):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, code)

    def test_the_renderer_does_not_call_an_llm(self):
        code = self._code().lower()
        for forbidden in ("llm", "model", "generate", "prompt", "completion", "agent", "invoke"):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, code)

    def test_the_renderer_imports_no_domain_outside_its_own(self):
        imported = set()
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        # from .findings import ... reports node.module as "findings" with level 1, so a relative
        # import names the same package without the dotted prefix.
        outside = [name for name in imported
                   if not name.startswith("debugagent.domains.vlsi")
                   and name not in RELATIVE_DOMAIN_MODULES
                   and name.split(".")[0] not in self.ALLOWED_STDLIB]
        self.assertEqual(outside, [], f"the renderer should need only its own domain and stdlib: "
                                      f"{sorted(outside)}")

    def test_the_renderer_claims_no_outcome(self):
        code = self._code().lower()
        # Precise phrases, not bare substrings: "proven" is inside the legitimate word "provenance",
        # and a test that cannot tell those apart trains you to ignore it.
        for forbidden in ("verified", "fixed", "this fixes", "root_cause", "resolved",
                          "is correct", "signoff"):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, code)

    def test_no_rendered_record_claims_an_outcome(self):
        repair = SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                           period="10", objects=("pll",))
        result = render(repair, MISSING_SOURCE)
        self.assertEqual(set(result.to_dict()),
                         {"operation", "clock", "target", "proposed", "inserted", "removed"})


class PatchGeneratorIntegration(unittest.TestCase):
    """16, 17: the existing generator consumes this unchanged, at the same trust level."""

    def test_the_trust_banner_is_byte_for_byte_unchanged(self):
        self.assertEqual(
            PROPOSAL_HEADER,
            "PROPOSED PATCH (unapplied, unverified): a candidate change for the engineer to review. "
            "It has NOT been written, applied, committed or run, and it is not a verified fix.")

    def test_rendered_content_feeds_the_existing_patch_task(self):
        repair = SdcRepair(operation="define_clock", clock="pll_clk", target=REF,
                           period="10", objects=("pll",))
        result = render(repair, MISSING_SOURCE)
        task = build_patch_task(task_id="t-1", case_signature="missing_constraint:pll_clk",
                                target=REF, proposed=result.proposed)
        self.assertEqual(task.agent, "patch_generator")
        self.assertEqual(dict(task.context.environment)["proposed_content"], result.proposed)

    def test_a_removal_also_feeds_the_patch_task(self):
        repair = SdcRepair(operation="remove_duplicate_clock", clock="core_clk", target=REF)
        result = render(repair, DUPLICATE_SOURCE)
        task = build_patch_task(task_id="t-2", case_signature="duplicate_constraint:core_clk",
                                target=REF, proposed=result.proposed)
        self.assertEqual(dict(task.context.environment)["proposed_content"], result.proposed)

    def test_the_proposed_content_still_yields_a_diff(self):
        from debugagent.agents.source_files import RepositoryScope  # noqa: F401
        import difflib

        repair = SdcRepair(operation="remove_duplicate_clock", clock="core_clk", target=REF)
        result = render(repair, DUPLICATE_SOURCE)
        diff = list(difflib.unified_diff(DUPLICATE_SOURCE.splitlines(), result.proposed.splitlines(),
                                         fromfile=REF, tofile=REF, lineterm=""))
        self.assertTrue(any(line.startswith("-create_clock") for line in diff))
        self.assertTrue(any(line.startswith("+") and "pll_clk" not in line for line in diff))


if __name__ == "__main__":
    unittest.main()