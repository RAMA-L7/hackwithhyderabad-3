"""VLSI-1A: the deterministic SDC reader.

Parsing only. These tests assert what the reader DID with a file - which constraints it read, at which
lines, and what it reported instead - and never whether a constraint is correct. The distinction is
load-bearing: a test that asserted "this clock is valid" would be doing VLSI-1B's job, and would fail
for reasons that have nothing to do with parsing.

The negative tests carry most of the weight here. A partial reader is dangerous in one specific way:
if it silently skips what it cannot handle, its output looks like a complete reading of the file. So
every case below asserts that something was REPORTED, not merely that something was absent.
"""

from __future__ import annotations

import unittest

from debugagent.domains.vlsi.sdc_parser import (
    ISSUE_KINDS,
    SUPPORTED_COMMANDS,
    SdcParseResult,
    parse_sdc,
)

REF = "constraints/top.sdc"


def _parse(text: str, ref: str = REF) -> SdcParseResult:
    return parse_sdc(text, ref=ref)


def _one(text: str, ref: str = REF):
    """Parse text expected to yield exactly one constraint and no issues."""
    result = _parse(text, ref)
    assert len(result.constraints) == 1, f"expected 1 constraint, got {len(result.constraints)}: {result.to_dict()}"
    assert not result.issues, f"expected no issues, got {[i.to_dict() for i in result.issues]}"
    return result.constraints[0]


def _kinds(result: SdcParseResult) -> list[str]:
    return [constraint.KIND for constraint in result.constraints]


def _issue_kinds(result: SdcParseResult) -> list[str]:
    return [issue.kind for issue in result.issues]


class CreateClockParsing(unittest.TestCase):
    """`create_clock` - the first constraint family in the incremental order."""

    def test_basic_clock_with_a_bare_port(self):
        clock = _one("create_clock -period 10.000 clk")
        self.assertEqual(clock.KIND, "create_clock")
        self.assertEqual(clock.period, "10.000")
        self.assertEqual(clock.objects, ("clk",))
        self.assertIsNone(clock.name)

    def test_named_clock(self):
        clock = _one("create_clock -name clk_sys -period 10.000 [get_ports clk_sys]")
        self.assertEqual(clock.name, "clk_sys")
        self.assertEqual(clock.objects, ("clk_sys",))

    def test_period_is_preserved_exactly_as_written(self):
        """No rounding, no canonicalisation, no conversion."""
        for written in ("10", "10.000", "0.5", "2.5000", "1e-3"):
            with self.subTest(period=written):
                self.assertEqual(_one(f"create_clock -period {written} clk").period, written)

    def test_value_with_an_attached_unit_is_split_but_not_converted(self):
        clock = _one("create_clock -period 10ns clk")
        self.assertEqual(clock.period, "10", "the numeric part was altered")
        self.assertEqual(clock.unit, "ns")

    def test_unrecognised_unit_suffix_is_not_split(self):
        """`2m` is not a time unit; splitting it would invent a value the file never stated."""
        clock = _one("create_clock -period 2m clk")
        self.assertEqual(clock.period, "2m")
        self.assertIsNone(clock.unit)

    def test_target_via_a_command_substitution(self):
        self.assertEqual(_one("create_clock -period 10 [get_ports clk_in]").objects, ("clk_in",))

    def test_grouped_target_yields_each_name(self):
        clock = _one("create_clock -period 10 [get_ports {clk_a clk_b}]")
        self.assertEqual(clock.objects, ("clk_a", "clk_b"))

    def test_multiple_target_forms(self):
        self.assertEqual(_one("create_clock -period 10 [get_ports {clk}]").objects, ("clk",))
        self.assertEqual(_one("create_clock -period 10 [get_ports data_in]").objects, ("data_in",))
        self.assertEqual(_one("create_clock -period 10 [get_pins {u1/clk}]").objects, ("u1/clk",))
        # A single-word accessor names a collection, and must not be emptied by dropping it.
        self.assertEqual(_one("create_clock -period 10 [all_inputs]").objects, ("all_inputs",))

    def test_multiple_objects_are_sorted_and_deduplicated(self):
        clock = _one("create_clock -period 10 [get_ports {clk_b clk_a clk_b}]")
        self.assertEqual(clock.objects, ("clk_a", "clk_b"))

    def test_waveform_is_kept_as_written(self):
        clock = _one("create_clock -period 10 -waveform {0.000 5.000} clk")
        self.assertEqual(clock.waveform, "0.000 5.000")

    def test_multiple_clocks_are_all_read_in_source_order(self):
        result = _parse("create_clock -name a -period 1 clk_a\ncreate_clock -name b -period 2 clk_b")
        self.assertEqual([c.name for c in result.constraints], ["a", "b"])
        self.assertEqual([c.provenance.line for c in result.constraints], [1, 2])

    def test_missing_period_is_malformed_not_skipped(self):
        result = _parse("create_clock -name clk_sys [get_ports clk]")
        self.assertEqual(result.constraints, ())
        self.assertEqual(_issue_kinds(result), ["malformed"])
        self.assertIn("-period", result.issues[0].message)

    def test_period_with_no_value_is_malformed(self):
        result = _parse("create_clock -name clk_sys -period")
        self.assertEqual(result.constraints, ())
        self.assertEqual(_issue_kinds(result), ["malformed"])

    def test_unsupported_command_is_reported_as_unsupported(self):
        result = _parse("set_false_path -from a -to b")
        self.assertEqual(result.constraints, ())
        self.assertEqual(_issue_kinds(result), ["unsupported"])
        self.assertFalse(result.issues[0].supported_command)

    def test_an_unsupported_option_is_reported_not_ignored(self):
        """An option that changes meaning cannot be dropped without misrepresenting the constraint."""
        result = _parse("create_clock -period 10 -add_different_clock clk")
        self.assertEqual(result.constraints, ())
        self.assertEqual(_issue_kinds(result), ["unsupported"])
        self.assertIn("-add_different_clock", result.issues[0].message)


class InputDelayParsing(unittest.TestCase):
    """`set_input_delay`."""

    def test_basic_input_delay(self):
        delay = _one("set_input_delay -clock clk_app 0.500 [get_ports data_in]")
        self.assertEqual(delay.KIND, "set_input_delay")
        self.assertEqual(delay.clock, "clk_app")
        self.assertEqual(delay.value, "0.500")
        self.assertEqual(delay.objects, ("data_in",))

    def test_clock_reference_is_recorded(self):
        self.assertEqual(_one("set_input_delay -clock [get_clocks clk] 0.5 [get_ports d]").clock, "clk")

    def test_target_ports(self):
        self.assertEqual(_one("set_input_delay -clock c 0.5 [get_ports {d0 d1}]").objects, ("d0", "d1"))

    def test_max_and_min_qualifiers(self):
        self.assertEqual(_one("set_input_delay -clock c -max 0.5 [get_ports d]").qualifier, "max")
        self.assertEqual(_one("set_input_delay -clock c -min 0.2 [get_ports d]").qualifier, "min")
        self.assertIsNone(_one("set_input_delay -clock c 0.5 [get_ports d]").qualifier)

    def test_an_invalid_qualifier_is_malformed_not_guessed(self):
        result = _parse("set_input_delay -clock c -typical 0.5 [get_ports d]")
        self.assertEqual(result.constraints, ())
        self.assertEqual(_issue_kinds(result), ["unsupported"])

    def test_value_with_attached_unit(self):
        delay = _one("set_input_delay -clock c 500ps [get_ports d]")
        self.assertEqual((delay.value, delay.unit), ("500", "ps"))

    def test_multiple_input_delays_keep_source_order(self):
        result = _parse("set_input_delay -clock a 0.1 [get_ports d0]\n"
                        "set_input_delay -clock b 0.2 [get_ports d1]")
        self.assertEqual([d.objects[0] for d in result.constraints], ["d0", "d1"])
        self.assertEqual([d.clock for d in result.constraints], ["a", "b"])

    def test_missing_clock_is_malformed(self):
        result = _parse("set_input_delay 0.5 [get_ports d]")
        self.assertEqual(result.constraints, ())
        self.assertIn("-clock", result.issues[0].message)

    def test_missing_value_is_malformed(self):
        result = _parse("set_input_delay -clock c [get_ports d]")
        self.assertEqual(result.constraints, ())
        self.assertEqual(_issue_kinds(result), ["malformed"])

    def test_unsupported_option_is_reported(self):
        result = _parse("set_input_delay -clock c -add_delay 0.5 [get_ports d]")
        self.assertEqual(_issue_kinds(result), ["unsupported"])


class OutputDelayParsing(unittest.TestCase):
    """`set_output_delay`."""

    def test_basic_output_delay(self):
        delay = _one("set_output_delay -clock clk_app 0.400 [get_ports data_out]")
        self.assertEqual(delay.KIND, "set_output_delay")
        self.assertEqual(delay.clock, "clk_app")
        self.assertEqual(delay.value, "0.400")

    def test_clock_reference_and_targets(self):
        delay = _one("set_output_delay -clock [get_clocks clk] 0.4 [get_ports {q0 q1}]")
        self.assertEqual(delay.clock, "clk")
        self.assertEqual(delay.objects, ("q0", "q1"))

    def test_multiple_output_delays(self):
        result = _parse("set_output_delay -clock a 0.1 [get_ports q0]\n"
                        "set_output_delay -clock b 0.2 [get_ports q1]")
        self.assertEqual(len(result.constraints), 2)
        self.assertEqual([d.clock for d in result.constraints], ["a", "b"])

    def test_missing_clock_is_malformed(self):
        result = _parse("set_output_delay 0.4 [get_ports q]")
        self.assertEqual(result.constraints, ())
        self.assertEqual(_issue_kinds(result), ["malformed"])

    def test_input_and_output_delay_are_distinct(self):
        result = _parse("set_input_delay -clock c 0.5 [get_ports d]\nset_output_delay -clock c 0.5 [get_ports q]")
        self.assertEqual(_kinds(result), ["set_input_delay", "set_output_delay"])


class Formatting(unittest.TestCase):
    """Normal SDC formatting must not change what is read."""

    def test_blank_lines_and_indentation_are_irrelevant_to_what_is_read(self):
        """Formatting changes the LINE, never the constraint.

        The provenance is excluded from the comparison deliberately: a command at line 3 is not the same
        record as one at line 1, and a reader that ignored that would be fabricating provenance.
        """
        compact = _parse("create_clock -period 10 clk").constraints[0]
        spaced = _parse("\n\n    create_clock    -period    10    clk\n\n\n").constraints[0]
        self.assertEqual(compact.to_dict()["period"], spaced.to_dict()["period"])
        self.assertEqual(compact.objects, spaced.objects)
        self.assertEqual(compact.provenance.line, 1)
        self.assertEqual(spaced.provenance.line, 3)

    def test_tabs_are_whitespace(self):
        self.assertEqual(_parse("create_clock\t-period\t10\tclk").constraints[0].objects, ("clk",))

    def test_multiple_spaces_inside_an_option_value(self):
        self.assertEqual(_parse("create_clock -period 10 -waveform {0.0  5.0} clk")
                         .constraints[0].waveform, "0.0  5.0")

    def test_full_line_comment_is_not_a_command(self):
        result = _parse("# create_clock -period 10 commented_out\ncreate_clock -period 10 clk")
        self.assertEqual(len(result.constraints), 1)
        self.assertEqual(result.issues, ())

    def test_inline_comment_is_stripped(self):
        clock = _one("create_clock -period 10 clk  # the system clock")
        self.assertEqual(clock.objects, ("clk",), "the comment leaked into the object list")

    def test_comment_inside_braces_is_literal(self):
        """`#` inside braces is part of the value, not a comment - otherwise a name could be truncated."""
        clock = _one("create_clock -period 10 -waveform {0.0 #5.0} clk")
        self.assertEqual(clock.waveform, "0.0 #5.0")

    def test_leading_and_trailing_whitespace(self):
        self.assertEqual(len(_parse("   \n\t create_clock -period 10 clk \t \n").constraints), 1)

    def test_empty_input_yields_nothing(self):
        result = _parse("")
        self.assertEqual(result.constraints, ())
        self.assertEqual(result.issues, ())
        self.assertTrue(result.ok)

    def test_a_file_of_only_comments_yields_nothing(self):
        result = _parse("# nothing here\n#  nor here\n")
        self.assertEqual((result.constraints, result.issues), ((), ()))


class Provenance(unittest.TestCase):
    """Where each statement came from, and no invented locations."""

    def test_every_record_carries_the_supplied_reference_and_a_line(self):
        result = _parse("create_clock -period 10 clk\nset_input_delay -clock clk 0.5 [get_ports d]")
        self.assertEqual([c.provenance.ref for c in result.constraints], [REF, REF])
        self.assertEqual([c.provenance.line for c in result.constraints], [1, 2])
        self.assertTrue(all(c.provenance.source_type == "file" for c in result.constraints))

    def test_issues_carry_provenance_too(self):
        result = _parse("set_false_path -from a\ncreate_clock clk")
        issue = result.issues[0]
        self.assertEqual(issue.provenance.ref, REF)
        self.assertEqual(issue.provenance.line, 1)

    def test_line_is_the_start_of_the_command_not_the_end(self):
        """A continued command must point at where it BEGINS."""
        clock = _one("create_clock -name clk_app \\\n    -period 2.500 \\\n    [get_ports clk_app]")
        self.assertEqual(clock.provenance.line, 1)

    def test_line_numbers_account_for_comments_and_blanks(self):
        result = _parse("# header\n\n\ncreate_clock -period 10 clk\n")
        self.assertEqual(result.constraints[0].provenance.line, 4)

    def test_line_numbers_track_a_continued_command_correctly(self):
        result = _parse("create_clock -period 1 a\ncreate_clock -period 2 \\\n  b\ncreate_clock -period 3 c")
        self.assertEqual([c.provenance.line for c in result.constraints], [1, 2, 4])

    def test_no_line_number_is_ever_fabricated(self):
        """Every parsed record has a real line, and none is zero or negative."""
        result = _parse("\n\ncreate_clock -period 10 clk\nset_false_path -from a")
        records = list(result.constraints) + list(result.issues)
        self.assertTrue(records)
        for record in records:
            line = record.provenance.line
            self.assertIsNotNone(line)
            self.assertGreaterEqual(line, 1)

    def test_a_different_reference_is_recorded(self):
        clock = _one("create_clock -period 10 clk", ref="constraints/io.sdc")
        self.assertEqual(clock.provenance.ref, "constraints/io.sdc")


class Determinism(unittest.TestCase):
    """Identical input, identical output - every time."""

    SOURCE = ("# constraints\ncreate_clock -name clk -period 10 -waveform {0 5} [get_ports clk]\n"
              "set_input_delay -clock clk -max 0.5 [get_ports {d0 d1}]\n"
              "set_false_path -from a -to b\n"
              "set_output_delay -clock clk 0.4 [get_ports q]\n")

    def test_repeated_parses_are_identical(self):
        results = [_parse(self.SOURCE).to_dict() for _ in range(25)]
        for result in results[1:]:
            self.assertEqual(result, results[0])

    def test_source_order_is_preserved_not_sorted(self):
        """Source order is the only ordering that means anything for a file."""
        result = _parse(self.SOURCE)
        self.assertEqual(_kinds(result), ["create_clock", "set_input_delay", "set_output_delay"])
        self.assertEqual([c.provenance.line for c in result.constraints], [2, 3, 5])
        self.assertEqual([i.provenance.line for i in result.issues], [4])

    def test_object_order_within_a_command_does_not_change_the_result(self):
        forwards = _parse("set_input_delay -clock c 0.5 [get_ports {a b}]").to_dict()
        backwards = _parse("set_input_delay -clock c 0.5 [get_ports {b a}]").to_dict()
        self.assertEqual(forwards, backwards)

    def test_result_round_trips_through_serialisation(self):
        result = _parse(self.SOURCE)
        self.assertEqual(SdcParseResult.parse(result.to_dict()), result)


class NegativeCases(unittest.TestCase):
    """What the reader must refuse to do."""

    def test_random_prose_is_not_parsed_as_a_constraint(self):
        for text in ("hello world", "this is a note about the design",
                     "create_clock but with no period at all",
                     "0.500 create_clock"):
            with self.subTest(text=text):
                result = _parse(text)
                self.assertEqual(result.constraints, (),
                                 f"{text!r} was read as a constraint")

    def test_unsupported_commands_never_become_constraints(self):
        for command in ("set_false_path -from a -to b",
                        "set_multicycle_path -setup 2 -from a -to b",
                        "set_clock_uncertainty -setup 0.1 [get_clocks c]",
                        "set_units -time ns"):
            with self.subTest(command=command):
                result = _parse(command)
                self.assertEqual(result.constraints, ())
                self.assertEqual(_issue_kinds(result), ["unsupported"])

    def test_unsupported_and_malformed_are_distinguished(self):
        """`unsupported` is this reader's limit; `malformed` is the file's problem."""
        result = _parse("set_false_path -from a\ncreate_clock [get_ports c]")
        self.assertEqual(_issue_kinds(result), ["unsupported", "malformed"])
        self.assertEqual(_kinds(result), [])

    def test_malformed_commands_are_never_silently_ignored(self):
        result = _parse("create_clock -period 10 clk\ncreate_clock -name broken\nset_input_delay 0.5")
        self.assertEqual(len(result.constraints), 1)
        self.assertEqual(_issue_kinds(result), ["malformed", "malformed"])

    def test_ok_is_false_only_for_malformed(self):
        """Unsupported commands do not make a read fail; malformed ones do."""
        self.assertTrue(_parse("set_false_path -from a").ok,
                        "an unsupported command should not make the parse fail")
        self.assertFalse(_parse("create_clock clk").ok)

    def test_an_unterminated_group_is_reported_not_guessed(self):
        result = _parse("create_clock -period 10 [get_ports clk")
        self.assertEqual(result.constraints, ())
        self.assertEqual(_issue_kinds(result), ["malformed"])

    def test_issue_kind_vocabulary_is_closed(self):
        self.assertEqual(ISSUE_KINDS, ("unsupported", "malformed"))
        self.assertEqual(SUPPORTED_COMMANDS, ("create_clock", "set_input_delay", "set_output_delay"))

    def test_the_reader_makes_no_judgement_about_validity(self):
        """A constraint that references an undefined clock is READ, not flagged.

        Deciding that the clock should exist is VLSI-1B's question. A parser that answered it would be
        doing analysis, and the boundary between the two is what keeps this milestone honest.
        """
        result = _parse("set_input_delay -clock never_defined 0.5 [get_ports d]")
        self.assertEqual(len(result.constraints), 1)
        self.assertEqual(result.issues, ())
        self.assertEqual(result.constraints[0].clock, "never_defined")

    def test_a_duplicate_clock_is_read_twice_and_not_flagged(self):
        result = _parse("create_clock -name a -period 1 clk\ncreate_clock -name a -period 2 clk")
        self.assertEqual(len(result.constraints), 2)
        self.assertEqual(result.issues, (), "the reader judged a duplicate, which is analysis")


if __name__ == "__main__":
    unittest.main()