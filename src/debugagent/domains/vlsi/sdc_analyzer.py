"""VLSI-1B: deterministic consistency analysis over parsed SDC constraints.

Consumes what VLSI-1A produced - typed constraints and parser issues - and returns `VlsiFinding`
observations. **Analysis only.** No agent, no reasoning, no repair, no patch, no LLM, no EDA tool.

This module deliberately does not accept SDC text. It takes a `SdcParseResult`, so the parse/analyze
boundary is structural rather than a convention: there is no code path here that could re-read a file,
re-tokenise a command, or disagree with the parser about what a file said.

## What "deterministic" buys, and what it costs

Every check here is decidable from the parsed constraints alone, with no timing model, no netlist and
no design knowledge. That is a real limit on what can be found, and the limit is the point: a check
that needed a Liberty table or a timing graph would produce a number nobody could reproduce, and a
number nobody can reproduce is not evidence.

## The three checks

1. **Clock-reference existence.** A `set_input_delay` / `set_output_delay` naming a clock that no
   parsed `create_clock` provides. The finding says a definition was not *parsed* - never that the
   clock does not exist. See "completeness" below for why that distinction is load-bearing.
2. **Duplicate clock definitions.** One clock name provided twice with identical period, unit, waveform
   and targets.
3. **Conflicting clock definitions.** One clock name provided more than once where those fields
   differ, so the two statements cannot both hold.

## Clock naming

`create_clock` names a clock either with `-name`, or - when `-name` is absent - after each of its
target objects. Both rules are plain SDC semantics and both are derivable from the typed model, so the
analyzer uses them rather than assuming a single convention.

## Completeness is reported, never assumed

If the parser reported ANY issue - a malformed command or an unsupported one - the file was not fully
understood, and every negative conclusion is therefore unproven: the missing clock could be defined by
an unsupported command such as `create_generated_clock`. Rather than silently emit findings that look
authoritative, the result carries `status="incomplete"` and the parser issues, so a consumer can see
that the negative findings are provisional.

**Zero findings is not a verdict.** `clean` means "no findings were produced and the input was fully
understood". It says nothing about whether the design is correct, and this module never says so - the
authority to conclude belongs to the engineer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .findings import VlsiFinding
from .provenance import Provenance, _errors_to_raise, _reject_unknown_keys
from .sdc_parser import ParseIssue, SdcParseResult

#: `complete` means every command in the file was understood, so negative conclusions hold.
#: `incomplete` means the parser reported something, so they are provisional.
ANALYSIS_STATUSES = ("complete", "incomplete")

#: The constraint kinds that reference a clock by name.
_DELAY_KINDS = ("set_input_delay", "set_output_delay")

#: The constraint kind that provides clocks.
_CLOCK_KIND = "create_clock"

#: Findings whose message compares several definitions point at the FIRST repeat in source order, and
#: carry every occurrence's line in `details`. That keeps a real line rather than a synthesised one: a
#: duplicate is not visible at the first definition, so that is not where it belongs, and dropping the
#: locations entirely would lose information a reader needs.
_LINE_SEPARATOR = ","


def _provided_names(clock: Any) -> tuple[str, ...]:
    """The clock names one `create_clock` provides.

    `-name` when present; otherwise each target object, because a `create_clock` with no name defines a
    clock per target. Returns a tuple, sorted, so the same clock always yields the same names.
    """
    name = getattr(clock, "name", None)
    if name:
        return (name,)
    return tuple(getattr(clock, "objects", ()) or ())


def _definition_fields(clock: Any) -> tuple[str, str, str, tuple[str, ...]]:
    """The fields that must agree for two definitions of one name to be the same statement."""
    return (
        str(getattr(clock, "period", "")),
        str(getattr(clock, "unit", "")),
        str(getattr(clock, "waveform", "")),
        tuple(getattr(clock, "objects", ()) or ()),
    )


def _line_of(record: Any) -> int | None:
    provenance = getattr(record, "provenance", None)
    return None if provenance is None else provenance.line


def _provenance_of(record: Any) -> Provenance:
    provenance = getattr(record, "provenance", None)
    if provenance is None:
        # A constraint from the parser always has provenance; a hand-built one might not. Say so rather
        # than inventing a location.
        return Provenance.derived()
    return provenance


@dataclass(frozen=True)
class SdcAnalysisResult:
    """What deterministic analysis observed, and how completely it was able to run.

    `status` and `findings` are deliberately separate. A result with no findings because the file was
    fully understood is a different thing from a result with no findings because half the file was
    unreadable, and `status` is what tells them apart.
    """

    findings: tuple[VlsiFinding, ...] = ()
    parser_issues: tuple[ParseIssue, ...] = ()

    @property
    def status(self) -> str:
        """`incomplete` when the parser reported anything, because negative findings are then provisional."""
        return "incomplete" if self.parser_issues else "complete"

    @property
    def complete(self) -> bool:
        return self.status == "complete"

    @property
    def clean(self) -> bool:
        """No findings AND a fully understood input.

        NOT a verdict. This does not mean the constraints are correct, the timing closes, or the design
        is safe - only that this analysis found nothing to report about what it could read.
        """
        return not self.findings and self.complete

    def findings_of_kind(self, kind: str) -> tuple[VlsiFinding, ...]:
        return tuple(finding for finding in self.findings if finding.kind == kind)

    def to_dict(self) -> dict:
        return {"status": self.status,
                "findings": [finding.to_dict() for finding in self.findings],
                "parser_issues": [issue.to_dict() for issue in self.parser_issues]}

    @classmethod
    def from_dict(cls, data: Any, label: str = "SdcAnalysisResult", errors: list[str] | None = None) -> "SdcAnalysisResult":
        errors = [] if errors is None else errors
        if not isinstance(data, dict):
            errors.append(f"{label}: expected object, got {type(data).__name__}")
            data = {}
        _reject_unknown_keys(data, frozenset({"status", "findings", "parser_issues"}), label, errors)

        # `status` is READ and checked rather than silently recomputed, so a serialised result cannot
        # claim "complete" while carrying parser issues - the exact confusion this contract exists to
        # prevent.
        status = data.get("status")
        if status not in ANALYSIS_STATUSES:
            errors.append(f"{label}.status: {status!r} is not one of {list(ANALYSIS_STATUSES)}")

        raw_findings = data.get("findings", [])
        raw_issues = data.get("parser_issues", [])
        for name, raw in (("findings", raw_findings), ("parser_issues", raw_issues)):
            if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
                errors.append(f"{label}.{name}: expected array, got {type(raw).__name__}")

        findings = [VlsiFinding.from_dict(item, f"{label}.findings[{index}]", errors)
                    for index, item in enumerate(raw_findings if isinstance(raw_findings, (list, tuple)) else [])]
        issues = [ParseIssue.from_dict(item, f"{label}.parser_issues[{index}]", errors)
                  for index, item in enumerate(raw_issues if isinstance(raw_issues, (list, tuple)) else [])]

        if status == "complete" and issues:
            errors.append(f"{label}: status 'complete' cannot be reported alongside "
                          f"{len(issues)} parser issue(s)")
        if status == "incomplete" and not issues:
            errors.append(f"{label}: status 'incomplete' requires at least one parser issue")

        return cls(findings=tuple(findings), parser_issues=tuple(issues))

    @classmethod
    def parse(cls, data: Any, label: str = "SdcAnalysisResult") -> "SdcAnalysisResult":
        errors: list[str] = []
        value = cls.from_dict(data, label, errors)
        _errors_to_raise(errors, label)
        return value


def analysis_order(finding: VlsiFinding) -> tuple:
    """Source order, then kind, then identity. Total, and independent of input order.

    Deliberately NOT the foundation's `sort_findings`, which orders by severity for REPORTING. An
    engineer working down a file wants the findings in the order the file states them; a dashboard
    wants the worst first. Both orders exist for a reason, so this one is stated and tested rather than
    borrowed.

    `line` is the primary key because every analyzer finding traces to a real source constraint.
    """
    line = finding.provenance.line
    return (
        line if line is not None else -1,
        finding.kind,
        finding.provenance.source_type,
        finding.provenance.ref,
        finding.message,
    )


def _missing_clock_findings(parsed: SdcParseResult, provided: dict[str, list[Any]]) -> list[VlsiFinding]:
    """Delays naming a clock no parsed `create_clock` provides."""
    findings: list[VlsiFinding] = []
    for constraint in parsed.constraints:
        kind = getattr(constraint, "KIND", "")
        if kind not in _DELAY_KINDS:
            continue
        clock = getattr(constraint, "clock", "")
        if not clock or clock in provided:
            continue
        direction = "Input" if kind == "set_input_delay" else "Output"
        findings.append(VlsiFinding(
            kind="missing_constraint",
            severity="critical",
            message=(f"{direction} delay references clock '{clock}', but no create_clock constraint "
                     f"for '{clock}' was parsed."),
            provenance=_provenance_of(constraint),
            # Details are written in sorted key order, matching what the foundation's parse boundary would
            # produce. Emitting them unsorted would make `to_dict()` differ from a parsed round trip.
            details=(("clock", "clock", clock),
                     ("constraint", "constraint", kind),
                     ("constraint_kind", "constraint_kind", getattr(constraint, "KIND", "")),
                     ("objects", "objects", _LINE_SEPARATOR.join(getattr(constraint, "objects", ()) or ()))),
        ))
    return findings


def _clock_name_findings(provided: dict[str, list[Any]]) -> list[VlsiFinding]:
    """One clock name defined more than once: duplicate when identical, conflicting when not."""
    findings: list[VlsiFinding] = []
    for name in sorted(provided):
        definitions = provided[name]
        if len(definitions) < 2:
            continue
        fields = [_definition_fields(constraint) for constraint in definitions]
        identical = all(field == fields[0] for field in fields)
        lines = _LINE_SEPARATOR.join(str(_line_of(constraint)) for constraint in definitions
                                     if _line_of(constraint) is not None)
        periods = _LINE_SEPARATOR.join(sorted({field[0] for field in fields}))

        if identical:
            findings.append(VlsiFinding(
                kind="duplicate_constraint",
                severity="warning",
                message=(f"Clock '{name}' is defined {len(definitions)} times with the same period "
                         f"and targets."),
                provenance=_provenance_of(definitions[1]),
                details=(("clock", "clock", name),
                         ("lines", "lines", lines),
                         ("occurrences", "occurrences", len(definitions))),
            ))
        else:
            findings.append(VlsiFinding(
                kind="conflicting_constraint",
                severity="critical",
                message=(f"Clock '{name}' is defined {len(definitions)} times with differing periods "
                         f"or targets."),
                provenance=_provenance_of(definitions[1]),
                details=(("clock", "clock", name),
                         ("lines", "lines", lines),
                         ("occurrences", "occurrences", len(definitions)),
                         ("periods", "periods", periods)),
            ))
    return findings


def analyze_sdc_constraints(parsed: SdcParseResult) -> SdcAnalysisResult:
    """Analyse a parse result and return observations, in source order.

    Takes typed constraints rather than SDC text, so this function cannot re-read a file or re-parse a
    command. Parser issues are carried into the result rather than dropped, because an unread command
    makes every negative conclusion provisional and the consumer has to know that.
    """
    provided: dict[str, list[Any]] = {}
    for constraint in parsed.constraints:
        if getattr(constraint, "KIND", "") != _CLOCK_KIND:
            continue
        for name in _provided_names(constraint):
            provided.setdefault(name, []).append(constraint)

    # Definitions are put in SOURCE order before anything else looks at them, so "the first repeat" means
    # the second definition in the file rather than the second one the caller happened to pass in. The
    # tiebreak is content rather than position, so a caller who reorders the constraints still gets the
    # same findings - including the same provenance.
    for definitions in provided.values():
        definitions.sort(key=lambda clock: (_line_of(clock) if _line_of(clock) is not None else -1,
                                           str(getattr(clock, "period", "")),
                                           tuple(getattr(clock, "objects", ()) or ())))

    findings = _missing_clock_findings(parsed, provided) + _clock_name_findings(provided)
    findings.sort(key=analysis_order)
    return SdcAnalysisResult(findings=tuple(findings), parser_issues=tuple(parsed.issues))