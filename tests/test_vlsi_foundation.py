"""VLSI-Foundation: the domain contracts.

`docs/architecture/vlsi-engineering-roadmap.md` Part III. This milestone is contracts only - no
parser, no analyser, no tool - so the tests are about properties a contract can be held to rather than
about behaviour:

- **identity and type** - what a VLSI artifact is, and that it is a different thing from an
  observation about it;
- **provenance** - including the case the roadmap calls out hardest: an unknown location stays
  explicitly unknown rather than being invented;
- **deterministic serialisation** - the same input produces byte-identical output, repeatedly, in a
  fresh interpreter, and independent of the order collections were supplied in;
- **the observation/proposal boundary** - enforced structurally, by a closed field set that rejects
  unknown keys, not by a convention;
- **the trust boundary** - asserted by scanning this package's own imports.

No network, no clock, no randomness, no LLM, no filesystem. Every test is a pure function of its
inputs, which is itself part of what is being asserted.
"""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
import unittest
from pathlib import Path

from debugagent.agents.tasks import Artifact as TaskArtifact
from debugagent.domains.vlsi import (
    ARTIFACT_TYPES,
    CONSTRAINT_KINDS,
    FINDING_KINDS,
    FINDING_SEVERITIES,
    PROVENANCE_SOURCE_TYPES,
    CreateClock,
    InputDelay,
    OutputDelay,
    Provenance,
    VlsiArtifact,
    VlsiContractError,
    VlsiFinding,
    artifact_identity,
    constraint_from_dict,
    sort_constraints,
    sort_findings,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
VLSI_DIR = REPO_ROOT / "src" / "debugagent" / "domains" / "vlsi"


def _clock(**overrides) -> CreateClock:
    fields = {"name": "app_clk", "period": "2.000", "unit": "ns",
              "objects": ("clk_a", "clk_b"),
              "provenance": Provenance("artifact", "constraints/top.sdc", line=14)}
    fields.update(overrides)
    return CreateClock(**fields)


def _finding(**overrides) -> VlsiFinding:
    fields = {
        "kind": "missing_constraint",
        "severity": "warning",
        "message": "app_clk has create_clock but no set_clock_uncertainty",
        "provenance": Provenance("artifact", "constraints/top.sdc", line=14),
        "details": (("clock", "clock", "app_clk"), ("tool", "tool", "internal")),
    }
    fields.update(overrides)
    return VlsiFinding(**fields)


class ProvenanceContract(unittest.TestCase):
    """Source, location, or an honest statement that the location is unknown."""

    def test_provenance_names_a_source_and_a_location(self):
        provenance = Provenance(source_type="file", ref="reports/setup_ss.rpt", line=112)
        self.assertEqual(provenance.source_type, "file")
        self.assertEqual(provenance.ref, "reports/setup_ss.rpt")
        self.assertEqual(provenance.line, 112)
        self.assertTrue(provenance.location_known)

    def test_unknown_location_stays_unknown_and_is_not_invented(self):
        """The case the roadmap calls out: an absent location must not become a plausible one."""
        provenance = Provenance(source_type="derived", ref="multiple", line=None)
        self.assertIsNone(provenance.line)
        self.assertFalse(provenance.location_known)
        self.assertEqual(provenance.to_dict()["line"], None)
        # Explicitly present in the serialised form, so "unknown" is not inferred from a missing key.
        self.assertIn("line", provenance.to_dict())

    def test_zero_and_negative_lines_are_rejected(self):
        """A placeholder line number would look like evidence and could be checked against the wrong place."""
        for bad in (0, -1, -112):
            with self.subTest(line=bad):
                with self.assertRaises(VlsiContractError):
                    Provenance.parse({"source_type": "file", "ref": "a.sdc", "line": bad})

    def test_a_boolean_is_not_a_line_number(self):
        with self.assertRaises(VlsiContractError):
            Provenance.parse({"source_type": "file", "ref": "a.sdc", "line": True})

    def test_source_type_is_from_a_closed_vocabulary(self):
        for source in PROVENANCE_SOURCE_TYPES:
            with self.subTest(source=source):
                self.assertEqual(Provenance.parse(
                    {"source_type": source, "ref": "x", "line": None}).source_type, source)
        with self.assertRaises(VlsiContractError):
            Provenance.parse({"source_type": "gossip", "ref": "x", "line": None})

    def test_blank_reference_is_rejected(self):
        with self.assertRaises(VlsiContractError):
            Provenance.parse({"source_type": "file", "ref": "   "})

    def test_derived_provenance_is_named_not_nullable(self):
        provenance = Provenance.derived()
        self.assertEqual(provenance.source_type, "derived")
        self.assertIsNone(provenance.line)
        self.assertEqual(provenance.to_dict(), {"source_type": "derived", "ref": "multiple", "line": None})

    def test_provenance_round_trips_through_serialisation(self):
        for provenance in (Provenance("file", "a/b.sdc", 7), Provenance.derived(), Provenance("tool_run", "r1")):
            with self.subTest(provenance=provenance.to_dict()):
                self.assertEqual(Provenance.parse(provenance.to_dict()), provenance)

    def test_provenance_rejects_unknown_fields(self):
        with self.assertRaises(VlsiContractError):
            Provenance.parse({"source_type": "file", "ref": "a.sdc", "line": 1, "confidence": "high"})

    def test_provenance_is_hashable_and_comparable(self):
        first = Provenance("file", "a.sdc", 1)
        second = Provenance("file", "a.sdc", 1)
        self.assertEqual(first, second)
        self.assertEqual(len({first, second}), 1, "equal provenance did not de-duplicate in a set")


class ArtifactContract(unittest.TestCase):
    """Identity, category, location - and separateness from an observation."""

    def test_artifact_identity_is_kind_and_ref_with_no_synthetic_id(self):
        artifact = VlsiArtifact(kind="sdc", ref="constraints/top.sdc")
        self.assertEqual(artifact.identity, ("sdc", "constraints/top.sdc"))
        # No id field exists, so identity cannot be a random or clock-derived value.
        self.assertEqual(set(artifact.to_dict()), {"kind", "ref"})

    def test_every_declared_artifact_type_is_representable(self):
        for kind in ARTIFACT_TYPES:
            with self.subTest(kind=kind):
                artifact = VlsiArtifact.parse({"kind": kind, "ref": "inputs/x"})
                self.assertEqual(artifact.kind, kind)

    def test_unknown_artifact_type_is_rejected(self):
        with self.assertRaises(VlsiContractError):
            VlsiArtifact.parse({"kind": "waveform", "ref": "inputs/x"})

    def test_artifact_provenance_defaults_to_unknown_location(self):
        artifact = VlsiArtifact(kind="sdc", ref="constraints/top.sdc")
        self.assertIsNone(artifact.provenance().line)
        self.assertFalse(artifact.provenance().location_known)
        self.assertEqual(artifact.provenance(line=3).line, 3)

    def test_artifact_carries_no_observation_fields(self):
        """An artifact is an INPUT. Severity, message and provenance belong to a finding."""
        self.assertEqual(set(VlsiArtifact.FIELDS), {"kind", "ref"})
        self.assertFalse({"severity", "message", "provenance", "details", "finding"} & set(VlsiArtifact.FIELDS))
        # And a finding's payload is not a valid artifact.
        with self.assertRaises(VlsiContractError):
            VlsiArtifact.parse(_finding().to_dict())

    def test_an_artifact_becomes_worker_context_through_the_existing_seam(self):
        """The bridge that makes this fit the current architecture rather than run beside it."""
        artifact = VlsiArtifact(kind="sdc", ref="constraints/top.sdc")
        task_artifact = artifact.as_task_artifact("create_clock -period 2.0")
        self.assertIsInstance(task_artifact, TaskArtifact)
        self.assertEqual(task_artifact.kind, "code", "an authored SDC file is task context of kind code")
        self.assertEqual(task_artifact.source, "code:constraints/top.sdc")
        # Machine output is `log`, keeping the existing code/log provenance distinction intact.
        self.assertEqual(VlsiArtifact(kind="timing_report", ref="r.rpt").task_artifact_kind, "log")

    def test_artifact_identity_of_a_set_is_order_independent(self):
        first = (VlsiArtifact("sdc", "b.sdc"), VlsiArtifact("netlist", "a.v"))
        second = tuple(reversed(first))
        self.assertEqual(artifact_identity(first), artifact_identity(second))


class FindingContract(unittest.TestCase):
    """What deterministic analysis observed."""

    def test_finding_carries_kind_severity_message_provenance_and_details(self):
        finding = _finding()
        self.assertEqual(finding.kind, "missing_constraint")
        self.assertEqual(finding.severity, "warning")
        self.assertIn("no set_clock_uncertainty", finding.message)
        self.assertEqual(finding.provenance.line, 14)
        self.assertEqual(finding.details_dict, {"clock": "app_clk", "tool": "internal"})

    def test_field_set_is_closed_and_carries_no_proposal_semantics(self):
        """The structural guarantee, pinned as a field set.

        If someone later adds `recommendation` or `hypothesis`, this test fails. That is the point: the
        boundary should be enforced by the type, and this is the test that enforces the type.
        """
        self.assertEqual(set(VlsiFinding.FIELDS),
                         {"kind", "severity", "message", "provenance", "details"})
        forbidden = {"hypothesis", "recommendation", "root_cause", "decision", "proposal",
                     "repair", "conclusion", "resolution", "approved"}
        self.assertEqual(set(VlsiFinding.FIELDS) & forbidden, set())
        self.assertEqual(forbidden & set(_finding().to_dict()), set(),
                         "a proposal-like key leaked into the serialised finding")

    def test_a_proposal_cannot_be_smuggled_into_a_finding(self):
        """Unknown keys are REJECTED, not ignored - so a proposal cannot travel inside a finding."""
        payload = _finding().to_dict()
        for smuggled in ("hypothesis", "recommendation", "root_cause", "decision"):
            with self.subTest(key=smuggled):
                with self.assertRaises(VlsiContractError) as caught:
                    VlsiFinding.parse({**payload, smuggled: "add set_clock_uncertainty"})
                self.assertIn(smuggled, str(caught.exception))

    def test_no_finding_kind_names_a_cause(self):
        """Kinds name observable conditions. A `root_cause` kind would smuggle a conclusion in."""
        for kind in FINDING_KINDS:
            with self.subTest(kind=kind):
                self.assertFalse(any(word in kind for word in
                                     ("cause", "root", "fix", "recommend", "should")), kind)

    def test_severity_ranks_observations_not_designs(self):
        """Pinned exactly, because widening the vocabulary is how a verdict would slip in.

        Round-tripping each declared value is not enough: a new value could be added to
        `FINDING_SEVERITIES` and every round-trip test would still pass.
        """
        self.assertEqual(FINDING_SEVERITIES, ("info", "warning", "critical"))
        for severity in FINDING_SEVERITIES:
            with self.subTest(severity=severity):
                self.assertEqual(_finding(severity=severity).severity, severity)
        # No value in the vocabulary may name a verdict about the design.
        for severity in FINDING_SEVERITIES:
            self.assertFalse(any(word in severity for word in
                                 ("cause", "root", "broken", "fail", "fail_to", "signoff", "guilty")),
                             f"severity {severity!r} names a verdict rather than a rank")
        with self.assertRaises(VlsiContractError):
            _finding(severity="catastrophic").to_dict() and VlsiFinding.parse(
                {**_finding().to_dict(), "severity": "catastrophic"})

    def test_details_are_sorted_and_scalar_only(self):
        finding = VlsiFinding.parse({**_finding().to_dict(),
                                     "details": [["z", "last"], ["a", "first"]]})
        self.assertEqual([entry[0] for entry in finding.details], ["a", "z"])
        self.assertEqual(finding.to_dict()["details"], [["a", "first"], ["z", "last"]])

    def test_supplying_details_in_a_different_order_serialises_identically(self):
        first = VlsiFinding.parse({**_finding().to_dict(), "details": [["a", "1"], ["b", "2"]]})
        second = VlsiFinding.parse({**_finding().to_dict(), "details": [["b", "2"], ["a", "1"]]})
        self.assertEqual(first, second)
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_nested_structures_are_rejected_in_details(self):
        """A detail cannot smuggle in an object graph."""
        with self.assertRaises(VlsiContractError):
            VlsiFinding.parse({**_finding().to_dict(), "details": [["paths", ["a", "b"]]]})

    def test_duplicate_detail_keys_are_rejected(self):
        with self.assertRaises(VlsiContractError):
            VlsiFinding.parse({**_finding().to_dict(), "details": [["a", "1"], ["a", "2"]]})

    def test_a_null_detail_is_recorded_not_dropped(self):
        finding = VlsiFinding.parse({**_finding().to_dict(), "details": [["corner", None]]})
        self.assertEqual(finding.details_dict, {"corner": None})
        self.assertEqual(finding.to_dict()["details"], [["corner", None]])

    def test_finding_round_trips_through_serialisation(self):
        for finding in (_finding(),
                        _finding(details=()),
                        _finding(provenance=Provenance.derived()),
                        _finding(kind="parse_error", severity="critical", message="unreadable")):
            with self.subTest(finding=finding.to_dict()):
                self.assertEqual(VlsiFinding.parse(finding.to_dict()), finding)

    def test_finding_with_unknown_location_reports_it(self):
        finding = _finding(provenance=Provenance("derived", "multiple", None))
        self.assertFalse(finding.location_known)
        self.assertIsNone(finding.to_dict()["provenance"]["line"])

    def test_finding_is_hashable(self):
        self.assertEqual(len({_finding(), _finding()}), 1)

    def test_missing_required_fields_are_rejected(self):
        for field in ("kind", "severity", "message", "provenance"):
            with self.subTest(field=field):
                payload = _finding().to_dict()
                del payload[field]
                with self.assertRaises(VlsiContractError):
                    VlsiFinding.parse(payload)


class FindingOrdering(unittest.TestCase):
    """Deterministic, total, and independent of input order."""

    def _mixed(self) -> tuple[VlsiFinding, ...]:
        return (
            _finding(severity="info", message="a", provenance=Provenance("file", "z.sdc", 9)),
            _finding(severity="critical", message="b", provenance=Provenance("file", "a.sdc", 2)),
            _finding(severity="warning", message="c", provenance=Provenance.derived()),
            _finding(severity="critical", message="a", provenance=Provenance("file", "a.sdc", 2)),
        )

    def test_sorting_is_independent_of_input_order(self):
        findings = self._mixed()
        forwards = [f.to_dict() for f in sort_findings(findings)]
        backwards = [f.to_dict() for f in sort_findings(tuple(reversed(findings)))]
        self.assertEqual(forwards, backwards)

    def test_most_severe_first(self):
        severities = [f.severity for f in sort_findings(self._mixed())]
        self.assertEqual(severities[0], "critical")
        self.assertEqual(severities[-1], "info")

    def test_unknown_locations_group_together(self):
        """A finding with no location must not scatter between known ones."""
        derived = _finding(severity="warning", message="d", provenance=Provenance.derived())
        unknown_line = _finding(severity="warning", message="e",
                                provenance=Provenance("tool_run", "run1", None))
        ordered = sort_findings((derived, unknown_line))
        self.assertEqual([f.provenance.line for f in ordered], [None, None])

    def test_no_two_distinct_findings_compare_equal(self):
        keys = [str(f.to_dict()) for f in self._mixed()]
        self.assertEqual(len(set(keys)), len(keys))


class SdcConstraintContract(unittest.TestCase):
    """Three typed constraint families, values preserved exactly as written."""

    def test_create_clock_keeps_the_literal_period_and_unit(self):
        clock = _clock()
        self.assertEqual(clock.period, "2.000", "the period was converted rather than preserved")
        self.assertEqual(clock.unit, "ns")
        self.assertEqual(clock.KIND, "create_clock")
        self.assertEqual(clock.to_dict()["kind"], "create_clock")

    def test_absent_unit_stays_absent_rather_than_defaulted(self):
        """SDC lets a command inherit a unit. Filling one in would invent a fact about the file."""
        clock = CreateClock(name="app_clk", period="2.000")
        self.assertIsNone(clock.unit)
        self.assertIn("unit", clock.to_dict())
        self.assertIsNone(clock.to_dict()["unit"])

    def test_input_and_output_delay_are_distinct_types(self):
        self.assertEqual(InputDelay.KIND, "set_input_delay")
        self.assertEqual(OutputDelay.KIND, "set_output_delay")
        # A delay is not interchangeable with a clock, and the types make a mistake loud.
        self.assertNotIsInstance(InputDelay(clock="c", value="1.0"), CreateClock)
        self.assertEqual(set(CONSTRAINT_KINDS),
                         {"create_clock", "set_input_delay", "set_output_delay"})

    def test_delay_qualifier_is_validated_and_may_be_absent(self):
        self.assertEqual(InputDelay(clock="c", value="1.0", qualifier="max").qualifier, "max")
        self.assertIsNone(InputDelay(clock="c", value="1.0").qualifier)
        with self.assertRaises(VlsiContractError):
            InputDelay.parse({**InputDelay(clock="c", value="1.0").to_dict(), "qualifier": "typical"})

    def test_objects_are_sorted_and_deduplicated(self):
        """Normalisation happens at the parse boundary, like every other contract here."""
        shuffled = _clock(objects=("clk_b", "clk_a", "clk_b")).to_dict()
        parsed = CreateClock.parse(shuffled)
        self.assertEqual(parsed.objects, ("clk_a", "clk_b"))
        self.assertEqual(parsed.to_dict(), _clock().to_dict(),
                         "object order changed the serialised representation")

    def test_validation_happens_at_the_parse_boundary(self):
        """Stated rather than assumed, because direct construction does not validate.

        This matches `Artifact`, `TaskSpec` and `WorkerContext`: constructing a contract value directly
        trusts the caller, and `from_dict`/`parse` is where a payload from outside is checked. Adding
        `__post_init__` validation to only these types would make them behave differently from every
        other contract in the codebase, which is its own inconsistency - so the convention is kept and
        pinned by this test.
        """
        unvalidated = InputDelay(clock="c", value="1.0", qualifier="not-a-qualifier")
        self.assertEqual(unvalidated.qualifier, "not-a-qualifier", "construction validated unexpectedly")
        with self.assertRaises(VlsiContractError):
            InputDelay.parse(unvalidated.to_dict())

    def test_provenance_is_carried_and_serialised(self):
        payload = _clock().to_dict()
        self.assertEqual(payload["provenance"], {"source_type": "artifact",
                                                 "ref": "constraints/top.sdc", "line": 14})
        self.assertEqual(CreateClock.parse(payload), _clock())

    def test_constraint_without_provenance_serialises_it_as_null(self):
        payload = CreateClock(name="c", period="1.0").to_dict()
        self.assertIn("provenance", payload)
        self.assertIsNone(payload["provenance"])
        self.assertEqual(CreateClock.parse(payload), CreateClock(name="c", period="1.0"))

    def test_every_constraint_round_trips(self):
        constraints = (
            _clock(),
            InputDelay(clock="app_clk", value="0.500", unit="ns", qualifier="max",
                       objects=("d[0]",), provenance=Provenance("artifact", "c/top.sdc", 3)),
            OutputDelay(clock="app_clk", value="0.500", unit="ns"),
            CreateClock(name="c", period="1.0"),
        )
        for constraint in constraints:
            with self.subTest(kind=constraint.KIND):
                self.assertEqual(type(constraint).parse(constraint.to_dict()), constraint)

    def test_dispatch_by_kind_selects_the_right_type(self):
        parsed = constraint_from_dict(_clock().to_dict())
        self.assertIsInstance(parsed, CreateClock)
        self.assertIsInstance(constraint_from_dict(InputDelay(clock="c", value="1").to_dict()), InputDelay)
        self.assertIsInstance(constraint_from_dict(OutputDelay(clock="c", value="1").to_dict()), OutputDelay)

    def test_an_unsupported_constraint_kind_is_an_error_not_silently_ignored(self):
        payload = CreateClock(name="c", period="1.0").to_dict()
        payload["kind"] = "set_false_path"
        errors: list[str] = []
        self.assertIsNone(constraint_from_dict(payload, "constraint", errors),
                          "an unsupported command produced a constraint instead of an error")
        self.assertTrue(any("set_false_path" in message for message in errors), errors)

    def test_a_payload_cannot_claim_a_command_it_is_not(self):
        """The discriminator is validated, so a payload cannot be read as the wrong command.

        The case that needs the check specifically: every field is valid for the type being used, and
        only the `kind` disagrees. A cross-type payload with foreign fields is already rejected by the
        field check, so it would pass this test whether or not the discriminator were validated.
        """
        lying = {"kind": "set_input_delay", "name": "app_clk", "period": "2.000"}
        with self.assertRaises(VlsiContractError) as caught:
            CreateClock.parse(lying)
        self.assertIn("does not match", str(caught.exception),
                      "the rejection came from somewhere other than the discriminator")

    def test_an_absent_discriminator_is_accepted(self):
        """A caller that already knows the type may omit it."""
        payload = CreateClock(name="app_clk", period="2.000").to_dict()
        payload.pop("kind")
        self.assertEqual(CreateClock.parse(payload), CreateClock(name="app_clk", period="2.000"))

    def test_constraint_ordering_is_total_and_order_independent(self):
        constraints = (_clock(), InputDelay(clock="app_clk", value="0.5"),
                       CreateClock(name="aux_clk", period="10.0"))
        forwards = [c.to_dict() for c in sort_constraints(constraints)]
        backwards = [c.to_dict() for c in sort_constraints(tuple(reversed(constraints)))]
        self.assertEqual(forwards, backwards)
        self.assertEqual(forwards[0]["kind"], "create_clock")


class DeterminismAndTrustBoundary(unittest.TestCase):
    """The properties that make this foundation usable by the rest of the architecture."""

    def test_repeated_serialisation_is_byte_identical(self):
        """No clock, no randomness, no set iteration order leaking into output."""
        finding, clock, artifact = _finding(), _clock(), VlsiArtifact("sdc", "c/top.sdc")
        for _ in range(25):
            self.assertEqual(finding.to_dict(), finding.to_dict())
            self.assertEqual(clock.to_dict(), clock.to_dict())
            self.assertEqual(artifact.to_dict(), artifact.to_dict())

    def test_serialisation_is_identical_in_a_fresh_interpreter(self):
        """A different hash seed must not change a single byte of output.

        Both sides go through `parse`, because normalisation (sorting details, sorting objects) is a
        parse-boundary property - see `test_validation_happens_at_the_parse_boundary`. Comparing a
        directly-constructed value against a parsed one would compare two different paths and prove
        nothing about either.
        """
        payload = {
            "finding": {"kind": "missing_constraint", "severity": "warning", "message": "m",
                        "provenance": {"source_type": "artifact", "ref": "c.sdc", "line": 4},
                        "details": [["b", "2"], ["a", "1"]]},
            "clock": {"kind": "create_clock", "name": "clk", "period": "2.0", "unit": "ns",
                      "objects": ["z", "a"]},
        }
        script = (
            "import sys; sys.path[:0] = ['src']\n"
            "from debugagent.domains.vlsi import VlsiFinding, CreateClock\n"
            f"payload = {payload!r}\n"
            "print('OUT:' + repr([VlsiFinding.parse(payload['finding']).to_dict(),\n"
            "                      CreateClock.parse(payload['clock']).to_dict()]))\n")
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                              cwd=str(REPO_ROOT), encoding="utf-8", errors="replace")
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("OUT:")), None)
        self.assertIsNotNone(line, f"no output: {proc.stderr[-400:]}")
        finding_dict, clock_dict = eval(line[len("OUT:"):])
        self.assertEqual(finding_dict, VlsiFinding.parse(payload["finding"]).to_dict())
        self.assertEqual(clock_dict, CreateClock.parse(payload["clock"]).to_dict())
        # And the round trip is stable: re-parsing the output reproduces it byte for byte.
        self.assertEqual(VlsiFinding.parse(finding_dict).to_dict(), finding_dict)
        self.assertEqual(CreateClock.parse(clock_dict).to_dict(), clock_dict)

    def test_package_imports_nothing_that_could_act(self):
        """The trust boundary, asserted from this package's own source.

        Checked by scanning imports rather than by reading the docstring, so the prose cannot drift
        away from the code.
        """
        allowed_first_party = {"debugagent.agents.tasks"}
        for module_path in sorted(VLSI_DIR.glob("*.py")):
            tree = ast.parse(module_path.read_text(encoding="utf-8"))
            imported: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
            for name in sorted(imported):
                with self.subTest(module=module_path.name, imported=name):
                    self.assertNotIn("subprocess", name)
                    self.assertNotIn("socket", name)
                    self.assertNotIn("urllib", name)
                    self.assertNotIn("random", name)
                    self.assertNotIn("time", name)
                    self.assertNotIn("uuid", name)
                    if name.startswith("debugagent"):
                        self.assertTrue(
                            name in allowed_first_party or name.startswith("debugagent.domains"),
                            f"{name} is outside the domain contract boundary")
                    self.assertFalse(name.startswith("debugagent.memory"), "no memory access")
                    self.assertFalse(name.startswith("debugagent.pipeline"), "no evidence access")
                    self.assertFalse(name.startswith("debugagent.agents.coordinator"), "no dispatch")
                    self.assertFalse(name.startswith("debugagent.agents.registry"), "no authorisation")

    def test_no_contract_type_can_write_or_apply_anything(self):
        """Nothing in the foundation exposes a mutating or executing operation."""
        for name in ("VlsiArtifact", "VlsiFinding", "Provenance", "CreateClock", "InputDelay",
                     "OutputDelay"):
            with self.subTest(type=name):
                members = {member for member in dir(getattr(importlib.import_module(
                    "debugagent.domains.vlsi"), name)) if not member.startswith("_")}
                self.assertEqual(members & {"write", "apply", "run", "execute", "retain",
                                            "authorize", "delete", "commit"}, set())

    def test_frozen_contracts_cannot_be_mutated_after_construction(self):
        finding, clock = _finding(), _clock()
        for target, field, value in ((finding, "severity", "info"), (clock, "period", "1.0")):
            with self.subTest(type=type(target).__name__):
                with self.assertRaises(Exception):
                    setattr(target, field, value)

    def test_contract_errors_are_value_errors(self):
        """Callers already written to catch a bad contract value keep working."""
        self.assertTrue(issubclass(VlsiContractError, ValueError))

    def test_foundation_defines_no_parsing_or_analysis(self):
        """No parser, analyser, engine or wrapper has leaked into this milestone."""
        public = set(importlib.import_module("debugagent.domains.vlsi").__all__)
        for forbidden in ("parse_sdc", "analyse", "analyze", "Analyzer", "Parser", "OpenSTA",
                          "run_tool", "slack", "repair", "unit_convert"):
            self.assertNotIn(forbidden, public)
        self.assertNotIn("parse", public, "no parser entry point is exported in this milestone")


if __name__ == "__main__":
    unittest.main()