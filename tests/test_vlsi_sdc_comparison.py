"""VLSI-1D.2: typed SDC repair comparison.

Two analyses in, a per-identity breakdown out. Every test here runs the REAL parser and analyzer over real
SDC text and compares the typed results - nothing is a hand-built finding, because a comparison fed
invented inputs would only prove that the set arithmetic works.

The properties that matter, in the order they are asserted below:

- **identity, not position.** A defect that moves from line 3 to line 7 is still present.
- **INCOMPLETE overrides everything.** A failed re-analysis reports nothing as resolved, however empty its
  finding set looks. This is the false-repair case the whole module exists to prevent.
- **the negative control.** A syntactically valid but ineffective repair must NOT read as a fix.
- **no aggregate success while anything remains.** `verified` is unreachable with a `still_present`, a
  `new`, or an `incomplete`.
"""

from __future__ import annotations

import unittest

from debugagent.domains.vlsi.comparison import (
    COMPARISON_OUTCOMES,
    COMPARISON_STATUSES,
    RepairVerification,
    compare_sdc_findings,
)
from debugagent.domains.vlsi.findings import VlsiFinding
from debugagent.domains.vlsi.provenance import Provenance
from debugagent.domains.vlsi.sdc_analyzer import SdcAnalysisResult, analyze_sdc_constraints
from debugagent.domains.vlsi.sdc_parser import parse_sdc

CLOCK = "create_clock -name core_clk -period 10 [get_ports clk]\n"

# The defect under test: an input delay naming a clock that was never defined, and one clock defined
# twice with different periods.
BROKEN = (CLOCK
          + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n"
          + CLOCK.replace("period 10", "period 12"))

# A valid but INEFFECTIVE repair: pll_clk gets defined, so that half is genuinely fixed, while core_clk is
# left contradictory. This is the shape a plausible-looking but wrong agent repair actually takes.
PARTIAL = (CLOCK
           + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n"
           + "create_clock -name pll_clk -period 10 [get_ports pll]\n"
           + CLOCK.replace("period 10", "period 12"))

# A complete repair: pll_clk defined and core_clk defined once.
FIXED = (CLOCK
         + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n"
         + "create_clock -name pll_clk -period 10 [get_ports pll]\n")

CLEAN = CLOCK

MISSING_PLL = "missing_constraint:clock=pll_clk,constraint_kind=set_input_delay,objects=d"
CONFLICT = "conflicting_constraint:clock=core_clk"
DUPLICATE = "duplicate_constraint:clock=core_clk"


def analyse(text: str, ref: str = "constraints/top.sdc"):
    return analyze_sdc_constraints(parse_sdc(text, ref=ref))


def compare(before_text: str, after_text, ref: str = "constraints/top.sdc"):
    after = None if after_text is None else analyse(after_text, ref)
    return compare_sdc_findings(analyse(before_text, ref), after)


class Resolution(unittest.TestCase):
    """1, 2, 4: the plain cases."""

    def test_a_missing_finding_that_is_gone_is_resolved(self):
        result = compare(BROKEN, FIXED)
        self.assertEqual(result.identities("resolved"), tuple(sorted((MISSING_PLL, CONFLICT))))

    def test_a_missing_finding_that_remains_is_still_present(self):
        result = compare(BROKEN, BROKEN)
        self.assertEqual(result.identities("still_present"), tuple(sorted((MISSING_PLL, CONFLICT))))
        self.assertEqual(result.identities("resolved"), ())

    def test_a_duplicate_finding_that_is_gone_is_resolved(self):
        duplicated = CLOCK + CLOCK
        result = compare(duplicated, CLOCK)
        self.assertEqual(result.identities("resolved"), (DUPLICATE,))
        self.assertEqual(result.status, "verified")

    def test_a_duplicate_that_remains_is_still_present(self):
        duplicated = CLOCK + CLOCK
        result = compare(duplicated, duplicated)
        self.assertEqual(result.identities("still_present"), (DUPLICATE,))


class DuplicateIsNotConflicting(unittest.TestCase):
    """5, 18: two distinct conditions on one clock must never merge."""

    def test_duplicate_and_conflicting_share_a_clock_but_not_an_identity(self):
        duplicate = analyse(CLOCK + CLOCK)
        conflicting = analyse(CLOCK + CLOCK.replace("period 10", "period 12"))
        duplicate_identity = duplicate.findings[0].identity
        conflicting_identity = conflicting.findings[0].identity
        self.assertNotEqual(duplicate_identity, conflicting_identity)
        self.assertEqual(duplicate_identity, DUPLICATE)
        self.assertEqual(conflicting_identity, CONFLICT)

    def test_replacing_a_duplicate_with_a_conflict_is_resolved_plus_new(self):
        """The clock got worse, not better - and the comparison says so rather than calling it a fix."""
        duplicate = CLOCK + CLOCK
        conflicting = CLOCK + CLOCK.replace("period 10", "period 12")
        result = compare(duplicate, conflicting)
        self.assertEqual(result.identities("resolved"), (DUPLICATE,))
        self.assertEqual(result.identities("new"), (CONFLICT,))
        self.assertFalse(result.verified)

    def test_resolving_a_conflict_that_became_a_duplicate_is_resolved_plus_new(self):
        result = compare(CLOCK + CLOCK.replace("period 10", "period 12"), CLOCK + CLOCK)
        self.assertEqual(result.identities("resolved"), (CONFLICT,))
        self.assertEqual(result.identities("new"), (DUPLICATE,))


class LineMovement(unittest.TestCase):
    """6, 17: position is not identity."""

    def test_the_same_identity_on_a_later_line_is_still_present(self):
        before = CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n"
        after = CLOCK + "\n\n\n\n" + CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n"
        result = compare(before, after)
        missing = [c for c in result.comparisons if c.identity == MISSING_PLL]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0].outcome, "still_present")
        self.assertNotEqual(missing[0].before_line, missing[0].after_line,
                            "the test is meaningless unless the line actually moved")
        self.assertIsNone(missing[0].after_line if missing[0].after_line == missing[0].before_line else None)

    def test_the_line_really_moves(self):
        before = CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n"
        after = CLOCK + "\n\n\n\n" + CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n"
        entry = next(c for c in compare(before, after).comparisons if c.identity == MISSING_PLL)
        self.assertEqual((entry.before_line, entry.after_line), (2, 7))

    def test_matching_ignores_the_location_entirely(self):
        """Two identical identities at two locations must collapse to one comparison entry."""
        before = CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n"
        after = before.replace("d]", "d]\n" + CLOCK)
        result = compare(before, after)
        self.assertEqual(len([c for c in result.comparisons if c.identity == MISSING_PLL]), 1)

    def test_a_location_supplied_as_a_detail_cannot_enter_the_identity(self):
        """The identity must stay positional-free even when position is handed to it directly.

        The analyser puts `lines` on a conflicting clock, never `line`, so the ordinary text path
        cannot exercise this. But `VlsiFinding` is publicly constructed, and identity is derived in
        `__post_init__` from whatever details it is given - so a caller that supplies `line` reaches
        `canonical_identity` with it. If a positional field were ever admitted to the identity rule,
        the identity would become `conflicting_constraint:clock=core_clk,line=3`, a defect that moved
        from line 3 to line 7 would stop matching itself, and the comparison would report a
        still-present conflict as RESOLVED. That is the exact false repair this module exists to
        prevent, so it is pinned from this side rather than trusted to the allowlist alone.
        """
        def conflict_at(line):
            return VlsiFinding(
                kind="conflicting_constraint", severity="critical",
                message="clock core_clk is defined more than once",
                provenance=Provenance("file", "constraints/top.sdc", line=line),
                details=(("clock", "str", "core_clk"), ("line", "int", line)))

        moved = compare_sdc_findings(SdcAnalysisResult(findings=(conflict_at(3),)),
                                    SdcAnalysisResult(findings=(conflict_at(7),)))
        self.assertEqual(moved.identities("still_present"), (CONFLICT,))
        self.assertEqual(moved.identities("resolved"), (),
                         "a defect that moved down the file must not read as repaired")
        for entry in moved.comparisons:
            for positional in ("line=", "lines=", "periods=", "occurrence"):
                self.assertNotIn(positional, entry.identity,
                                 f"{positional} must never take part in matching")


class ChangedContentSameIdentity(unittest.TestCase):
    """3, 7: identity survives exactly the changes a repair causes."""

    def test_conflicting_periods_converging_is_still_present(self):
        """`periods` is what the repair changes, so it cannot be in the identity."""
        before = analyse(CLOCK + CLOCK.replace("period 10", "period 12"))
        after = analyse(CLOCK + CLOCK.replace("period 10", "period 14"))
        self.assertNotEqual([f.details_dict.get("periods") for f in before.findings],
                            [f.details_dict.get("periods") for f in after.findings],
                            "the periods must actually differ for this test to mean anything")
        result = compare_sdc_findings(before, after)
        self.assertEqual(result.identities("still_present"), (CONFLICT,))
        self.assertFalse(result.verified)

    def test_three_occurrences_reduced_to_two_is_still_present(self):
        """A clock still contradictory after a partial repair must not read as fixed."""
        three = (CLOCK + CLOCK.replace("period 10", "period 12")
                 + CLOCK.replace("period 10", "period 14"))
        two = CLOCK + CLOCK.replace("period 10", "period 12")
        result = compare(three, two)
        self.assertEqual(result.identities("still_present"), (CONFLICT,))

    def test_renaming_a_clock_is_resolved_plus_new(self):
        before = CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n"
        after = CLOCK + "set_input_delay -clock spi_clk 0.4 [get_ports d]\n"
        result = compare(before, after)
        self.assertEqual(result.identities("resolved"), (MISSING_PLL,))
        self.assertEqual(result.identities("new"),
                         ("missing_constraint:clock=spi_clk,constraint_kind=set_input_delay,objects=d",))
        self.assertEqual(result.status, "findings")
        self.assertFalse(result.verified)


class MixedOutcomes(unittest.TestCase):
    """8, 9: a partial improvement is a partial improvement, not a fix."""

    def test_two_before_one_after_gives_one_resolved_and_one_still_present(self):
        result = compare(BROKEN, PARTIAL)
        self.assertEqual(result.identities("resolved"), (MISSING_PLL,))
        self.assertEqual(result.identities("still_present"), (CONFLICT,))

    def test_an_unrelated_new_finding_is_reported_and_blocks_verified(self):
        before = CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n"
        after = FIXED + "set_output_delay -clock mm_clk 0.4 [get_ports q]\n"
        result = compare(before, after)
        self.assertEqual(result.identities("resolved"), (MISSING_PLL,))
        self.assertEqual(result.identities("new"),
                         ("missing_constraint:clock=mm_clk,constraint_kind=set_output_delay,objects=q",))
        self.assertEqual(result.status, "findings")
        self.assertFalse(result.verified)

    def test_a_new_finding_never_appears_as_resolved(self):
        result = compare(CLOCK, CLOCK + CLOCK.replace("period 10", "period 12"))
        self.assertEqual(result.identities("new"), (CONFLICT,))
        self.assertEqual(result.identities("resolved"), ())


class IncompleteOverridesEverything(unittest.TestCase):
    """10-13: the false-repair guard."""

    def test_an_incomplete_after_is_incomplete(self):
        result = compare(BROKEN, "create_clock -name\n")
        self.assertEqual(result.status, "incomplete")
        self.assertFalse(result.verified)

    def test_an_incomplete_after_reports_nothing_resolved(self):
        result = compare(BROKEN, "create_clock -name\n")
        self.assertEqual(result.identities("resolved"), (),
                         "an incomplete analysis must never resolve a finding")
        self.assertEqual({c.outcome for c in result.comparisons}, {"incomplete"})

    def test_an_after_with_no_result_at_all_is_incomplete(self):
        result = compare(BROKEN, None)
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.identities("resolved"), ())
        self.assertEqual(result.after_total, 0)

    def test_an_absent_after_is_distinguished_from_a_failed_one(self):
        """"The re-analysis never ran" and "the re-analysis ran and failed" are different faults.

        Both are `incomplete`, so the STATUS cannot separate them - only the reason can. That
        distinction is what tells an engineer whether to look at the analyser or at the toolchain, so
        it is asserted here rather than left to whatever sentence the code happens to build.
        """
        absent = compare(BROKEN, None)
        failed = compare(BROKEN, "create_clock -name\n")
        self.assertEqual(absent.status, failed.status)
        self.assertNotEqual(absent.note, failed.note)
        self.assertIn("no result", absent.note)
        self.assertIn("incomplete", failed.note)

    def test_an_after_rejecting_evidence_it_could_not_produce_is_incomplete(self):
        """A file that yielded no SDC constraints at all must not read as clean."""
        result = compare(BROKEN, "def handler(r):\n    return 200\n")
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.identities("resolved"), ())

    def test_zero_after_findings_with_an_incomplete_after_is_not_resolution(self):
        """The exact false-repair shape: empty set, failed analysis, everything 'resolved'."""
        incomplete = analyse("create_clock -name\n")
        self.assertEqual(len(incomplete.findings), 0, "the guard needs a genuinely empty finding set")
        self.assertNotEqual(incomplete.status, "complete")
        result = compare_sdc_findings(analyse(BROKEN), incomplete)
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.identities("resolved"), ())
        self.assertFalse(result.verified)

    def test_many_before_findings_with_an_incomplete_after_are_all_incomplete(self):
        many = (CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n"
                + "set_output_delay -clock mm_clk 0.4 [get_ports q]\n"
                + CLOCK.replace("period 10", "period 12"))
        result = compare(many, "create_clock -name\n")
        self.assertEqual(len(result.comparisons), 3)
        self.assertEqual(result.identities("resolved"), ())
        self.assertEqual({c.outcome for c in result.comparisons}, {"incomplete"})

    def test_a_complete_after_carrying_analysis_unavailable_is_incomplete(self):
        """The guard the analyser path cannot reach, so it has to be built directly.

        `SdcAnalysisResult.status` is `complete` when there are no parser issues, and the analyser
        emits `analysis_unavailable` only alongside one - so on the text path these two never co-occur.
        But a `complete` result CAN carry that finding, and then its empty-or-short finding list is not
        evidence of anything. Building the record directly is the only way to cover that, and it is the
        reason the check exists rather than being redundant with the status check.
        """
        marker = VlsiFinding(kind="analysis_unavailable", severity="critical",
                             message="evidence could not be produced",
                             provenance=Provenance("file", "constraints/top.sdc", line=1),
                             details=(("reason", "reason", "partial read"),))
        after = SdcAnalysisResult(findings=(marker,))
        self.assertEqual(after.status, "complete", "the record must look complete for this to bite")

        result = compare_sdc_findings(analyse(BROKEN), after)
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.identities("resolved"), ())
        self.assertIn("analysis_unavailable", result.note)

    def test_an_incomplete_after_reports_nothing_new(self):
        """When the after set is untrustworthy, a 'new' claim is as unsafe as a 'resolved' one."""
        before = CLOCK
        after = CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\ncreate_clock -name\n"
        result = compare(before, after)
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.identities("new"), ())

    def test_an_incomplete_before_does_not_manufacture_new_findings(self):
        before = analyse("create_clock -name\n")
        result = compare_sdc_findings(before, analyse(FIXED))
        self.assertEqual(result.identities("new"), (),
                         "a baseline that could not be read cannot establish what is new")


class AggregateStatus(unittest.TestCase):
    """14, and the rules that make `verified` unreachable when it should be."""

    def test_a_clean_baseline_is_not_applicable_not_verified(self):
        result = compare(CLEAN, CLEAN)
        self.assertEqual(result.status, "not_applicable")
        self.assertFalse(result.verified)
        self.assertIn("not evidence that a repair worked", result.note)

    def test_a_full_repair_is_verified(self):
        result = compare(BROKEN, FIXED)
        self.assertEqual(result.status, "verified")
        self.assertTrue(result.verified)

    def test_verified_is_unreachable_while_any_baseline_finding_remains(self):
        result = compare(BROKEN, PARTIAL)
        self.assertNotEqual(result.status, "verified")
        self.assertFalse(result.verified)

    def test_verified_is_unreachable_when_something_new_appears(self):
        result = compare(BROKEN, FIXED + "set_output_delay -clock mm_clk 0.4 [get_ports q]\n")
        self.assertNotEqual(result.status, "verified")
        self.assertFalse(result.verified)

    def test_verified_is_unreachable_when_incomplete(self):
        self.assertFalse(compare(BROKEN, "create_clock -name\n").verified)

    def test_verified_property_agrees_with_the_breakdown_not_just_the_status(self):
        """A status string that disagrees with its own entries must not be believed."""
        forged = RepairVerification(status="verified",
                                    comparisons=(
                                        __import__("debugagent.domains.vlsi.comparison",
                                                   fromlist=["FindingComparison"])
                                        .FindingComparison("still_present", CONFLICT, "conflicting_constraint"),
                                    ))
        self.assertEqual(forged.status, "verified")
        self.assertFalse(forged.verified, "the property is derived from the entries, not the label")

    def test_every_status_and_outcome_is_a_declared_constant(self):
        for result in (compare(BROKEN, FIXED), compare(BROKEN, PARTIAL),
                       compare(CLEAN, CLEAN), compare(BROKEN, None)):
            self.assertIn(result.status, COMPARISON_STATUSES)
            for entry in result.comparisons:
                self.assertIn(entry.outcome, COMPARISON_OUTCOMES)


class Determinism(unittest.TestCase):
    """15, and 16: the comparison reads identity, never the wording."""

    def test_the_same_inputs_always_produce_the_same_output(self):
        first, second = compare(BROKEN, PARTIAL), compare(BROKEN, PARTIAL)
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_ordering_is_baseline_then_new_then_identity(self):
        before = (CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n"
                  + "set_output_delay -clock mm_clk 0.4 [get_ports q]\n")
        after = (CLOCK + "set_input_delay -clock spi_clk 0.4 [get_ports d]\n"
                 + "set_output_delay -clock mm_clk 0.4 [get_ports q]\n"
                 + "set_input_delay -clock uart_clk 0.4 [get_ports u]\n")
        entries = compare(before, after).comparisons
        # Grouped, not outcome-ordered: baseline-origin entries first, then the new ones, sorted by
        # identity inside each group. Within the baseline group the outcome itself does not decide the
        # position - mm sorts before pll, so "still_present" comes first.
        self.assertEqual([e.outcome for e in entries],
                         ["still_present", "resolved", "new", "new"])
        self.assertEqual([e.identity for e in entries[:2]],
                         sorted(e.identity for e in entries[:2]))
        self.assertEqual([e.identity for e in entries[2:]],
                         sorted(e.identity for e in entries[2:]))
        self.assertEqual({e.outcome for e in entries[:2]}, {"resolved", "still_present"})

    def test_the_comparison_does_not_read_the_message(self):
        """Two findings differing only in wording must compare as the same condition."""
        from dataclasses import replace

        original = analyse(BROKEN).findings[0]
        reworded = replace(original, message="a completely different sentence about something else")
        
        mutated = replace(analyse(BROKEN), findings=(reworded,))
        result = compare_sdc_findings(analyse(BROKEN), mutated)
        self.assertEqual(result.identities("still_present"), (original.identity,))
        self.assertNotEqual(original.message, reworded.message)


class NegativeControl(unittest.TestCase):
    """The core VLSI-1D.2 behaviour: a valid but ineffective repair must not read as a fix."""

    def test_a_partially_effective_repair_is_not_verified(self):
        result = compare(BROKEN, PARTIAL)
        self.assertEqual(result.identities("resolved"), (MISSING_PLL,))
        self.assertEqual(result.identities("still_present"), (CONFLICT,))
        self.assertNotEqual(result.status, "verified")
        self.assertFalse(result.verified)
        self.assertIn("not a verified repair", result.note)

    def test_the_untouched_defect_is_reported_with_both_locations(self):
        entry = next(c for c in compare(BROKEN, PARTIAL).comparisons
                     if c.identity == CONFLICT)
        self.assertEqual(entry.outcome, "still_present")
        self.assertTrue(entry.before_ref)
        self.assertTrue(entry.after_ref)
        self.assertEqual(entry.before_ref, entry.after_ref)

    def test_a_repair_that_changes_nothing_is_not_a_repair(self):
        result = compare(BROKEN, BROKEN)
        self.assertEqual(result.status, "findings")
        self.assertEqual(result.identities("resolved"), ())


class Serialisation(unittest.TestCase):
    """Enough structured information for a generic UI to render without understanding VLSI."""

    def test_to_dict_carries_the_outcome_and_identity(self):
        entry = compare(BROKEN, PARTIAL).to_dict()
        self.assertEqual(entry["status"], "findings")
        self.assertEqual(len(entry["comparisons"]), 2)
        for record in entry["comparisons"]:
            self.assertIn(record["outcome"], COMPARISON_OUTCOMES)
            self.assertIn("identity", record)
            self.assertIn("kind", record)

    def test_to_dict_omits_absent_locations_rather_than_reporting_none(self):
        entry = next(c for c in compare(BROKEN, FIXED).comparisons).to_dict()
        self.assertNotIn("after_ref", entry)
        self.assertNotIn("after_line", entry)

    def test_the_verification_is_frozen(self):
        result = compare(BROKEN, PARTIAL)
        with self.assertRaises(AttributeError):
            result.status = "verified"


if __name__ == "__main__":
    unittest.main()