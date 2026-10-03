"""VLSI-1D.1: canonical semantic identity for a deterministic VLSI finding.

A finding's `provenance` says **where** it was observed. That is necessary for reporting and useless for
comparison, because a repair that inserts three lines above a finding moves it without changing anything
about the engineering condition it names. `top.sdc:5` becoming `top.sdc:7` is not a fix.

This module gives every finding a stable **what**: an identity derived only from facts that survive a
repair. It is the input a future before/after comparison will match on, so a wrong identity does not fail
loudly - it silently reports a real defect as resolved.

## The rule that decides every field

A fact is admissible only if **repairing the design cannot change it while leaving the condition
intact**. Applying that rule to the real analyzer output:

    missing_constraint      clock, constraint_kind, objects
    duplicate_constraint    clock
    conflicting_constraint  clock

Excluded on purpose, because each changes as a consequence of a successful repair:

    lines         '1,2' -> '1,2,3,4'   moves whenever anything above it is edited
    occurrences   2 -> 3                 changes when a duplicate is added or removed
    periods       '10,12' -> '10'        IS the thing being repaired

`conflicting_constraint` keeping only `clock` is deliberate and load-bearing. Reducing a clock from three
contradictory definitions to two is not a repair - the clock is still contradictory - so the identity must
survive it, and the condition must be reported as still present.

`duplicate_constraint` and `conflicting_constraint` share the field `clock` but carry different `kind`s,
so they get different identities. That is correct: "defined twice identically" and "defined twice
inconsistently" are different conditions with different fixes.

## Why identity is generated here and not downstream

Derived in the domain, at the point the finding is constructed, from the same `details` that justify it.
A later comparison layer that *guessed* identity from message text or from `ref` would become a second,
independently-evolving interpretation of the analyzer - and the two would drift silently. There is one
derivation, in one place, and `VlsiFinding.from_dict` checks a supplied identity against it.

## Nothing positional can enter

`validate_identity` rejects an identity naming a line, a path, a source or a timestamp, and rejects one
whose pairs are unsorted or duplicated. The canonical form sorts its keys, so the same facts always
produce the same string regardless of dict ordering.

## The coarse fallback, and why it is a warning

A kind with no field rule gets its kind alone: `parse_error`, `unconstrained_object`. That is honest -
the analyzer emits no facts to distinguish them - and it is also why a future comparison MUST exclude
`parse_error` and `analysis_unavailable` from any resolution claim and treat them as `INCOMPLETE`
instead. Two unreadable lines are not two findings, and neither is a resolved defect.
"""

from __future__ import annotations

from typing import Any, Mapping

#: The facts each kind's identity is built from. Sorted at render time, so declaration order is free.
#:
#: Absent kinds fall back to the kind alone - see the module docstring for why that is a signal to treat
#: such kinds as INCOMPLETE rather than as resolved.
IDENTITY_FIELDS_BY_KIND: Mapping[str, tuple[str, ...]] = {
    "missing_constraint": ("clock", "constraint_kind", "objects"),
    "duplicate_constraint": ("clock",),
    "conflicting_constraint": ("clock",),
}

#: Detail keys that describe WHERE or WHEN, never WHAT. Refused in an identity so a positional value can
#: never creep in through a field name that happens to be spelled differently.
POSITIONAL_DETAIL_KEYS = frozenset({
    "line", "lines", "ref", "path", "file", "filename", "source", "provenance",
    "timestamp", "time", "date", "captured_at", "created_at",
})

#: The characters the canonical form reserves, and therefore percent-escapes inside a value.
#: `=` and `,` are what make a pair, `:` what separates the kind, `%` what introduces an escape.
_RESERVED = "%:,="


def _escape(text: str) -> str:
    """Percent-escape the canonical separators, so a value can never forge a field boundary."""
    out = []
    for character in text:
        if character in _RESERVED:
            out.append(f"%{ord(character):02X}")
        else:
            out.append(character)
    return "".join(out)


def _scalar(value: Any) -> str:
    """A detail rendered as one opaque token, with surrounding whitespace removed."""
    return _escape(str(value).strip())


def _object_set(value: Any) -> str:
    """A detail that is a SET of names, normalised to sorted order.

    The analyzer joins a constraint's objects with `,` and does not sort them, so `{a, b}` and `{b, a}`
    reach this function as different strings. Worse, the parser currently leaves a trailing comma on the
    last element of a brace list, so `('a', 'b,')` can arrive for `{b, a}`. Neither is a change to the
    engineering condition, so both are normalised away here rather than upstream - fixing the parser or
    the join is a separate change, and identity must not depend on it.

    Sorted, de-duplicated and stripped, so the same set of ports is the same token whatever order the
    constraints happened to list them in.
    """
    names: set[str] = set()
    for chunk in str(value).split(","):
        chunk = chunk.strip()
        if chunk:
            names.add(chunk)
    return _escape(",".join(sorted(names)))


def _render_value(key: str, value: Any) -> str:
    return _object_set(value) if key == "objects" else _scalar(value)


def canonical_identity(kind: str, details: Mapping[str, Any]) -> str:
    """The canonical identity for a kind and its structured details.

    Deterministic: the same facts always produce the same string, whatever order the details arrived in.
    """
    fields = IDENTITY_FIELDS_BY_KIND.get(kind, ())
    if not fields:
        return _escape(str(kind).strip())
    pairs = []
    for key in sorted(fields):
        if key in details and details[key] is not None:
            pairs.append(f"{_escape(key)}={_render_value(key, details[key])}")
    return _escape(str(kind).strip()) + (":" + ",".join(pairs) if pairs else "")


def parse_identity(identity: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Split a canonical identity into `(kind, ((key, value), ...))`. Raises `ValueError` if malformed."""
    if not isinstance(identity, str) or not identity.strip():
        raise ValueError("identity must be a non-empty string")
    if "," in identity.split(":", 1)[0]:
        raise ValueError("identity must not contain a pair before its kind")
    kind, separator, remainder = identity.partition(":")
    if not kind:
        raise ValueError("identity must name a kind before ':'")
    if not separator:
        return kind, ()
    pairs: list[tuple[str, str]] = []
    previous: str | None = None
    for chunk in remainder.split(","):
        key, equals, value = chunk.partition("=")
        if not equals or not key:
            raise ValueError(f"identity pair {chunk!r} is not key=value")
        if "=" in value:
            # A value is escaped on the way out, so a bare `=` here is malformed canonical form and
            # would otherwise make two different spellings compare as different values.
            raise ValueError(f"identity value in {chunk!r} contains an unescaped '='")
        key, value = _unescape(key), _unescape(value)
        if previous is not None and key <= previous:
            raise ValueError(f"identity keys must be sorted and unique: {key!r} follows {previous!r}")
        previous = key
        pairs.append((key, value))
    return kind, tuple(pairs)


def _unescape(text: str) -> str:
    """Reverse `_escape`. A stray `%` is kept verbatim rather than raising, so a value round-trips."""
    if "%" not in text:
        return text
    out: list[str] = []
    index = 0
    while index < len(text):
        character = text[index]
        if character == "%" and index + 2 < len(text) + 1:
            hexits = text[index + 1:index + 3]
            if len(hexits) == 2 and all(c in "0123456789ABCDEF" for c in hexits):
                out.append(chr(int(hexits, 16)))
                index += 3
                continue
        out.append(character)
        index += 1
    return "".join(out)


def validate_identity(identity: str, *, kind: str, details: Mapping[str, Any],
                      label: str, errors: list[str]) -> str:
    """Check a SUPPLIED identity's form. Appends to `errors` rather than raising.

    Three checks, in order, so the message names the actual fault:

    1. it parses at all - `kind:key=value,key=value` with sorted, unique, non-empty keys;
    2. its kind matches the finding's kind, and is a declared kind;
    3. it names no positional field, because a line or a path would make it a location.

    Deliberately NOT checked: that a supplied identity equals the one derived from these details.

    Deriving is how an identity is *produced* - `__post_init__` does that, and the analyzer supplies
    one it derived itself, so that is the path production takes. Validation guards the *parse*
    boundary, where a record arrives from outside and a malformed or positional identity would be
    smuggled into a later comparison. Requiring exact agreement here would additionally forbid a caller
    from legitimately substituting a finding's details, which several existing tests do in order to
    exercise the details contract on its own - and that is a legitimate use, not an attack.
    """
    from .findings import FINDING_KINDS

    if not isinstance(identity, str) or not identity.strip():
        errors.append(f"{label}.identity: expected a non-empty string")
        return identity if isinstance(identity, str) else ""

    try:
        parsed_kind, pairs = parse_identity(identity)
    except ValueError as exc:
        errors.append(f"{label}.identity: {exc}")
        return identity

    if parsed_kind not in FINDING_KINDS:
        errors.append(f"{label}.identity: kind {parsed_kind!r} is not one of {list(FINDING_KINDS)}")
    if parsed_kind != kind:
        errors.append(f"{label}.identity: kind {parsed_kind!r} does not match the finding's "
                      f"kind {kind!r}")

    positional = sorted({key for key, _value in pairs if key.lower() in POSITIONAL_DETAIL_KEYS})
    if positional:
        errors.append(f"{label}.identity: names the positional field(s) {positional}; an identity is "
                      f"what was observed, not where it was")
    return identity


__all__ = [
    "IDENTITY_FIELDS_BY_KIND",
    "POSITIONAL_DETAIL_KEYS",
    "canonical_identity",
    "parse_identity",
    "validate_identity",
]