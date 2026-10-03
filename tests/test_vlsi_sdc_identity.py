"""VLSI-1D.1: semantic SDC finding identity.

Every test here is about one property: **a finding's identity must name the engineering condition, not
where the condition was observed.** That is what makes a later before/after comparison meaningful, and
getting it wrong does not fail loudly - a repair that leaves the defect in place would simply be
reported as resolved.

Three things are therefore pinned:

- the identity each REAL analyzer finding receives, derived by execution rather than by assumption about
  `FINDING_KINDS` - which is how the fact that `duplicate_constraint` IS emitted came to be known, the
  declared constant list being wider than what the analyzer produces;
- stability under the changes a repair causes: the line moves, the occurrence count changes, the
  conflicting periods converge;
- distinction between genuinely different conditions, so identity is not so coarse that everything
  collapses into one.
"""

from __future__ import annotations

import unittest

from debugagent.domains.vlsi.findings import FINDING_KINDS, VlsiFinding
from debugagent.domains.vlsi.identity import (
    IDENTITY_FIELDS_BY_KIND,
    canonical_identity,
    parse_identity,
    validate_identity,
)
from debugagent.domains.vlsi.provenance import Provenance, VlsiContractError
from debugagent.domains.vlsi.sdc_analyzer import analyze_sdc_constraints
from debugagent.domains.vlsi.sdc_parser import parse_sdc

CLOCK = "create_clock -name core_clk -period 10 [get_ports clk]\n"


def analyze(text: str, ref: str = "top.sdc"):
    """Run the REAL parser and analyzer. Nothing here is a hand-built finding."""
    return analyze_sdc_constraints(parse_sdc(text, ref=ref))


def identities(text: str, ref: str = "top.sdc") -> list[str]:
    return [finding.identity for finding in analyze(text, ref).findings]


class EmittedKinds(unittest.TestCase):
    """Which kinds the analyzer ACTUALLY emits, established by running it."""

    def test_all_three_clock_and_delay_kinds_are_reachable(self):
        emitted = set()
        emitted.update(identities(CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n"))
        emitted.update(identities(CLOCK + CLOCK.replace("period 10", "period 10")))
        emitted.update(identities(CLOCK + CLOCK.replace("period 10", "period 12")))
        kinds = {identity.split(":", 1)[0] for identity in emitted}
        self.assertEqual(kinds, {"missing_constraint", "duplicate_constraint",
                                 "conflicting_constraint"},
                         "the analyzer emits these three; the identity contract must cover all of them")

    def test_every_emitted_kind_has_an_identity(self):
        samples = [
            CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n",
            CLOCK + CLOCK,
            CLOCK + CLOCK.replace("period 10", "period 12"),
        ]
        for sample in samples:
            for finding in analyze(sample).findings:
                with self.subTest(kind=finding.kind):
                    self.assertTrue(finding.identity, "an emitted finding must carry an identity")
                    self.assertTrue(finding.identity.startswith(finding.kind + ":"))

    def test_a_kind_with_no_field_rule_falls_back_to_the_kind_alone(self):
        """Documented as a signal to treat such kinds as INCOMPLETE, never as resolved."""
        self.assertEqual(canonical_identity("parse_error", {"anything": "x"}), "parse_error")
        self.assertEqual(canonical_identity("analysis_unavailable", {}), "analysis_unavailable")
        self.assertNotIn("parse_error", IDENTITY_FIELDS_BY_KIND)

    def test_no_declared_kind_is_left_without_an_identity_rule_or_a_fallback(self):
        """Every kind must either have a rule or be covered by the fallback. Structural, not assumed."""
        for kind in FINDING_KINDS:
            with self.subTest(kind=kind):
                self.assertTrue(canonical_identity(kind, {"clock": "c"}),
                                "every declared kind must yield a non-empty identity")


class IdentityContent(unittest.TestCase):
    """The concrete identities the real analyzer produces."""

    def test_a_missing_clock_reference_names_clock_constraint_and_objects(self):
        finding = analyze(CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n").findings[0]
        self.assertEqual(finding.identity,
                         "missing_constraint:clock=pll_clk,constraint_kind=set_input_delay,objects=d")

    def test_input_and_output_delay_produce_different_identities(self):
        first = identities(CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n")
        second = identities(CLOCK + "set_output_delay -clock mm_clk 0.4 [get_ports q]\n")
        self.assertNotEqual(first, second,
                            "a different clock, constraint kind and object set is a different condition")

    def test_a_duplicate_clock_names_only_the_clock(self):
        finding = analyze(CLOCK + CLOCK).findings[0]
        self.assertEqual(finding.kind, "duplicate_constraint")
        self.assertEqual(finding.identity, "duplicate_constraint:clock=core_clk")

    def test_a_conflicting_clock_names_only_the_clock(self):
        finding = analyze(CLOCK + CLOCK.replace("period 10", "period 12")).findings[0]
        self.assertEqual(finding.kind, "conflicting_constraint")
        self.assertEqual(finding.identity, "conflicting_constraint:clock=core_clk")

    def test_duplicate_and_conflicting_on_one_clock_differ(self):
        """Same clock, different condition, therefore different identity."""
        duplicate = analyze(CLOCK + CLOCK).findings[0]
        conflicting = analyze(CLOCK + CLOCK.replace("period 10", "period 12")).findings[0]
        self.assertNotEqual(duplicate.identity, conflicting.identity)

    def test_identity_is_not_derived_from_the_message(self):
        """Two findings with different messages but identical facts share an identity."""
        details = (("clock", "clock", "core_clk"),)
        first = VlsiFinding(kind="conflicting_constraint", severity="critical",
                            message="one wording entirely", provenance=Provenance("file", "a.sdc"),
                            details=details)
        second = VlsiFinding(kind="conflicting_constraint", severity="warning",
                             message="a completely different sentence", provenance=Provenance("file", "b.sdc"),
                             details=details)
        self.assertNotEqual(first.message, second.message)
        self.assertEqual(first.identity, second.identity)


class StabilityUnderRepair(unittest.TestCase):
    """The changes a repair causes must not change the identity."""

    def test_the_same_missing_constraint_on_a_later_line_has_the_same_identity(self):
        """The core acceptance case: `top.sdc:2` and `top.sdc:7` are the SAME condition.

        The later sample repeats the clock definition, which the analyzer correctly also reports as a
        duplicate, so only the missing-clock identities are compared here.
        """
        early = [i for i in identities(CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n")
                 if i.startswith("missing_constraint")]
        late = [i for i in identities(
            CLOCK + "\n\n\n\n" + CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n")
            if i.startswith("missing_constraint")]
        self.assertEqual(early, late)
        self.assertEqual(early[0], "missing_constraint:clock=pll_clk,constraint_kind=set_input_delay,"
                                   "objects=d")

    def test_the_line_actually_moved(self):
        """Proves the previous test is testing line movement and not two identical inputs."""
        lines = []
        for sample in (CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n",
                       CLOCK + "\n\n\n\n" + CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n"):
            lines.append(next(f.provenance.line for f in analyze(sample).findings
                              if f.kind == "missing_constraint"))
        self.assertEqual(lines, [2, 7])

    def test_a_moved_conflicting_clock_keeps_its_identity(self):
        early = identities(CLOCK + CLOCK.replace("period 10", "period 12"))
        late = identities(CLOCK + "\n\n\n\n" + CLOCK + CLOCK.replace("period 10", "period 12"))
        self.assertEqual(early, late)

    def test_changed_conflicting_periods_keep_the_same_identity(self):
        """`periods` IS the thing being repaired, so it cannot be part of identity."""
        before = identities(CLOCK + CLOCK.replace("period 10", "period 12"))
        after = identities(CLOCK + CLOCK.replace("period 10", "period 10"))
        conflicting_before = [i for i in before if i.startswith("conflicting_constraint")]
        self.assertTrue(conflicting_before)
        self.assertEqual(conflicting_before[0], "conflicting_constraint:clock=core_clk")

    def test_fewer_occurrences_keep_the_same_identity(self):
        """Three contradictory definitions reduced to two is still the same broken clock."""
        three = identities(CLOCK + CLOCK.replace("period 10", "period 12")
                           + CLOCK.replace("period 10", "period 14"))
        two = identities(CLOCK + CLOCK.replace("period 10", "period 12"))
        self.assertEqual(three, two, "a partial repair must not look like a different condition")

    def test_object_order_does_not_change_the_identity(self):
        """The analyzer joins objects without sorting, and the parser leaves a trailing comma."""
        first = identities(CLOCK + "set_input_delay -clock p -max 2 [get_ports {b, a}]\n")
        second = identities(CLOCK + "set_input_delay -clock p -max 2 [get_ports {a, b}]\n")
        self.assertEqual(first, second,
                         "identity normalises an unordered set, so it cannot depend on the order")

    def test_identity_contains_no_line_number_or_location(self):
        for finding in analyze(CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n").findings:
            with self.subTest(kind=finding.kind):
                self.assertNotIn(f"line={finding.provenance.line}", finding.identity)
                self.assertNotIn("constraints", finding.identity)
                self.assertNotIn("top.sdc", finding.identity)


class Distinction(unittest.TestCase):
    """Different conditions must not collapse into one identity."""

    def test_renaming_a_clock_produces_a_different_identity(self):
        before = identities(CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n")
        after = identities(CLOCK + "set_input_delay -clock spi_clk 0.4 [get_ports d]\n")
        self.assertNotEqual(before, after)

    def test_two_different_conflicting_clocks_produce_two_identities(self):
        text = (CLOCK
                + CLOCK.replace("period 10", "period 12")
                + CLOCK.replace("core_clk", "spi_clk").replace("period 10", "period 10")
                + CLOCK.replace("core_clk", "spi_clk").replace("period 10", "period 20"))
        found = [i for i in identities(text) if i.startswith("conflicting_constraint")]
        self.assertEqual(len(found), 2)
        self.assertNotEqual(found[0], found[1])

    def test_two_missing_clocks_produce_two_identities(self):
        found = identities(CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n"
                           + "set_output_delay -clock mm_clk 0.4 [get_ports q]\n")
        self.assertEqual(len(found), 2)
        self.assertNotEqual(found[0], found[1])

    def test_a_different_object_set_is_a_different_condition(self):
        first = identities(CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n")
        second = identities(CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports e]\n")
        self.assertNotEqual(first, second)


class CanonicalForm(unittest.TestCase):
    """Serialisation must be deterministic and free of positional fields."""

    def test_keys_are_sorted_regardless_of_detail_order(self):
        forwards = canonical_identity("missing_constraint",
                                      {"clock": "c", "constraint_kind": "k", "objects": "o"})
        backwards = canonical_identity("missing_constraint",
                                       {"objects": "o", "constraint_kind": "k", "clock": "c"})
        self.assertEqual(forwards, backwards)
        self.assertEqual(forwards, "missing_constraint:clock=c,constraint_kind=k,objects=o")

    def test_every_field_rule_is_itself_alphabetical(self):
        """Pins the input to `sorted()`.

        All three tuples happen to be alphabetical already, which means removing the `sorted()` call in
        `canonical_identity` is an EQUIVALENT mutation - the tests cannot catch it, because no output
        would change. This test makes the property explicit instead: a future kind whose fields are not
        alphabetical would otherwise render in declaration order, and nothing would notice.
        """
        for kind, fields in IDENTITY_FIELDS_BY_KIND.items():
            with self.subTest(kind=kind):
                self.assertEqual(tuple(fields), tuple(sorted(fields)),
                                 f"{kind}'s identity fields must be listed alphabetically")
                self.assertEqual(len(set(fields)), len(fields), f"{kind} repeats a field")

    def test_a_three_way_object_set_renders_in_sorted_order(self):
        """Three elements is where set iteration order actually diverges from sorted.

        Two-element sets happen to iterate in sorted order in CPython, so a two-object case cannot
        distinguish `sorted()` from an unsorted join. Three does, and this is the assertion that makes
        the object normalisation real rather than incidental.

        The commas INSIDE the value are escaped, because a comma is what separates pairs - `a,b,c` must
        not read as three empty pairs. `parse_identity` round-trips it back to the original set.
        """
        identity = canonical_identity("missing_constraint", {"objects": "c,a,b"})
        self.assertEqual(identity, "missing_constraint:objects=a%2Cb%2Cc")
        self.assertNotEqual(identity, "missing_constraint:objects=c%2Ca%2Cb")
        _kind, pairs = parse_identity(identity)
        self.assertEqual(pairs, (("objects", "a,b,c"),),
                         "the escaped separators must decode back to one value")

    def test_a_single_object_needs_no_escaping(self):
        """The common case stays readable: one object has no separators inside it."""
        self.assertEqual(canonical_identity("missing_constraint", {"objects": "d"}),
                         "missing_constraint:objects=d")

    def test_identity_round_trips_through_parse(self):
        kind, pairs = parse_identity("missing_constraint:clock=c,constraint_kind=k,objects=o")
        self.assertEqual(kind, "missing_constraint")
        self.assertEqual(pairs, (("clock", "c"), ("constraint_kind", "k"), ("objects", "o")))

    def test_separators_inside_a_value_cannot_forge_a_field_boundary(self):
        """A clock name containing the separators must survive intact rather than split a pair."""
        escaped = canonical_identity("conflicting_constraint", {"clock": "a,b:c=d"})
        kind, pairs = parse_identity(escaped)
        self.assertEqual(kind, "conflicting_constraint")
        self.assertEqual(pairs, (("clock", "a,b:c=d"),))
        self.assertNotEqual(escaped, "conflicting_constraint:clock=a,b")

    def test_an_empty_object_set_is_kept_rather_than_dropped(self):
        """"no objects named" is part of the condition, and must not read as "not recorded".

        A `None` detail means the analysis could not determine the value; an empty string means it
        determined the value to be empty. Rendering the pair keeps that distinction, so
        `objects=` is correct output rather than a missing field.
        """
        self.assertEqual(canonical_identity("missing_constraint", {"clock": "c", "objects": ""}),
                         "missing_constraint:clock=c,objects=")
        self.assertEqual(canonical_identity("missing_constraint", {"clock": "c", "objects": None}),
                         "missing_constraint:clock=c")


class Validation(unittest.TestCase):
    """The parse boundary is where a record from outside is checked."""

    BASE = {"kind": "conflicting_constraint", "severity": "critical", "message": "m",
            "provenance": {"source_type": "file", "ref": "top.sdc", "line": 3},
            "details": [["clock", "core_clk"]]}

    def _errors_for(self, identity) -> list[str]:
        errors: list[str] = []
        validate_identity(identity, kind=self.BASE["kind"],
                          details={"clock": "core_clk"}, label="F", errors=errors)
        return errors

    def test_a_well_formed_identity_is_accepted(self):
        self.assertEqual(self._errors_for("conflicting_constraint:clock=core_clk"), [])

    def test_a_non_string_identity_is_rejected(self):
        self.assertTrue(self._errors_for(42))
        self.assertTrue(self._errors_for(""))

    def test_a_malformed_pair_is_rejected(self):
        for bad in ("conflicting_constraint:clock", "conflicting_constraint:=core_clk",
                    "conflicting_constraint:clock=a=extra"):
            with self.subTest(identity=bad):
                self.assertTrue(self._errors_for(bad), f"{bad!r} should be rejected")

    def test_positional_field_names_are_rejected(self):
        """An identity naming a line or a path is a location wearing an identity's clothes."""
        for bad in ("conflicting_constraint:line=3,clock=core_clk",
                    "conflicting_constraint:clock=core_clk,ref=top.sdc",
                    "conflicting_constraint:clock=core_clk,timestamp=now"):
            with self.subTest(identity=bad):
                self.assertTrue(self._errors_for(bad), f"{bad!r} must be rejected as positional")

    def test_unsorted_keys_are_rejected(self):
        """Isolated: the kind here MATCHES, so the sorting check is the only thing that can fail.

        The other cases in this class pair an unsorted payload with a mismatched kind, and a kind
        mismatch alone would satisfy `assertTrue(errors)` - so they would still pass with the sorting
        check removed. This one has to be clean apart from the ordering.
        """
        errors: list[str] = []
        validate_identity("conflicting_constraint:objects=o,clock=c",
                          kind="conflicting_constraint", details={"clock": "c"},
                          label="F", errors=errors)
        self.assertEqual(len(errors), 1, f"expected only the ordering fault, got {errors}")
        self.assertIn("sorted", errors[0])

    def test_a_duplicate_key_is_rejected(self):
        errors: list[str] = []
        validate_identity("conflicting_constraint:clock=a,clock=b",
                          kind="conflicting_constraint", details={"clock": "c"},
                          label="F", errors=errors)
        self.assertTrue(errors)
        self.assertIn("sorted", errors[0])

    def test_a_kind_mismatch_is_rejected(self):
        self.assertTrue(self._errors_for("missing_constraint:clock=core_clk"))

    def test_an_unknown_kind_is_rejected(self):
        self.assertTrue(self._errors_for("vibes_are_off:clock=core_clk"))

    def test_parse_rejects_a_malformed_identity(self):
        with self.assertRaises(VlsiContractError):
            VlsiFinding.parse({**self.BASE, "identity": "conflicting_constraint:line=3"})

    def test_parse_accepts_a_well_formed_identity(self):
        finding = VlsiFinding.parse({**self.BASE, "identity": "conflicting_constraint:clock=core_clk"})
        self.assertEqual(finding.identity, "conflicting_constraint:clock=core_clk")


class RoundTrip(unittest.TestCase):
    def test_identity_survives_serialisation(self):
        for sample in (CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n",
                       CLOCK + CLOCK,
                       CLOCK + CLOCK.replace("period 10", "period 12")):
            for finding in analyze(sample).findings:
                with self.subTest(kind=finding.kind):
                    self.assertEqual(VlsiFinding.parse(finding.to_dict()), finding)

    def test_a_finding_constructed_directly_still_carries_an_identity(self):
        finding = VlsiFinding(kind="conflicting_constraint", severity="critical", message="m",
                              provenance=Provenance("file", "a.sdc"), details=(("clock", "clock", "k"),))
        self.assertEqual(finding.identity, "conflicting_constraint:clock=k")

    def test_a_finding_with_no_details_still_carries_an_identity(self):
        finding = VlsiFinding(kind="parse_error", severity="warning", message="unreadable",
                              provenance=Provenance("file", "a.sdc"))
        self.assertEqual(finding.identity, "parse_error")

    def test_identity_is_deterministic_across_runs(self):
        sample = CLOCK + "set_input_delay -clock pll_clk 0.4 [get_ports d]\n"
        self.assertEqual(identities(sample), identities(sample))


if __name__ == "__main__":
    unittest.main()