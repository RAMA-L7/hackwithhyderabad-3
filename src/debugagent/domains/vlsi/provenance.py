"""Provenance: the minimal record that makes a VLSI observation traceable to a source.

A deterministic finding is only checkable if it says where it came from. This module is that record,
and it is deliberately small: it names a source, and it says where in that source, or says that it
cannot.

**Unknown location stays unknown.** `line` is `int | None`, and `None` is a first-class value meaning
"this analysis does not know a line". It is never coerced to `0`, `-1`, `"unknown"` or `""`, and it
serialises as an explicit `null` rather than being omitted. A fabricated line number is worse than an
absent one: it looks like evidence and can be checked against the wrong place.

Trust boundary: data only. Nothing here reads a file, runs a tool, or judges anything. A
`Provenance` is a claim about origin, not about correctness - the same distinction
`docs/adr-001-hub-spoke-coordinator.md` draws between a refusal and a failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: What kind of thing a provenance record points at. Deliberately about ORIGIN, not about trust:
#: `tool_run` is not more authoritative than `file`, it is only differently located.
PROVENANCE_SOURCE_TYPES = ("artifact", "file", "tool_run", "derived")

#: Used when a constraint or finding genuinely has no single source - e.g. an analysis over several
#: inputs at once. It is a named value rather than a nullable `ref` so that "several sources" is
#: visible in a report instead of looking like a missing one.
DERIVED_SOURCE = "derived"

#: The `ref` value used with `DERIVED_SOURCE`.
DERIVED_REF = "multiple"


class VlsiContractError(ValueError):
    """A VLSI contract value is malformed. Fails closed, never coerced.

    Subclasses `ValueError` to match `agents.tasks.TaskSpecError`, so callers already written to
    catch a bad contract value keep working unchanged.
    """


def _errors_to_raise(errors: list[str], label: str) -> None:
    if errors:
        raise VlsiContractError(f"{label}: " + "; ".join(errors))


def _require_str(value: Any, label: str, errors: list[str]) -> str:
    if not isinstance(value, str):
        errors.append(f"{label}: expected string, got {type(value).__name__}")
        return ""
    if not value.strip():
        errors.append(f"{label}: must not be empty")
    return value


def _require_source_type(value: Any, label: str, errors: list[str]) -> str:
    source_type = _require_str(value, label, errors)
    if source_type and source_type not in PROVENANCE_SOURCE_TYPES:
        errors.append(f"{label}: '{source_type}' is not one of {list(PROVENANCE_SOURCE_TYPES)}")
    return source_type


def _require_optional_line(value: Any, label: str, errors: list[str]) -> int | None:
    """A line number, or explicitly unknown.

    `None` is accepted and returned unchanged. A bool is rejected because `isinstance(True, int)` is
    true in Python, and `True` as a line number is a bug rather than a location.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{label}: expected an integer line number or null, got {type(value).__name__}")
        return None
    if value < 1:
        # Line numbers in every tool this will face are 1-based. Accepting 0 or -1 would let a
        # placeholder masquerade as a location, which is exactly what this field must not allow.
        errors.append(f"{label}: line numbers are 1-based, got {value}")
        return None
    return value


def _reject_unknown_keys(mapping: dict, allowed: frozenset[str], label: str,
                         errors: list[str]) -> None:
    """Fail on any key the contract does not define.

    This is what makes the observation/proposal boundary structurally enforceable rather than a
    convention: a caller cannot attach `hypothesis`, `recommendation` or `root_cause` to a
    deterministic finding, because the extra key is rejected instead of ignored. Silently dropping
    unknown fields would let a proposal travel inside a finding unnoticed.
    """
    for key in sorted(mapping):
        if key not in allowed:
            errors.append(f"{label}: unknown field '{key}' (allowed: {sorted(allowed)})")


@dataclass(frozen=True)
class Provenance:
    """Where an observation came from.

    `source_type` says what sort of source, `ref` identifies it (a repository-relative path, an
    artifact identity, a tool-run label), and `line` says where inside it - or `None` when that is
    genuinely unknown.

    Frozen and hashable, so a finding carrying provenance can be compared, sorted and de-duplicated.
    """

    source_type: str
    ref: str
    line: int | None = None

    FIELDS = frozenset({"source_type", "ref", "line"})

    @property
    def location_known(self) -> bool:
        """Whether a location is known. Explicit, so a report can say "location unknown" out loud."""
        return self.line is not None

    def to_dict(self) -> dict:
        # `line` is always present, including as null. Omitting it would make unknown and absent the
        # same thing on the wire.
        return {"source_type": self.source_type, "ref": self.ref, "line": self.line}

    @classmethod
    def derived(cls, ref: str = DERIVED_REF) -> "Provenance":
        """Provenance for an analysis over several inputs at once, with no single location."""
        return cls(source_type=DERIVED_SOURCE, ref=ref, line=None)

    @classmethod
    def from_dict(cls, data: Any, label: str = "Provenance", errors: list[str] | None = None) -> "Provenance":
        errors = [] if errors is None else errors
        if not isinstance(data, dict):
            errors.append(f"{label}: expected object, got {type(data).__name__}")
            data = {}
        _reject_unknown_keys(data, cls.FIELDS, label, errors)
        return cls(
            source_type=_require_source_type(data.get("source_type"), f"{label}.source_type", errors),
            ref=_require_str(data.get("ref"), f"{label}.ref", errors),
            line=_require_optional_line(data.get("line"), f"{label}.line", errors),
        )

    @classmethod
    def parse(cls, data: Any, label: str = "Provenance") -> "Provenance":
        """`from_dict` that raises instead of accumulating. For callers with a single value to check."""
        errors: list[str] = []
        value = cls.from_dict(data, label, errors)
        _errors_to_raise(errors, label)
        return value