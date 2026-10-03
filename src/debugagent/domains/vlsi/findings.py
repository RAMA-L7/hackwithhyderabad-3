"""VLSI findings: what deterministic analysis OBSERVES, and nothing it concludes.

This is the load-bearing contract of the foundation, so the boundary it holds is enforced by the shape
of the type rather than by a comment.

**A finding carries observations only.** The field set is fixed and closed:

    kind  severity  message  provenance  details

There is no field for a hypothesis, a root cause, a recommendation, a repair or a decision, and
`from_dict` REJECTS an unrecognised key instead of ignoring it. That is the mechanism: a caller cannot
smuggle `"recommendation": "add set_clock_uncertainty"` into a deterministic finding, because the extra
key is an error. Silently dropping unknown fields would let a proposal travel inside a finding
unnoticed, which is precisely the confusion the architecture exists to prevent.

The distinction the whole roadmap rests on, restated in code terms:

    observation  what deterministic analysis saw        VlsiFinding
    hypothesis   what the agent thinks it means         not here; agent reasoning
    proposal     a candidate change                     not here; agent reasoning
    decision     whether to accept it                   not here; the engineer

`severity` ranks how much an engineer's attention an observation deserves. It is NOT a judgement
about the design: `critical` means "look at this now", never "the design is broken". Nothing in this
module claims a design is correct, incorrect, or signoff-ready.

No timing number is interpreted here. Details are opaque typed values with no unit conversion, which
is VLSI-1's concern and the roadmap's explicit non-goal for this milestone.

Trust boundary: data only. No tool invocation, no LLM, no filesystem, no memory, no authorisation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .identity import canonical_identity, validate_identity
from .provenance import (Provenance, _errors_to_raise, _reject_unknown_keys,
                        _require_str)

#: What was observed. A vocabulary, not a class hierarchy: the foundation does not know the analyses
#: that will produce findings, and inventing an enum for them now would be guessing at VLSI-1.
#:
#: These name OBSERVABLE CONDITIONS, never causes. There is deliberately no `root_cause` kind.
FINDING_KINDS = (
    "missing_constraint",       # an object has no constraint where one is required
    "duplicate_constraint",     # the same constraint is defined more than once
    "conflicting_constraint",   # two constraints cannot both hold
    "unconstrained_object",     # an object has no clock / delay at all
    "out_of_scope_reference",   # a constraint names something outside the authorised tree
    "parse_error",              # an input could not be read
    "analysis_unavailable",     # evidence could not be produced here
)

#: How much an engineer's attention the observation deserves. Ranks OBSERVATIONS, not designs:
#: `critical` is "examine this now", never "the design is broken" and never "this is the root cause".
FINDING_SEVERITIES = ("info", "warning", "critical")

#: Severity as a rank, so findings can be ordered most-urgent-first without comparing strings.
SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}

#: Values a `details` entry may hold. Plain scalars only: no nested structures, so the record stays
#: hashable and its serialisation stays trivially comparable. `None` is allowed - it is how a detail
#: records a value the analysis could not determine, which is different from omitting the key.
DETAIL_VALUE_TYPES = (str, int, float, bool, type(None))


def _require_severity(value: Any, label: str, errors: list[str]) -> str:
    severity = _require_str(value, label, errors)
    if severity and severity not in FINDING_SEVERITIES:
        errors.append(f"{label}: '{severity}' is not one of {list(FINDING_SEVERITIES)}")
    return severity


def _require_details(value: Any, label: str, errors: list[str]) -> tuple[tuple[str, str, Any], ...]:
    """Normalise a details mapping to SORTED `(key, value)` pairs.

    Accepted as a mapping, or as the `[[key, value], ...]` form `to_dict` emits, so a finding
    survives a serialise -> validate round trip unchanged. Sorted, because two findings with the same
    details supplied in a different order must serialise identically.
    """
    if value is None:
        return ()
    if isinstance(value, dict):
        pairs = list(value.items())
    elif isinstance(value, (list, tuple)):
        pairs = []
        for index, pair in enumerate(value):
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                pairs.append((pair[0], pair[1]))
            else:
                errors.append(f"{label}[{index}]: expected a [key, value] pair")
    else:
        errors.append(f"{label}: expected object or array of pairs, got {type(value).__name__}")
        return ()

    details: list[tuple[str, str, Any]] = []
    for key, item in pairs:
        if not isinstance(key, str) or not key.strip():
            errors.append(f"{label}: detail keys must be non-empty strings")
            continue
        # `bool` is a subclass of `int`, so it is allowed deliberately - a flag IS a legitimate detail -
        # but nothing more complex is, so a detail can never smuggle in a nested object graph.
        if not isinstance(item, DETAIL_VALUE_TYPES):
            errors.append(f"{label}.{key}: expected a string, number, boolean or null, "
                          f"got {type(item).__name__}")
            continue
        details.append((key, key, item))

    keys = [entry[0] for entry in details]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        errors.append(f"{label}: duplicate detail keys {duplicates}")
    details.sort(key=lambda entry: entry[0])
    return tuple(details)


@dataclass(frozen=True)
class VlsiFinding:
    """One observation from deterministic VLSI analysis, with its provenance.

    Frozen and hashable. `details` holds sorted `(key, key, value)` triples internally - the key is
    stored twice so a non-scalar value can never be mistaken for a key - and serialises as a plain
    `[[key, value], ...]` array, which keeps the JSON stable and comparison-friendly.
    """

    kind: str
    severity: str
    message: str
    provenance: Provenance
    details: tuple[tuple[str, str, Any], ...] = ()
    identity: str = ""

    FIELDS = frozenset({"kind", "severity", "message", "provenance", "details", "identity"})

    @property
    def details_dict(self) -> dict:
        """The details as a plain mapping, for reading. Ordering is already fixed by construction."""
        return {key: value for key, _key, value in self.details}

    @property
    def location_known(self) -> bool:
        """Whether this observation knows where it came from. False is reportable, not a defect."""
        return self.provenance.location_known

    def __post_init__(self) -> None:
        """Derive identity when absent.

        Derived HERE rather than by the analyzer alone, so every `VlsiFinding` carries an identity
        however it was built - directly, by the analyzer, or by parsing. A type whose identity existed
        only on some construction paths would compare wrongly on the others, silently.

        A supplied identity is left alone: it was checked for FORM at the parse boundary (see
        `identity.validate_identity`), which is where a record arrives from outside. Production never
        supplies one that this would disagree with, because the analyzer derives it from these same
        details.
        """
        if not self.identity:
            object.__setattr__(self, "identity",
                               canonical_identity(self.kind, {k: v for k, _k, v in self.details}))

    def to_dict(self) -> dict:
        record = {
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "provenance": self.provenance.to_dict(),
            "details": [[key, value] for key, _key, value in self.details],
        }
        # Omitted when empty so a finding that predates identity - or one of a kind with no field rule
        # and no facts to distinguish - does not serialise an empty string that reads as a claim.
        if self.identity:
            record["identity"] = self.identity
        return record

    @classmethod
    def from_dict(cls, data: Any, label: str = "VlsiFinding", errors: list[str] | None = None) -> "VlsiFinding":
        errors = [] if errors is None else errors
        if not isinstance(data, dict):
            errors.append(f"{label}: expected object, got {type(data).__name__}")
            data = {}
        # The mechanism that keeps proposals out of findings. Called FIRST, so an unknown key is
        # reported even if the rest of the payload is also wrong.
        _reject_unknown_keys(data, cls.FIELDS, label, errors)

        kind = _require_str(data.get("kind"), f"{label}.kind", errors)
        if kind and kind not in FINDING_KINDS:
            errors.append(f"{label}.kind: '{kind}' is not one of {list(FINDING_KINDS)}")

        raw_provenance = data.get("provenance")
        provenance = Provenance.from_dict(raw_provenance, f"{label}.provenance", errors)
        details = _require_details(data.get("details"), f"{label}.details", errors)
        detail_map = {key: value for key, _key, value in details}

        # Identity is derived from THIS finding's own details, so there is exactly one interpretation of
        # what a finding is. A caller may supply one - the analyzer always does - and it is checked
        # against the derived value, which is what stops an identity being attached that the details do
        # not support.
        raw_identity = data.get("identity")
        if raw_identity is None:
            identity = ""
        else:
            before = len(errors)
            identity = validate_identity(raw_identity, kind=kind, details=detail_map,
                                         label=label, errors=errors)
            if len(errors) != before:
                # Already reported above; let `__post_init__` derive silently so one fault is reported
                # once, in the error list, rather than as an exception escaping mid-parse.
                identity = ""

        return cls(
            kind=kind,
            severity=_require_severity(data.get("severity"), f"{label}.severity", errors),
            message=_require_str(data.get("message"), f"{label}.message", errors),
            provenance=provenance,
            details=details,
            identity=identity,
        )

    @classmethod
    def parse(cls, data: Any, label: str = "VlsiFinding") -> "VlsiFinding":
        errors: list[str] = []
        value = cls.from_dict(data, label, errors)
        _errors_to_raise(errors, label)
        return value


def finding_sort_key(finding: VlsiFinding) -> tuple:
    """The total order for reporting findings.

    Most severe first, then by kind, then by provenance - so a report is grouped by what the finding is
    about before it is grouped by where it was found - then by message, which is the final tiebreak so
    that no two findings can compare equal and leave the order to the caller's input sequence.

    `line` is placed before `message` and None sorts first, so an unknown location groups with the
    other unknown-location findings instead of scattering between known ones.
    """
    return (
        SEVERITY_RANK[finding.severity],
        finding.kind,
        finding.provenance.source_type,
        finding.provenance.ref,
        finding.provenance.line if finding.provenance.line is not None else -1,
        finding.message,
    )


def sort_findings(findings: tuple[VlsiFinding, ...] | list[VlsiFinding]) -> tuple[VlsiFinding, ...]:
    """Findings in the canonical report order. Stable, total, and independent of input order."""
    return tuple(sorted(findings, key=finding_sort_key))