"""Typed SDC constraints: the first constraint families VLSI-1 needs, as data.

Scope is deliberately three commands - `create_clock`, `set_input_delay`, `set_output_delay` - because
those are the incremental order the roadmap commits to (section 4.2), and nothing beyond them.

**This is a representation, not a language and not an analyser.** There is no parser, no tokenizer, no
grammar and no evaluation here. A constraint object records what a `.sdc` file *said*, with its
provenance, so that VLSI-1 has something typed to read into and something to compare a proposed change
against. Building the parser is VLSI-1 work and building the repair is later still.

**Values stay as written.** `period`, `value` and `unit` are the literal source tokens, unconverted.
`create_clock -period 10.0 [ns]` is represented as period `"10.0"` and unit `"ns"`, never as a number
in some canonical unit. The roadmap requires a unit to travel with a quantity, and unit conversion is
an explicit non-goal for this milestone: converting now would mean choosing a canonical unit, and every
later unit choice would then be a compatibility problem.

Trust boundary: data only. These objects cannot be read from a file, evaluated, or applied.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .provenance import Provenance, _errors_to_raise, _reject_unknown_keys, _require_str

#: The constraint commands represented. Only the first three incremental steps; the rest of SDC
#: (uncertainty, false paths, multicycle, generated clocks) is left for VLSI-1 and later.
CONSTRAINT_KINDS = ("create_clock", "set_input_delay", "set_output_delay")

#: SDC delay qualifiers, when the command states one. `None` means the command did not say, which is
#: different from saying `-max`: an absent qualifier is an observation about the source.
DELTA_QUALIFIERS = ("max", "min")


def _require_optional_str(value: Any, label: str, errors: list[str]) -> str | None:
    """An optional source token. Absent stays `None` and serialises as `null`.

    Empty and whitespace-only strings are rejected rather than treated as absent, because a blank
    qualifier in a file is a fact about the file.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        errors.append(f"{label}: expected string or null, got {type(value).__name__}")
        return None
    if not value.strip():
        errors.append(f"{label}: must not be empty when present")
        return None
    return value


def _require_optional_qualifier(value: Any, label: str, errors: list[str]) -> str | None:
    qualifier = _require_optional_str(value, label, errors)
    if qualifier and qualifier not in DELTA_QUALIFIERS:
        errors.append(f"{label}: '{qualifier}' is not one of {list(DELTA_QUALIFIERS)}")
    return qualifier


def _require_matching_kind(data: dict, expected: str, label: str, errors: list[str]) -> None:
    """Validate the `kind` discriminator that `to_dict` emits.

    `kind` is not a payload field - it is how `constraint_from_dict` chooses a type - so it is accepted
    but never stored. Validating it here means a payload claiming one command cannot be read as
    another: `CreateClock.from_dict({..., "kind": "set_input_delay"})` is an error rather than a
    constraint that quietly says something else. Absent is accepted, so a payload may also be built
    without the discriminator when the caller already knows the type.
    """
    supplied = data.get("kind")
    if supplied is None:
        return
    if not isinstance(supplied, str):
        errors.append(f"{label}.kind: expected string or null, got {type(supplied).__name__}")
    elif supplied != expected:
        errors.append(f"{label}.kind: '{supplied}' does not match '{expected}'")


def _require_objects(value: Any, label: str, errors: list[str]) -> tuple[str, ...]:
    """The objects a constraint applies to, sorted and de-duplicated.

    Sorted because the same object list supplied in a different order must serialise identically; that
    is what makes two constraint files comparable without a semantic pass.
    """
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        errors.append(f"{label}: expected array of strings, got {type(value).__name__}")
        return ()
    objects: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            errors.append(f"{label}[{index}]: expected string, got {type(item).__name__}")
        elif not item.strip():
            errors.append(f"{label}[{index}]: must not be empty")
        else:
            objects.append(item)
    return tuple(sorted(set(objects)))


def _require_clock(value: Any, label: str, errors: list[str]) -> str:
    return _require_str(value, label, errors)


@dataclass(frozen=True)
class CreateClock:
    """`create_clock -name <name> -period <value> [unit] -waveform [...]`

    `period` and `unit` are the literal tokens from the file. `unit` is `None` when the command did
    not state one, which SDC permits by inheriting a default - an absent unit is preserved as absent
    rather than being filled in with a guessed default.
    """

    name: str
    period: str
    unit: str | None = None
    waveform: str | None = None
    objects: tuple[str, ...] = ()
    provenance: Provenance | None = None

    KIND = "create_clock"
    FIELDS = frozenset({"kind", "name", "period", "unit", "waveform", "objects", "provenance"})

    def to_dict(self) -> dict:
        return {
            "kind": self.KIND,
            "name": self.name,
            "period": self.period,
            "unit": self.unit,
            "waveform": self.waveform,
            "objects": list(self.objects),
            # Absent provenance serialises as null rather than being omitted, so "unknown location"
            # is visible on the wire instead of inferred from a missing key.
            "provenance": None if self.provenance is None else self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Any, label: str = "CreateClock", errors: list[str] | None = None) -> "CreateClock":
        errors = [] if errors is None else errors
        if not isinstance(data, dict):
            errors.append(f"{label}: expected object, got {type(data).__name__}")
            data = {}
        _reject_unknown_keys(data, cls.FIELDS, label, errors)
        _require_matching_kind(data, cls.KIND, label, errors)
        raw_provenance = data.get("provenance")
        provenance = (None if raw_provenance is None
                      else Provenance.from_dict(raw_provenance, f"{label}.provenance", errors))
        return cls(
            name=_require_str(data.get("name"), f"{label}.name", errors),
            period=_require_str(data.get("period"), f"{label}.period", errors),
            unit=_require_optional_str(data.get("unit"), f"{label}.unit", errors),
            waveform=_require_optional_str(data.get("waveform"), f"{label}.waveform", errors),
            objects=_require_objects(data.get("objects"), f"{label}.objects", errors),
            provenance=provenance,
        )

    @classmethod
    def parse(cls, data: Any, label: str = "CreateClock") -> "CreateClock":
        errors: list[str] = []
        value = cls.from_dict(data, label, errors)
        _errors_to_raise(errors, label)
        return value


@dataclass(frozen=True)
class InputDelay:
    """`set_input_delay -clock <clock> [-max|-min] <value> [unit] <objects>`

    The value is kept as written. No delay arithmetic happens in the foundation.
    """

    clock: str
    value: str
    unit: str | None = None
    qualifier: str | None = None
    objects: tuple[str, ...] = ()
    provenance: Provenance | None = None

    KIND = "set_input_delay"
    FIELDS = frozenset({"kind", "clock", "value", "unit", "qualifier", "objects", "provenance"})

    def to_dict(self) -> dict:
        return {
            "kind": self.KIND,
            "clock": self.clock,
            "value": self.value,
            "unit": self.unit,
            "qualifier": self.qualifier,
            "objects": list(self.objects),
            "provenance": None if self.provenance is None else self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Any, label: str = "InputDelay", errors: list[str] | None = None) -> "InputDelay":
        errors = [] if errors is None else errors
        if not isinstance(data, dict):
            errors.append(f"{label}: expected object, got {type(data).__name__}")
            data = {}
        _reject_unknown_keys(data, cls.FIELDS, label, errors)
        _require_matching_kind(data, cls.KIND, label, errors)
        raw_provenance = data.get("provenance")
        provenance = (None if raw_provenance is None
                      else Provenance.from_dict(raw_provenance, f"{label}.provenance", errors))
        return cls(
            clock=_require_clock(data.get("clock"), f"{label}.clock", errors),
            value=_require_str(data.get("value"), f"{label}.value", errors),
            unit=_require_optional_str(data.get("unit"), f"{label}.unit", errors),
            qualifier=_require_optional_qualifier(data.get("qualifier"), f"{label}.qualifier", errors),
            objects=_require_objects(data.get("objects"), f"{label}.objects", errors),
            provenance=provenance,
        )

    @classmethod
    def parse(cls, data: Any, label: str = "InputDelay") -> "InputDelay":
        errors: list[str] = []
        value = cls.from_dict(data, label, errors)
        _errors_to_raise(errors, label)
        return value


@dataclass(frozen=True)
class OutputDelay:
    """`set_output_delay -clock <clock> [-max|-min] <value> [unit] <objects>`

    Structurally the same shape as `InputDelay`. Kept as a separate type rather than one type with a
    direction flag: VLSI-1 will reason about input and output delay differently, and a flag would push
    that difference into every call site instead of into the type. The duplication is four fields, and
    it buys a contract that cannot be misused by passing the wrong one.
    """

    clock: str
    value: str
    unit: str | None = None
    qualifier: str | None = None
    objects: tuple[str, ...] = ()
    provenance: Provenance | None = None

    KIND = "set_output_delay"
    FIELDS = frozenset({"kind", "clock", "value", "unit", "qualifier", "objects", "provenance"})

    def to_dict(self) -> dict:
        return {
            "kind": self.KIND,
            "clock": self.clock,
            "value": self.value,
            "unit": self.unit,
            "qualifier": self.qualifier,
            "objects": list(self.objects),
            "provenance": None if self.provenance is None else self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Any, label: str = "OutputDelay", errors: list[str] | None = None) -> "OutputDelay":
        errors = [] if errors is None else errors
        if not isinstance(data, dict):
            errors.append(f"{label}: expected object, got {type(data).__name__}")
            data = {}
        _reject_unknown_keys(data, cls.FIELDS, label, errors)
        _require_matching_kind(data, cls.KIND, label, errors)
        raw_provenance = data.get("provenance")
        provenance = (None if raw_provenance is None
                      else Provenance.from_dict(raw_provenance, f"{label}.provenance", errors))
        return cls(
            clock=_require_clock(data.get("clock"), f"{label}.clock", errors),
            value=_require_str(data.get("value"), f"{label}.value", errors),
            unit=_require_optional_str(data.get("unit"), f"{label}.unit", errors),
            qualifier=_require_optional_qualifier(data.get("qualifier"), f"{label}.qualifier", errors),
            objects=_require_objects(data.get("objects"), f"{label}.objects", errors),
            provenance=provenance,
        )

    @classmethod
    def parse(cls, data: Any, label: str = "OutputDelay") -> "OutputDelay":
        errors: list[str] = []
        value = cls.from_dict(data, label, errors)
        _errors_to_raise(errors, label)
        return value


#: Constructed rather than written out twice, so the round-trip contract is checked for all three.
CONSTRAINT_TYPES = (CreateClock, InputDelay, OutputDelay)


def constraint_from_dict(data: Any, label: str = "constraint", errors: list[str] | None = None):
    """Dispatch on a constraint's `kind` to the right type. The only polymorphism here.

    A `kind` outside `CONSTRAINT_KINDS` is an error rather than a silently ignored entry: the
    foundation represents three commands, and a fourth arriving in a payload means the caller expects
    support this milestone does not have.
    """
    errors = [] if errors is None else errors
    if not isinstance(data, dict):
        errors.append(f"{label}: expected object, got {type(data).__name__}")
        return None
    kind = data.get("kind")
    for constraint_type in CONSTRAINT_TYPES:
        if kind == constraint_type.KIND:
            return constraint_type.from_dict(data, label, errors)
    errors.append(f"{label}.kind: '{kind}' is not one of {list(CONSTRAINT_KINDS)}")
    return None


def constraint_sort_key(constraint: Any) -> tuple:
    """A total order for constraints: by command, then clock/name, then value, then location.

    Total, so a set of constraints read from several files has one representation regardless of the
    order they were read in. Provenance is the final tiebreak rather than the first, so two constraints
    of the same kind sort by what they SAY before by where they were written.
    """
    kind = getattr(constraint, "KIND", "")
    name = getattr(constraint, "name", None) or getattr(constraint, "clock", "")
    value = getattr(constraint, "period", None) or getattr(constraint, "value", "") or ""
    provenance = getattr(constraint, "provenance", None)
    if provenance is None:
        source_type, ref, line = "", "", -1
    else:
        source_type, ref = provenance.source_type, provenance.ref
        line = provenance.line if provenance.line is not None else -1
    return (kind, name, value, source_type, ref, line)


def sort_constraints(constraints):
    """Constraints in the canonical order. Stable, total, independent of input order."""
    return tuple(sorted(constraints, key=constraint_sort_key))