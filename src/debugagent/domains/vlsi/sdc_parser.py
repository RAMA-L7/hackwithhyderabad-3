"""VLSI-1A: a deterministic SDC reader for the first three constraint families.

Reads SDC source text into the typed contracts of `sdc.py`. **Parsing only.** This module answers one
question - *what constraints does this file state?* - and never *are these constraints correct?* Every
check that would require judgement (a clock referenced but never defined, a port with no delay, a clock
defined twice) belongs to VLSI-1B, and nothing here is a step toward one.

Three properties are worth stating before the code, because they are what make the output usable:

1. **Determinism.** Identical input produces identical constraints, identical ordering, identical
   provenance and identical errors. There is no clock, no randomness, no set iteration and no
   filesystem access in this module. Constraints come back in SOURCE order, which is the only ordering
   that means anything for a file.

2. **Nothing is silently dropped.** A file usually contains commands this subset does not cover. Each
   one is reported as an `unsupported` issue with its own provenance, so "the parser read this file" is
   a statement about the whole file rather than about the part it happened to understand. Silent
   skipping is the failure mode that makes a partial parser dangerous, because the result looks
   complete.

3. **Values are preserved, never converted.** `10`, `10ns`, `0.5` and `500ps` are recorded exactly as
   written. The parser performs no unit arithmetic and no cross-unit comparison; `500ps` is not turned
   into `0.5ns`, because choosing a canonical unit is a VLSI-1 decision with long-lived consequences and
   it is not the parser's to make.

## Language subset

Supported commands, with the options this reader interprets:

    create_clock        -name <name> -period <value> [-waveform <spec>] [-add] <objects>
    set_input_delay     -clock <clock> [-max|-min] <value> <objects>
    set_output_delay    -clock <clock> [-max|-min] <value> <objects>

Everything else - `set_false_path`, `set_multicycle_path`, `set_clock_uncertainty`, `if`, variable
assignment, `if`/`else` blocks - is reported `unsupported`. An option outside a supported command's list
is likewise `unsupported` rather than ignored, because an option that changes the meaning of a
constraint cannot be dropped without misrepresenting it.

## What it does not do

No analysis, no root cause, no recommendation, no repair, no patch, no LLM, no EDA tool, no memory, no
dispatch, no file writing. A constraint read here is an OBSERVATION of what a file says, with
provenance - exactly the kind of thing `VlsiFinding` will later describe, and nothing more.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .provenance import Provenance, _errors_to_raise, _reject_unknown_keys, _require_str
from .sdc import CONSTRAINT_KINDS, CreateClock, InputDelay, OutputDelay

#: Commands this reader supports. Anything else in the file is reported, not ignored.
SUPPORTED_COMMANDS = ("create_clock", "set_input_delay", "set_output_delay")

#: Options interpreted per supported command. An option outside its command's list is `unsupported`.
SUPPORTED_OPTIONS = {
    "create_clock": ("-name", "-period", "-waveform", "-add"),
    "set_input_delay": ("-clock", "-max", "-min"),
    "set_output_delay": ("-clock", "-max", "-min"),
}

#: Outcomes for a command that produced no constraint. The distinction is deliberate and load-bearing:
#: `unsupported` says "this reader does not cover that", `malformed` says "that is not valid syntax".
#: Collapsing them would report a file's limits as if they were the file's bugs.
ISSUE_KINDS = ("unsupported", "malformed")

#: Time units recognised as a suffix on a value. Used only to SPLIT a token into value and unit - never
#: to convert between them. A token with an unrecognised suffix keeps its whole text as the value.
TIME_UNITS = ("ps", "ns", "us", "ms", "s")

#: A numeric value, optionally with a unit suffix: `10`, `0.5`, `-2.0`, `10ns`, `500ps`, `1.2e-3ns`.
_VALUE_RE = re.compile(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-z]*)$")

#: Accessor words whose single-word form (`[all_inputs]`) names an object collection rather than a call.
#: Recorded for documentation; the rule below is positional and does not depend on this list.
_COLLECTION_ACCESSORS = ("all_inputs", "all_outputs", "all_clocks", "all_registers")


def _split_value_unit(token: str) -> tuple[str, str | None]:
    """Split `10ns` into `("10", "ns")`, and `10` into `("10", None)`.

    Both halves are returned as the source text, unchanged. No conversion happens, and a token whose
    suffix is not a recognised unit keeps its entire text as the value rather than being split on a
    guess.
    """
    match = _VALUE_RE.match(token)
    if match is None:
        return (token, None)
    number, suffix = match.group(1), match.group(2)
    if suffix and suffix.lower() in TIME_UNITS:
        return (number, suffix)
    return (token if suffix else number, None)


@dataclass(frozen=True)
class ParseIssue:
    """One command that produced no constraint, and why.

    An issue is a record about the FILE, not a finding about the design. It never claims a constraint is
    wrong - only that this reader did not turn it into one, or could not read it at all.
    """

    kind: str
    command: str
    message: str
    provenance: Provenance

    FIELDS = frozenset({"kind", "command", "message", "provenance"})

    @property
    def supported_command(self) -> bool:
        """Whether the command itself is in this reader's subset, as opposed to being foreign."""
        return self.command in SUPPORTED_COMMANDS

    def to_dict(self) -> dict:
        return {"kind": self.kind, "command": self.command, "message": self.message,
                "provenance": self.provenance.to_dict()}

    @classmethod
    def from_dict(cls, data: Any, label: str = "ParseIssue", errors: list[str] | None = None) -> "ParseIssue":
        errors = [] if errors is None else errors
        if not isinstance(data, dict):
            errors.append(f"{label}: expected object, got {type(data).__name__}")
            data = {}
        _reject_unknown_keys(data, cls.FIELDS, label, errors)
        kind = _require_str(data.get("kind"), f"{label}.kind", errors)
        if kind and kind not in ISSUE_KINDS:
            errors.append(f"{label}.kind: '{kind}' is not one of {list(ISSUE_KINDS)}")
        return cls(
            kind=kind,
            command=_require_str(data.get("command"), f"{label}.command", errors),
            message=_require_str(data.get("message"), f"{label}.message", errors),
            provenance=Provenance.from_dict(data.get("provenance"), f"{label}.provenance", errors),
        )


@dataclass(frozen=True)
class SdcParseResult:
    """What one SDC file stated: the constraints read, and everything that was not.

    `constraints` and `issues` are each in SOURCE order, so two runs over the same text produce equal
    results and a reader can map any entry back to a line. `malformed` and `unsupported` are separated
    because they mean different things to whoever acts on the result.
    """

    constraints: tuple[Any, ...] = ()
    issues: tuple[ParseIssue, ...] = ()

    @property
    def ok(self) -> bool:
        """True when nothing was malformed. Unsupported commands do not make a parse fail."""
        return not self.malformed

    @property
    def malformed(self) -> tuple[ParseIssue, ...]:
        return tuple(issue for issue in self.issues if issue.kind == "malformed")

    @property
    def unsupported(self) -> tuple[ParseIssue, ...]:
        return tuple(issue for issue in self.issues if issue.kind == "unsupported")

    def to_dict(self) -> dict:
        return {"constraints": [constraint.to_dict() for constraint in self.constraints],
                "issues": [issue.to_dict() for issue in self.issues]}

    @classmethod
    def from_dict(cls, data: Any, label: str = "SdcParseResult", errors: list[str] | None = None) -> "SdcParseResult":
        errors = [] if errors is None else errors
        if not isinstance(data, dict):
            errors.append(f"{label}: expected object, got {type(data).__name__}")
            data = {}
        _reject_unknown_keys(data, frozenset({"constraints", "issues"}), label, errors)

        raw_constraints = data.get("constraints", [])
        raw_issues = data.get("issues", [])
        for name, raw in (("constraints", raw_constraints), ("issues", raw_issues)):
            if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
                errors.append(f"{label}.{name}: expected array, got {type(raw).__name__}")

        from .sdc import constraint_from_dict

        constraints = []
        for index, item in enumerate(raw_constraints if isinstance(raw_constraints, (list, tuple)) else []):
            parsed = constraint_from_dict(item, f"{label}.constraints[{index}]", errors)
            if parsed is not None:
                constraints.append(parsed)

        issues = []
        for index, item in enumerate(raw_issues if isinstance(raw_issues, (list, tuple)) else []):
            issues.append(ParseIssue.from_dict(item, f"{label}.issues[{index}]", errors))
        return cls(constraints=tuple(constraints), issues=tuple(issues))

    @classmethod
    def parse(cls, data: Any, label: str = "SdcParseResult") -> "SdcParseResult":
        errors: list[str] = []
        value = cls.from_dict(data, label, errors)
        _errors_to_raise(errors, label)
        return value


@dataclass
class _Command:
    """One logical command located in the source. Internal; never leaves the parser."""

    name: str
    words: tuple[str, ...]
    line: int


def _scan_logical_commands(text: str) -> tuple[list[_Command], list[tuple[int, str, str]]]:
    """Split source into logical commands, honouring braces, brackets, quotes, comments and `\` continuations.

    Returns the commands plus a list of `(line, command_name, message)` for input the scanner could not
    delimit - an unterminated brace or quote is reported, never guessed at.

    A logical command spans lines when a brace or bracket is still open, or when a line ends with a
    backslash. Its recorded line is where it STARTS, so provenance points at the command rather than at
    wherever the reader happened to stop.
    """
    commands: list[_Command] = []
    errors: list[tuple[int, str, str]] = []

    buffer: list[str] = []
    start_line = 1
    line = 1
    brace_depth = 0
    bracket_depth = 0
    in_quote = False
    in_comment = False
    index = 0
    length = len(text)

    def flush() -> None:
        nonlocal buffer, brace_depth, bracket_depth, in_quote
        segment = "".join(buffer).strip()
        buffer = []
        brace_depth = bracket_depth = 0
        in_quote = False
        if not segment:
            return
        words = _tokenize(segment)
        if not words:
            return
        commands.append(_Command(name=words[0], words=tuple(words[1:]), line=start_line))

    while index < length:
        char = text[index]

        if char == "\n":
            line += 1
            in_comment = False
            if brace_depth == 0 and bracket_depth == 0 and not in_quote:
                # A trailing backslash continues the command onto the next line; anything else ends it.
                stripped = "".join(buffer).rstrip()
                if stripped.endswith("\\"):
                    buffer = [stripped[:-1]]
                    index += 1
                    continue
                flush()
                start_line = line
            else:
                buffer.append(char)
            index += 1
            continue

        # A comment runs to end of line and is literal inside braces, brackets and quotes.
        if char == "#" and not in_quote and brace_depth == 0 and bracket_depth == 0:
            in_comment = True
            index += 1
            continue
        if in_comment:
            index += 1
            continue

        if char == "\\" and index + 1 < length:
            nxt = text[index + 1]
            if nxt == "\n":
                # Line continuation: the newline is consumed, the line counter still advances.
                line += 1
                index += 2
                continue
            buffer.append(char)
            buffer.append(nxt)
            index += 2
            continue

        if char == "{" and not in_quote:
            brace_depth += 1
        elif char == "}" and not in_quote:
            brace_depth = max(0, brace_depth - 1)
        elif char == "[" and not in_quote:
            bracket_depth += 1
        elif char == "]" and not in_quote:
            bracket_depth = max(0, bracket_depth - 1)
        elif char == '"':
            in_quote = not in_quote

        buffer.append(char)
        index += 1

    if brace_depth or bracket_depth or in_quote:
        # Report the unterminated group and DISCARD the trailing partial command. Emitting a constraint
        # from text whose grouping was never closed would mean the reader closed the group by guesswork,
        # and the resulting constraint would look as trustworthy as a properly delimited one.
        errors.append((start_line, "", "unterminated brace, bracket or quote"))
        return commands, errors

    if buffer and "".join(buffer).strip():
        flush()
    return commands, errors


def _tokenize(segment: str) -> list[str]:
    """Split one logical command into words, honouring Tcl-style grouping.

    Three grouping forms are handled, and only these:

      - `{...}`  braces: literal contents, no substitution, whitespace preserved
      - `"..."`  quotes: contents with backslash escapes resolved
      - `[...]`  command substitution: tokenised recursively, and flattened to its ARGUMENTS

    This is not a Tcl implementation. `if`, `expr`, variables and `foreach` are outside the subset; a
    command containing them is reported unsupported rather than interpreted. An unterminated group is
    read to the end of the segment rather than being closed by guesswork - the scanner has already
    reported the unterminated group as a malformed issue, so the words here are only used if it did not.
    """
    words: list[str] = []
    index = 0
    length = len(segment)

    def push(word: str) -> None:
        if word != "":
            words.append(word)

    while index < length:
        char = segment[index]
        if char.isspace():
            index += 1
            continue

        if char == "{":
            depth = 0
            index += 1
            content_start = index
            closed = False
            while index < length:
                if segment[index] == "{":
                    depth += 1
                elif segment[index] == "}":
                    if depth == 0:
                        index += 1
                        closed = True
                        break
                    depth -= 1
                index += 1
            push(segment[content_start:index - 1 if closed else index])
            continue

        if char == "[":
            depth = 0
            index += 1
            content_start = index
            closed = False
            while index < length:
                if segment[index] == "[":
                    depth += 1
                elif segment[index] == "]":
                    if depth == 0:
                        index += 1
                        closed = True
                        break
                    depth -= 1
                index += 1
            inner = segment[content_start:index - 1 if closed else index]
            for word in _substitution_arguments(inner):
                push(word)
            continue

        if char == '"':
            index += 1
            buffer: list[str] = []
            while index < length and segment[index] != '"':
                if segment[index] == "\\" and index + 1 < length:
                    index += 1
                buffer.append(segment[index])
                index += 1
            index += 1
            push("".join(buffer).strip())
            continue

        buffer = []
        while index < length and not segment[index].isspace():
            if segment[index] == "\\" and index + 1 < length:
                index += 1
            buffer.append(segment[index])
            index += 1
        push("".join(buffer))

    return words


def _substitution_arguments(inner: str) -> list[str]:
    """The object names a `[...]` substitution names.

    `[get_ports clk]` -> `["clk"]`, `[get_ports {a b}]` -> `["a", "b"]`.

    The rule is positional and stated rather than clever: the first inner word is the accessor and is
    dropped, and the rest are the names. The one exception is a single-word accessor such as
    `[all_inputs]`, where dropping would leave nothing - that word IS the collection, so it is kept.

    Braced content is split on whitespace, because a Tcl list inside braces yields its elements rather
    than one string. A hypothetical object name containing a space would therefore be over-split; that
    is documented rather than handled, because inventing a quoting rule for it would be guessing.

    Nothing here evaluates the design: the parser records the names a file states, not the objects that
    exist. `[get_ports clk]` says `clk`; whether `clk` is a real port is VLSI-1B's question.
    """
    words = _tokenize(inner)
    if not words:
        return []
    arguments = words[1:]
    if not arguments:
        # `[all_inputs]` and friends: a single word that names a collection, not a call with arguments.
        return [words[0]]
    flattened: list[str] = []
    for argument in arguments:
        flattened.extend(argument.split())
    return flattened


def _provenance(ref: str, line: int) -> Provenance:
    return Provenance(source_type="file", ref=ref, line=line)


def _issue(kind: str, command: str, message: str, ref: str, line: int) -> ParseIssue:
    return ParseIssue(kind=kind, command=command, message=message, provenance=_provenance(ref, line))


def _unsupported_option(command: str, option: str, ref: str, line: int) -> ParseIssue:
    return _issue("unsupported", command,
                  f"option '{option}' is not supported for {command} by this reader", ref, line)


def _parse_create_clock(words: tuple[str, ...], ref: str, line: int):
    name: str | None = None
    period: str | None = None
    period_unit: str | None = None
    waveform: str | None = None
    objects: list[str] = []
    seen_period = False

    index = 0
    while index < len(words):
        word = words[index]
        if word.startswith("-"):
            option = word
            if option not in SUPPORTED_OPTIONS["create_clock"]:
                return (None, _unsupported_option("create_clock", option, ref, line))
            if option == "-add":
                index += 1
                continue
            if index + 1 >= len(words):
                return (None, _issue("malformed", "create_clock",
                                     f"option '{option}' has no value", ref, line))
            value = words[index + 1]
            if option == "-name":
                name = value
            elif option == "-period":
                period, period_unit = _split_value_unit(value)
                seen_period = True
            elif option == "-waveform":
                waveform = value
            index += 2
            continue
        objects.append(word)
        index += 1

    if not seen_period:
        return (None, _issue("malformed", "create_clock", "missing required option '-period'", ref, line))
    if period is None or not period.strip():
        return (None, _issue("malformed", "create_clock", "'-period' has no value", ref, line))
    return (CreateClock(name=name, period=period, unit=period_unit, waveform=waveform,
                        objects=tuple(sorted(set(objects))), provenance=_provenance(ref, line)),
            None)


def _parse_delay(kind: str, words: tuple[str, ...], ref: str, line: int):
    clock: str | None = None
    value: str | None = None
    unit: str | None = None
    qualifier: str | None = None
    objects: list[str] = []
    seen_value = False

    index = 0
    while index < len(words):
        word = words[index]
        if word.startswith("-"):
            option = word
            if option not in SUPPORTED_OPTIONS[kind]:
                return (None, _unsupported_option(kind, option, ref, line))
            if option == "-max" or option == "-min":
                # A qualifier takes no value of its own; it is a flag on the following value.
                qualifier = option[1:]
                index += 1
                continue
            if index + 1 >= len(words):
                return (None, _issue("malformed", kind,
                                     f"option '{option}' has no value", ref, line))
            if option == "-clock":
                clock = words[index + 1]
            index += 2
            continue
        if not seen_value:
            # SDC delay values are numeric. A positional token that is not a number is not a value, and
            # treating it as one would silently read `set_input_delay -clock c [get_ports d]` as a delay
            # of "d" - a malformed file turned into a valid-looking constraint. So the value must look
            # like a value; otherwise the command is malformed and says so.
            if _VALUE_RE.match(word) is None:
                return (None, _issue("malformed", kind,
                                     "missing a delay value before the target objects", ref, line))
            value, unit = _split_value_unit(word)
            seen_value = True
            index += 1
            continue
        objects.append(word)
        index += 1

    if clock is None:
        return (None, _issue("malformed", kind, "missing required option '-clock'", ref, line))
    if not seen_value or value is None or not value.strip():
        return (None, _issue("malformed", kind, "missing a delay value", ref, line))
    constraint_type = InputDelay if kind == "set_input_delay" else OutputDelay
    return (constraint_type(clock=clock, value=value, unit=unit, qualifier=qualifier,
                            objects=tuple(sorted(set(objects))), provenance=_provenance(ref, line)),
            None)


def parse_sdc(text: str, *, ref: str) -> SdcParseResult:
    """Read SDC text into constraints and issues.

    `ref` is the repository-relative reference recorded in every provenance; the reader does not open the
    file, so the caller supplies the identity it wants attached.

    Constraints and issues both come back in source order. A command this reader does not support is
    reported as `unsupported`; a supported command whose syntax is broken is reported as `malformed`.
    Neither is dropped.
    """
    commands, scan_errors = _scan_logical_commands(text)

    constraints: list[Any] = []
    issues: list[ParseIssue] = []

    for line, name, message in scan_errors:
        issues.append(_issue("malformed", name, message, ref, line))

    for command in commands:
        if command.name not in SUPPORTED_COMMANDS:
            issues.append(_issue("unsupported", command.name,
                                 f"command '{command.name}' is outside this reader's subset "
                                 f"{list(SUPPORTED_COMMANDS)}", ref, command.line))
            continue
        if command.name == "create_clock":
            constraint, issue = _parse_create_clock(command.words, ref, command.line)
        else:
            constraint, issue = _parse_delay(command.name, command.words, ref, command.line)
        if constraint is not None:
            constraints.append(constraint)
        if issue is not None:
            issues.append(issue)

    return SdcParseResult(constraints=tuple(constraints), issues=tuple(issues))