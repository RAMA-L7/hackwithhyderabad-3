"""VLSI-1D.3b: semantic SDC repair, and its deterministic rendering into proposed file content.

This module answers the last open question of the VLSI-1D design: **who authors the repair?**

    the agent proposes SEMANTIC repair intent      ->  SdcRepair
    the deterministic domain layer renders it      ->  proposed SDC text

The agent never writes SDC. It states an engineering operation in structured fields - "define a clock
named pll_clk, period 10, on port pll" - and the renderer composes the Tcl from those fields. There is no
field anywhere in `SdcRepair` that could carry a line of Tcl, a whole file, or a diff, so the failure mode
of "the model emitted plausible-looking constraints" is not reachable by construction rather than by
instruction. The rendered text then feeds the existing Patch Generator unchanged, which continues to
label it `PROPOSED PATCH (unapplied, unverified)`; this module adds no trust level and claims no outcome.

## The rule that shapes everything here: never invent an engineering value

A period is not a formatting detail. `create_clock -name pll_clk -period 10 [get_ports pll]` states that
`pll_clk` has a 10 ns period and arrives on port `pll`, and a reader will sign off against it. Producing
those two numbers when the evidence does not contain them would be fabrication wearing the costume of a
patch - and it would be invisible, because the result would be syntactically perfect.

So the renderer composes only what a `SdcRepair` states, and refuses otherwise:

    define_clock without a period        -> RepairUnavailable(missing_required_value)
    define_clock without target objects  -> RepairUnavailable(missing_required_value)

Which findings can be rendered at all, from the evidence the analyzer actually produces:

    duplicate_constraint    RENDERABLE   the repeats are identical, so one can be removed whole.
                                         Nothing is chosen: the survivor already existed.
    conflicting_constraint  REFUSED      evidence lists `periods=10,12` but establishes no basis to
                                         prefer one. Picking is an engineering decision.
    missing_constraint      CONDITIONAL  the analysis establishes the clock NAME only. A create_clock
                                         additionally needs a period and target objects, which the
                                         analysis does not contain. A repair that states them renders; a
                                         repair that does not is refused.

A refusal is a first-class outcome with a structured reason, never a silent fallback and never a
plausible-looking line.

## Validation is by re-parsing, not by inspection

The renderer does not trust its own formatting. It parses the line it just produced with the same
`sdc_parser` the analysis used and requires the result to be exactly the intended constraint - right
kind, right name, right period, right unit, right targets. A value the renderer cannot round-trip
through the real parser (`-period abc`) is refused rather than emitted. So "never invent" is enforced by
the parser, and there is no second, private notion of what a valid SDC line looks like.

Semantic validation is equally structural: a repair renders only if the baseline contains a finding of
the matching kind naming that same clock. An operation invented for a finding that does not exist is
refused, which is what stops the renderer from becoming a general-purpose SDC editor reachable from a
model's imagination.

## Placement is evidence, not taste

- `define_clock` inserts immediately BEFORE the first line that references the missing clock - the line
  the `missing_constraint` finding already points at. Tcl evaluates sequentially, so a clock defined
  after the delay that uses it is still a broken file; appending at the end would "repair" the finding
  and leave the design just as broken.
- `remove_duplicate_clock` deletes the repeats named in the finding's `lines` detail and keeps the
  first. Each line to delete is re-parsed in isolation first; a definition spanning several lines cannot
  be delimited by the current domain model, so it is refused rather than half-removed.

Only insertion and deletion are performed. Every other byte of the source is carried through untouched,
including comments, blank lines and the file's line-ending convention.

## What this module is not

No filesystem access, no LLM, no `RepositoryWriter`, no apply path, no memory, no generic pipeline
contract, no analysis. It is a pure function of `(repair, baseline, source_text)` - the same three inputs
always produce the same bytes, and nothing here can change a file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .findings import VlsiFinding
from .provenance import _errors_to_raise, _reject_unknown_keys, _require_str
from .sdc_analyzer import SdcAnalysisResult
from .sdc_parser import parse_sdc

#: The engineering operations this renderer can express. A vocabulary, not a hierarchy: the foundation
#: does not know which repairs a later increment will need, and inventing a class per operation now
#: would be guessing. An operation outside this tuple is REFUSED, never approximated.
REPAIR_OPERATIONS = ("define_clock", "remove_duplicate_clock")

#: Why a repair could not be rendered. Every refusal names one of these, so "no repair was produced" is
#: always accompanied by a reason a caller can act on rather than a generic failure.
REPAIR_UNAVAILABLE_REASONS = (
    "unsupported_operation",         # the operation is outside REPAIR_OPERATIONS
    "missing_required_value",        # a value the operation requires was not stated
    "unrenderable_value",            # a stated value does not survive a round trip through the parser
    "no_supporting_finding",         # the baseline holds no finding of the matching kind for this clock
    "conflicting_finding",           # the clock is CONFLICTING, not duplicate: choosing is not derivation
    "incomplete_evidence",           # the analysis did not fully understand the file
    "target_mismatch",               # the repair names a different file than the finding does
    "unknown_location",              # the finding does not know where to act
    "definition_not_single_line",    # a definition spans lines the domain model cannot delimit
    "source_line_mismatch",          # a named source line is not the constraint the finding described
)

#: The finding kind each operation must be supported by. Stated once so the pairing cannot drift between
#: the validation step and the rendering step.
_REQUIRED_FINDING_KIND = {
    "define_clock": "missing_constraint",
    "remove_duplicate_clock": "duplicate_constraint",
}

#: The constraint kind the renderer emits. Only one, because only one is safely derivable today.
_CLOCK_KIND = "create_clock"


def _require_object_list(value: Any, label: str, errors: list[str]) -> tuple[str, ...]:
    """Normalise an object list to a tuple, rejecting blanks rather than dropping them.

    A blank object would render as `[get_ports  ]`, so it is an error here rather than something the
    renderer quietly tidies away. Order is preserved as given; the parser sorts on read, so rendering
    order does not change meaning.
    """
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        errors.append(f"{label}: expected an array of strings, got {type(value).__name__}")
        return ()
    objects: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            errors.append(f"{label}[{index}]: expected a string, got {type(item).__name__}")
            continue
        if not item.strip():
            errors.append(f"{label}[{index}]: must not be empty")
            continue
        objects.append(item)
    return tuple(objects)


@dataclass(frozen=True)
class SdcRepair:
    """An engineering operation, stated in structured fields. Never text.

    `operation` says what is intended; `clock` says which clock; `period`, `unit` and `objects` carry the
    values that operation needs and are meaningless for `remove_duplicate_clock`. There is no field for
    Tcl, for a file body or for a diff, so this record cannot express "whatever SDC the model felt like
    writing" - the boundary is the type, not a rule the caller is asked to respect.

    Values are stored as written, never converted: `period="10"` with `unit="ns"` and `period="10ns"` with
    no unit both mean 10 ns, and this module does not get a vote on which spelling to prefer.
    """

    operation: str
    clock: str
    target: str = ""
    period: str = ""
    unit: str = ""
    objects: tuple[str, ...] = ()

    FIELDS = frozenset({"operation", "clock", "target", "period", "unit", "objects"})

    #: Fields that carry text a renderer would have to compose. Checked at the contract boundary so an
    #: unrecognised or misspelled field is an error, never a silently ignored instruction.
    def to_dict(self) -> dict:
        return {"operation": self.operation, "clock": self.clock, "target": self.target,
                "period": self.period, "unit": self.unit,
                "objects": list(self.objects)}

    @classmethod
    def from_dict(cls, data: Any, label: str = "SdcRepair", errors: list[str] | None = None) -> "SdcRepair":
        errors = [] if errors is None else errors
        if not isinstance(data, dict):
            errors.append(f"{label}: expected object, got {type(data).__name__}")
            data = {}
        # FIRST, so an unknown field is reported even when the rest of the payload is also wrong. This is
        # the mechanism that stops a repair travelling extra instructions: `{"period": "10", "tcl": "..."}`
        # is rejected outright rather than parsed with `tcl` dropped.
        _reject_unknown_keys(data, cls.FIELDS, label, errors)

        operation = _require_str(data.get("operation"), f"{label}.operation", errors)
        if operation and operation not in REPAIR_OPERATIONS:
            errors.append(f"{label}.operation: '{operation}' is not one of {list(REPAIR_OPERATIONS)}")

        clock = _require_str(data.get("clock"), f"{label}.clock", errors)
        if clock and clock != clock.strip():
            errors.append(f"{label}.clock: must not have surrounding whitespace, got {clock!r}")

        target = data.get("target", "")
        if target is not None and not isinstance(target, str):
            errors.append(f"{label}.target: expected string, got {type(target).__name__}")
            target = ""

        # `period`/`unit` are optional STRINGS, and empty means "not stated". They are not validated as
        # timing here: what a period may contain is the renderer's question, decided by round-tripping
        # through the parser, not this record's.
        for name in ("period", "unit"):
            value = data.get(name, "")
            if value is None:
                continue
            if not isinstance(value, str):
                errors.append(f"{label}.{name}: expected string, got {type(value).__name__}")

        return cls(operation=operation, clock=clock, target=target or "",
                   period=data.get("period") or "", unit=data.get("unit") or "",
                   objects=_require_object_list(data.get("objects"), f"{label}.objects", errors))

    @classmethod
    def parse(cls, data: Any, label: str = "SdcRepair") -> "SdcRepair":
        errors: list[str] = []
        value = cls.from_dict(data, label, errors)
        _errors_to_raise(errors, label)
        return value


@dataclass(frozen=True)
class RenderedSdcRepair:
    """Proposed content for one repair, plus what changed. Not applied, not verified, not claimed to work.

    `proposed` is FULL file content, because that is what the existing Patch Generator diffs and what a
    future authorised write would replace. It is a proposal: this module has no way to apply it.
    """

    operation: str
    clock: str
    target: str
    proposed: str
    inserted: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"operation": self.operation, "clock": self.clock, "target": self.target,
                "proposed": self.proposed, "inserted": list(self.inserted),
                "removed": list(self.removed)}


@dataclass(frozen=True)
class RepairUnavailable:
    """A repair that will not be rendered, and why. The refusal IS the result.

    Carries no proposed content, so a caller cannot accidentally use one: there is nothing to misuse. A
    reason is always present, because "no repair" without a cause is indistinguishable from a bug.
    """

    operation: str
    clock: str
    target: str
    reason: str
    detail: str

    def to_dict(self) -> dict:
        return {"operation": self.operation, "clock": self.clock, "target": self.target,
                "reason": self.reason, "detail": self.detail}


# -- source text handling -------------------------------------------------------------------------------

def _terminator(source_text: str) -> str:
    """The file's line ending, so an inserted line matches the file it joins.

    CRLF when the file contains any, otherwise LF. A repair must not convert a file's endings as a side
    effect of adding one line - that would show as every line changing in the diff the engineer reviews.
    """
    return "\r\n" if "\r\n" in source_text else "\n"


def _split_lines(source_text: str) -> list[str]:
    """Source lines WITH their terminators, so rejoining reproduces the file byte for byte."""
    return source_text.splitlines(keepends=True)


def _line_at(lines: list[str], number: int) -> str | None:
    """The 1-based source line, without its terminator, or None if the number is out of range."""
    if number < 1 or number > len(lines):
        return None
    return lines[number - 1].rstrip("\r\n")


def _is_self_contained(line: str) -> bool:
    """Whether one physical line is a whole command, rather than part of one.

    Re-parsing a line in isolation is NOT enough to establish this. A line ending in a backslash is the
    START of a continued command, and the parser will happily read it alone as a complete one - the
    trailing backslash simply becomes a stray object - so a single-line parse of
    `create_clock ... -period 10 \\` succeeds while the real command is not yet finished. Deleting that
    line would leave its continuation orphaned in the file: a fragment of a constraint, which is worse
    than leaving the duplicate in place.

    So a line must also not end in a continuation, and its brackets, braces and quotes must balance.
    This is deliberately a conservative test rather than a second tokenizer: over-refusing costs one
    unrepairable file, while under-refusing corrupts one.
    """
    if line.rstrip().endswith("\\"):
        return False
    brackets = braces = 0
    in_quote = False
    for character in line:
        if character == '"':
            in_quote = not in_quote
        elif not in_quote:
            if character == "[":
                brackets += 1
            elif character == "]":
                brackets -= 1
            elif character == "{":
                braces += 1
            elif character == "}":
                braces -= 1
    return brackets == 0 and braces == 0 and not in_quote


# -- rendering helpers -----------------------------------------------------------------------------------

def _render_clock_line(repair: SdcRepair) -> str:
    """Compose one `create_clock` line from the structured fields. Composition only, never invention.

    A single target is written `[get_ports d]` and several are written `[get_ports {a b}]`, which is the
    spelling a hand-written SDC file uses and which the parser reads back as the same set either way.
    """
    objects = " ".join(repair.objects)
    if len(repair.objects) > 1:
        objects = "{" + objects + "}"
    period = f"{repair.period}{repair.unit}"
    return (f"create_clock -name {repair.clock} -period {period} "
            f"[get_ports {objects}]")


def _verify_clock_line(line: str, repair: SdcRepair, target: str) -> str | None:
    """Parse the composed line and confirm it says exactly what the repair asked for. None if it does not.

    This is the guarantee that no invented value can escape: a period the parser cannot read, or a target
    it cannot resolve, comes back as a refusal rather than as text that merely looks right.
    """
    parsed = parse_sdc(line, ref=target)
    if parsed.issues:
        first = parsed.issues[0]
        return f"the composed line does not parse ({first.kind}: {first.message})"
    if len(parsed.constraints) != 1:
        return (f"the composed line yielded {len(parsed.constraints)} constraints, expected exactly 1")
    constraint = parsed.constraints[0]
    if getattr(constraint, "KIND", "") != _CLOCK_KIND:
        return f"the composed line yielded a {getattr(constraint, 'KIND', '?')}, expected {_CLOCK_KIND}"
    if getattr(constraint, "name", None) != repair.clock:
        return (f"the composed line defines clock {getattr(constraint, 'name', None)!r}, "
                f"expected {repair.clock!r}")
    if str(getattr(constraint, "period", "")) != repair.period:
        return (f"the composed line carries period {getattr(constraint, 'period', '')!r}, "
                f"expected {repair.period!r}")
    if (getattr(constraint, "unit", None) or "") != repair.unit:
        return (f"the composed line carries unit {getattr(constraint, 'unit', None)!r}, "
                f"expected {repair.unit!r}")
    if tuple(getattr(constraint, "objects", ()) or ()) != tuple(sorted(set(repair.objects))):
        return (f"the composed line carries objects {tuple(getattr(constraint, 'objects', ()) or ())!r}, "
                f"expected {tuple(sorted(set(repair.objects)))!r}")
    return None


#: A clock name used only inside validation probes. Never reaches proposed content.
_PROBE = "_probe"


def _timing_value_problem(period_text: str, target: str) -> str | None:
    """Whether `period_text` is a value this domain recognises as a timing quantity. None if it is.

    Round-tripping the composed `create_clock` is NOT sufficient on its own: `create_clock -period`
    accepts any non-empty token, so `-period abc` parses cleanly and a verification built only on the
    clock line would wave it through.

    The parser IS strict about a delay value - a non-numeric positional token is `malformed`, and
    deliberately so, so that `set_input_delay -clock c [get_ports d]` cannot be misread as a delay of
    "d". So the same text is checked as a delay value, which reuses the parser's own definition of a
    number instead of introducing a private one here that could drift from it. An unrecognised unit
    suffix is accepted, matching the parser's documented stance that values are preserved rather than
    normalised.
    """
    probe = f"set_input_delay -clock {_PROBE} {period_text} [get_ports {_PROBE}]"
    parsed = parse_sdc(probe, ref=target)
    if parsed.issues:
        first = parsed.issues[0]
        return (f"{period_text!r} is not a timing value ({first.kind}: {first.message}); a period that "
                f"is not a number would state a constraint the design cannot mean")
    constraint = parsed.constraints[0]
    read_back = f"{getattr(constraint, 'value', '')}{getattr(constraint, 'unit', None) or ''}"
    if read_back != period_text:
        return f"{period_text!r} does not survive parsing unchanged (read back as {read_back!r})"
    return None


def _render_define_clock(repair: SdcRepair, findings: tuple[VlsiFinding, ...],
                         source_text: str) -> RenderedSdcRepair | RepairUnavailable:
    """Insert a `create_clock` before the first line that references the missing clock."""
    # `strip()` on the strings: a value of `"   "` is not a period, and treating whitespace as present would
    # render `-period    ` and hand it to the parser as though it were stated.
    missing = [name for name in ("period", "objects")
               if not str(getattr(repair, name) or "").strip()]
    if missing:
        return RepairUnavailable(
            operation=repair.operation, clock=repair.clock, target=repair.target,
            reason="missing_required_value",
            detail=(f"defining clock {repair.clock!r} requires {' and '.join(missing)}, and deterministic "
                    f"analysis establishes only that {repair.clock!r} is referenced but never defined. "
                    f"A period or a target port would have to be invented, so no repair is proposed."))

    problem = _timing_value_problem(f"{repair.period}{repair.unit}", repair.target)
    if problem is not None:
        return RepairUnavailable(operation=repair.operation, clock=repair.clock, target=repair.target,
                                 reason="unrenderable_value", detail=problem)

    line = _render_clock_line(repair)
    problem = _verify_clock_line(line, repair, repair.target)
    if problem is not None:
        return RepairUnavailable(operation=repair.operation, clock=repair.clock, target=repair.target,
                                 reason="unrenderable_value",
                                 detail=f"{problem}; the stated value cannot be expressed as { _CLOCK_KIND }")

    # Insert before the EARLIEST reference. Every finding for this clock names a delay line; the clock
    # must exist by the first of them, because Tcl evaluates sequentially and a delay naming an
    # undefined clock is exactly the fault being repaired.
    locations = [finding.provenance.line for finding in findings
                 if finding.provenance.line is not None]
    if not locations:
        return RepairUnavailable(operation=repair.operation, clock=repair.clock, target=repair.target,
                                 reason="unknown_location",
                                 detail=(f"no finding for clock {repair.clock!r} records a line, so there "
                                         f"is no deterministic insertion point"))
    at = min(locations)

    lines = _split_lines(source_text)
    if at > len(lines):
        return RepairUnavailable(operation=repair.operation, clock=repair.clock, target=repair.target,
                                 reason="unknown_location",
                                 detail=(f"the insertion point line {at} is past the end of the file, "
                                         f"which holds {len(lines)} line(s)"))

    terminator = _terminator(source_text)
    lines.insert(at - 1, f"{line}{terminator}")
    return RenderedSdcRepair(operation=repair.operation, clock=repair.clock, target=repair.target,
                             proposed="".join(lines), inserted=(line,))


def _render_remove_duplicate(repair: SdcRepair, findings: tuple[VlsiFinding, ...],
                             source_text: str) -> RenderedSdcRepair | RepairUnavailable:
    """Delete the redundant repeats of a clock, keeping the first definition.

    Nothing is chosen here. The repeats are identical in period, unit, waveform and targets by the
    analyzer's own definition of `duplicate_constraint`, so the survivor is a definition that already
    exists and no engineering value is chosen by removing the others.
    """
    numbers: list[int] = []
    for finding in findings:
        raw = finding.details_dict.get("lines", "")
        numbers.extend(int(part) for part in str(raw).split(",") if part.strip().isdigit())
    ordered = sorted(set(numbers))
    if len(ordered) < 2:
        return RepairUnavailable(operation=repair.operation, clock=repair.clock, target=repair.target,
                                 reason="no_supporting_finding",
                                 detail=(f"clock {repair.clock!r} has {len(ordered)} recorded definition "
                                         f"line(s); removing a duplicate needs at least 2"))

    lines = _split_lines(source_text)
    removed: list[str] = []
    for number in ordered[1:]:
        text = _line_at(lines, number)
        if text is None:
            return RepairUnavailable(operation=repair.operation, clock=repair.clock, target=repair.target,
                                     reason="unknown_location",
                                     detail=f"definition line {number} is not present in the source")
        # Re-parse the single line in isolation, and confirm it is a WHOLE command. If it is only the
        # start of a continued one, its extent is not something this model records, and deleting the
        # start would leave the continuation behind as an orphaned fragment.
        if not _is_self_contained(text):
            return RepairUnavailable(
                operation=repair.operation, clock=repair.clock, target=repair.target,
                reason="definition_not_single_line",
                detail=(f"line {number} is not a self-contained command (it continues, or a bracket, "
                        f"brace or quote is open); its extent cannot be established, so it will not be "
                        f"partially deleted"))
        parsed = parse_sdc(text, ref=repair.target)
        if parsed.issues or len(parsed.constraints) != 1:
            detail = (parsed.issues[0].message if parsed.issues
                      else f"the line yielded {len(parsed.constraints)} constraints")
            return RepairUnavailable(
                operation=repair.operation, clock=repair.clock, target=repair.target,
                reason="definition_not_single_line",
                detail=(f"line {number} does not parse to a single {_CLOCK_KIND} ({detail}); its extent "
                        f"cannot be established, so it will not be partially deleted"))
        constraint = parsed.constraints[0]
        names = [constraint.name] if constraint.name else list(constraint.objects or ())
        if getattr(constraint, "KIND", "") != _CLOCK_KIND or repair.clock not in names:
            return RepairUnavailable(
                operation=repair.operation, clock=repair.clock, target=repair.target,
                reason="source_line_mismatch",
                detail=(f"line {number} is a {getattr(constraint, 'KIND', '?')} for {names!r}, not a "
                        f"{_CLOCK_KIND} defining {repair.clock!r}"))
        removed.append(number)

    survivors = [line for index, line in enumerate(lines, start=1) if index not in set(removed)]
    return RenderedSdcRepair(operation=repair.operation, clock=repair.clock, target=repair.target,
                             proposed="".join(survivors),
                             removed=tuple(_line_at(lines, number) or "" for number in removed))


def render_sdc_repair(repair: SdcRepair, baseline: SdcAnalysisResult,
                      source_text: str) -> RenderedSdcRepair | RepairUnavailable:
    """Render a semantic repair into proposed full file content, or refuse with a structured reason.

    A pure function of its three arguments: the same repair, baseline and source always produce the same
    bytes, and nothing here opens a file, calls a model, or applies anything. `baseline` is the analysis
    the repair is measured against, and it is REQUIRED rather than optional - a repair is only ever
    rendered against evidence, never against an assumption that the evidence exists.
    """
    if repair.operation not in REPAIR_OPERATIONS:
        return RepairUnavailable(operation=repair.operation, clock=repair.clock, target=repair.target,
                                 reason="unsupported_operation",
                                 detail=(f"{repair.operation!r} is not one of "
                                         f"{list(REPAIR_OPERATIONS)}; no repair is proposed"))
    if not repair.clock.strip():
        return RepairUnavailable(operation=repair.operation, clock=repair.clock, target=repair.target,
                                 reason="missing_required_value",
                                 detail="a repair must name the clock it acts on")

    # An incomplete analysis makes every negative conclusion provisional, so it cannot support a claim
    # that a specific line is a redundant definition. Same rule the comparison layer applies.
    if baseline is None or not baseline.complete:
        status = getattr(baseline, "status", "absent")
        return RepairUnavailable(operation=repair.operation, clock=repair.clock, target=repair.target,
                                 reason="incomplete_evidence",
                                 detail=(f"the analysis status is {status!r}; a repair cannot be derived "
                                         f"from an analysis that did not fully understand the file"))

    required_kind = _REQUIRED_FINDING_KIND[repair.operation]
    supported = tuple(finding for finding in baseline.findings_of_kind(required_kind)
                      if str(finding.details_dict.get("clock", "")) == repair.clock)

    # `conflicting_constraint` is a DIFFERENT condition from `duplicate_constraint` and must not satisfy
    # a removal: the repeats disagree, so removing one would choose a period rather than restate a fact.
    if not supported and repair.operation == "remove_duplicate_clock":
        conflicting = tuple(finding for finding in baseline.findings_of_kind("conflicting_constraint")
                            if str(finding.details_dict.get("clock", "")) == repair.clock)
        if conflicting:
            periods = conflicting[0].details_dict.get("periods", "")
            return RepairUnavailable(
                operation=repair.operation, clock=repair.clock, target=repair.target,
                reason="conflicting_finding",
                detail=(f"clock {repair.clock!r} is CONFLICTING, not duplicate (periods {periods!r}). "
                        f"Removing one definition would choose between them, and deterministic evidence "
                        f"establishes no basis for that choice, so no repair is proposed."))

    if not supported:
        return RepairUnavailable(operation=repair.operation, clock=repair.clock, target=repair.target,
                                 reason="no_supporting_finding",
                                 detail=(f"the analysis holds no {required_kind} for clock "
                                         f"{repair.clock!r}, so this operation is not supported by evidence"))

    references = {finding.provenance.ref for finding in supported}
    if repair.target and references != {repair.target}:
        return RepairUnavailable(operation=repair.operation, clock=repair.clock, target=repair.target,
                                 reason="target_mismatch",
                                 detail=(f"the repair names {repair.target!r} but the finding for clock "
                                         f"{repair.clock!r} is in {sorted(references)!r}"))

    if repair.operation == "define_clock":
        return _render_define_clock(repair, supported, source_text)
    return _render_remove_duplicate(repair, supported, source_text)


__all__ = [
    "REPAIR_OPERATIONS",
    "REPAIR_UNAVAILABLE_REASONS",
    "RenderedSdcRepair",
    "RepairUnavailable",
    "SdcRepair",
    "render_sdc_repair",
]