"""VLSI-1D.2: deterministic comparison of two SDC analyses, by semantic finding identity.

The question this answers is narrow and important: **did the repair resolve the condition, or only move
it?** Everything else about a repair - whether it was a good idea, whether an engineer liked it - is out
of scope. This is a set operation over typed domain results, and it decides nothing on its own.

## What it consumes, and what it deliberately cannot see

Inputs are `SdcAnalysisResult` objects holding typed `VlsiFinding`s. It reads `VlsiFinding.identity` and
`provenance`, and nothing else.

It cannot read a patch, a diff, a message, or a score, because none of those reach it. The finding text is
never parsed; the generic `Artifact`/`RankedFinding` stream is never touched; the view model is never
consulted. That is deliberate rather than incidental: a comparison that read the rendered sentence would
break the moment the wording changed, and would be comparing prose rather than engineering conditions.

## Line numbers are not identity

`before` may report a condition at `top.sdc:3` and `after` at `top.sdc:7`, because inserting lines above a
defect moves it without changing anything. VLSI-1D.1 made identity independent of position for exactly
this reason, and this module trusts that: it never reads `provenance.line`, and the before/after
locations it reports are for a human to read, never for matching.

## INCOMPLETE overrides everything

The failure this must never have:

    after analysis fails  ->  after findings = []  ->  before - after = everything resolved

An analysis that could not read the file produces no findings, and an empty set is indistinguishable from
a clean one unless you check WHY it is empty. So an `after` that is absent, incomplete, or that carries
an `analysis_unavailable` finding yields `incomplete` overall, and **no baseline finding may be reported
resolved** - each is reported `incomplete` instead, so a reader can see which conditions went unchecked.

This is the same reasoning that keeps recalled memory from satisfying an evidence gap: absence of data is
not data.

## Four outcomes, never collapsed

    resolved        identity was in before, absent from a COMPLETE after
    still_present   identity was in before and is in after
    new             identity was absent from before and is in after
    incomplete      after could not be established, so the question was not answered

A caller that wants a single yes/no must decide what `still_present`, `new` and `incomplete` each mean for
it. This module will not do that collapsing, because the three answer different questions and the whole
point of the negative control is that they must not be flattened into one another.

## Aggregate status

    incomplete      after could not be established
    not_applicable  there was no baseline finding to resolve
    findings        something remains, or something new appeared
    verified        every baseline identity is gone from a complete `after`, and nothing new appeared

`verified` requires all four conditions at once, so it cannot be reached while any baseline finding
remains, while any new finding appeared, or while the comparison is incomplete. An EMPTY baseline is
deliberately `not_applicable` rather than `verified`: nothing was found before, so nothing was repaired,
and reporting success there would claim a measurement that was never made.
"""

from __future__ import annotations

from dataclasses import dataclass

from debugagent.domains.vlsi.findings import VlsiFinding
from debugagent.domains.vlsi.sdc_analyzer import SdcAnalysisResult

#: Machine values, lowercase to match the domain's other vocabularies (`SdcAnalysisResult.status` is
#: `"complete"`/`"incomplete"`). A UI may render them in whatever case suits it; this module never
#: decides how they are presented, only what they mean.
COMPARISON_OUTCOMES = ("resolved", "still_present", "new", "incomplete")

COMPARISON_STATUSES = ("verified", "findings", "incomplete", "not_applicable")

#: A finding kind that means "evidence could not be produced here". Its presence in an `after` makes the
#: comparison incomplete regardless of how clean the rest looks - the analyser said out loud that it could
#: not finish, and an empty finding set beside that sentence is not a clean bill of health.
UNAVAILABLE_KIND = "analysis_unavailable"

#: Presentation order: baseline findings first, then newly appeared ones. Within each group, by identity.
_GROUP = {"resolved": 0, "still_present": 0, "incomplete": 0, "new": 1}


@dataclass(frozen=True)
class FindingComparison:
    """One identity's fate across the two analyses.

    `before_ref`/`after_ref` and the line numbers are carried for a human reading the result. They are
    NOT used for matching - two entries match only when their `identity` is equal - and they are allowed
    to differ for the same identity, which is the normal case after any repair that adds or removes lines.
    """

    outcome: str
    identity: str
    kind: str
    before_ref: str | None = None
    before_line: int | None = None
    after_ref: str | None = None
    after_line: int | None = None

    def to_dict(self) -> dict:
        record = {"outcome": self.outcome, "identity": self.identity, "kind": self.kind}
        for key, value in (("before_ref", self.before_ref), ("before_line", self.before_line),
                           ("after_ref", self.after_ref), ("after_line", self.after_line)):
            if value is not None:
                record[key] = value
        return record


@dataclass(frozen=True)
class RepairVerification:
    """The whole comparison, with its per-identity breakdown kept intact.

    Deliberately not a boolean. `status` is an aggregate for a caller that wants one, and `comparisons` is
    the evidence for it; `verified` cannot be reached while any entry says `still_present`, `new` or
    `incomplete`.
    """

    status: str
    comparisons: tuple[FindingComparison, ...] = ()
    before_total: int = 0
    after_total: int = 0
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "comparisons": [entry.to_dict() for entry in self.comparisons],
            "before_total": self.before_total,
            "after_total": self.after_total,
            "note": self.note,
        }

    def identities(self, outcome: str) -> tuple[str, ...]:
        """Every identity with a given outcome, in comparison order. Convenience for a caller."""
        return tuple(entry.identity for entry in self.comparisons if entry.outcome == outcome)

    @property
    def verified(self) -> bool:
        """Whether every baseline identity is gone and nothing new appeared.

        Derived from the entries rather than trusting `status`, so a caller cannot be misled by a status
        string that does not match the breakdown it sits on.
        """
        return (self.status == "verified"
                and bool(self.comparisons)
                and all(entry.outcome == "resolved" for entry in self.comparisons))


def _location(finding: VlsiFinding) -> tuple[str | None, int | None]:
    provenance = finding.provenance
    if provenance is None:
        return None, None
    return provenance.ref, provenance.line


def _index(findings: tuple[VlsiFinding, ...]) -> dict[str, VlsiFinding]:
    """Identity -> finding, first occurrence in source order.

    Two findings of one identity in a single analysis would be a defect in the analyser, not something
    to reconcile here; keeping the first is deterministic, which is what matters, and silently dropping
    one would hide it.
    """
    index: dict[str, VlsiFinding] = {}
    for finding in findings or ():
        index.setdefault(finding.identity, finding)
    return index


def _sorted(comparisons: list[FindingComparison]) -> tuple[FindingComparison, ...]:
    """The single place output order is defined: baseline-origin entries first, then new ones.

    Applied once at the end rather than relied upon emerging from the order entries happen to be
    appended in. Right now the two coincide - the body already walks each identity set sorted - so
    removing this call changes nothing observable (verified across every before/after pairing of the
    test corpus). It stays because stating the contract in one place survives an edit to the body,
    where an emergent ordering silently would not.
    """
    return tuple(sorted(comparisons, key=lambda entry: (_GROUP[entry.outcome], entry.identity)))


def _incomplete(baseline: dict[str, VlsiFinding], note: str, before_total: int, after_total: int
                ) -> RepairVerification:
    """The `after` could not be established, so nothing may be reported resolved.

    Every baseline identity is listed as `incomplete` rather than omitted, so a reader can see which
    conditions went unverified. Nothing is reported `new`: when the `after` set is not trustworthy, a
    claim that something is new is as unsafe as a claim that something was resolved.
    """
    entries = []
    for identity in sorted(baseline):
        before_ref, before_line = _location(baseline[identity])
        entries.append(FindingComparison(
            outcome="incomplete", identity=identity, kind=baseline[identity].kind,
            before_ref=before_ref, before_line=before_line))
    return RepairVerification(status="incomplete", comparisons=_sorted(entries),
                              before_total=before_total, after_total=after_total, note=note)


def compare_sdc_findings(before: SdcAnalysisResult | None,
                       after: SdcAnalysisResult | None) -> RepairVerification:
    """Compare two analyses of the same design by semantic finding identity.

    `before` and `after` are `SdcAnalysisResult`s. `after` may be `None`, meaning the re-analysis never
    produced a result at all; that is `incomplete`, not an empty finding set.

    The comparison is a pure function of its two arguments: same inputs, same output, including order.
    """
    baseline = before.findings if before is not None else ()
    before_index = _index(baseline)
    before_total = len(baseline)

    if after is None:
        return _incomplete(before_index, "the re-analysis produced no result",
                           before_total, 0)

    after_findings = after.findings
    after_index = _index(after_findings)
    after_total = len(after_findings)

    status = after.status
    if status != "complete":
        return _incomplete(before_index,
                           f"the re-analysis was {status or 'of unknown status'}: a finding set from an "
                           f"incomplete analysis cannot establish that anything was resolved",
                           before_total, after_total)

    if any(finding.kind == UNAVAILABLE_KIND for finding in after_findings):
        return _incomplete(before_index,
                           f"the re-analysis reported {UNAVAILABLE_KIND}: evidence could not be produced "
                           f"for at least part of the design",
                           before_total, after_total)

    if before is None or not baseline:
        # An empty baseline means there was no condition to resolve, so this cannot be a verified
        # repair. It is NOT an early return: findings that appeared in `after` are still reported,
        # because a change that INTRODUCED a problem is as worth knowing as one that removed one, and
        # returning early here would report such a change as silence.
        after_only = [identity for identity in sorted(after_index) if identity not in before_index]
        entries = [FindingComparison(
            outcome="new", identity=identity, kind=after_index[identity].kind,
            after_ref=_location(after_index[identity])[0],
            after_line=_location(after_index[identity])[1]) for identity in after_only]
        return RepairVerification(
            status="findings" if entries else "not_applicable",
            comparisons=_sorted(entries), before_total=0, after_total=after_total,
            note=("the earlier analysis reported no finding, so there was no condition to resolve; "
                  "findings present now were not present then"
                  if entries else
                  "the earlier analysis reported no finding, so there was no condition to resolve; "
                  "this is not evidence that a repair worked"))

    entries: list[FindingComparison] = []
    for identity in sorted(before_index):
        before_ref, before_line = _location(before_index[identity])
        if identity in after_index:
            after_ref, after_line = _location(after_index[identity])
            entries.append(FindingComparison(
                outcome="still_present", identity=identity,
                kind=after_index[identity].kind,
                before_ref=before_ref, before_line=before_line,
                after_ref=after_ref, after_line=after_line))
        else:
            entries.append(FindingComparison(
                outcome="resolved", identity=identity, kind=before_index[identity].kind,
                before_ref=before_ref, before_line=before_line))

    for identity in sorted(after_index):
        if identity not in before_index:
            after_ref, after_line = _location(after_index[identity])
            entries.append(FindingComparison(
                outcome="new", identity=identity, kind=after_index[identity].kind,
                after_ref=after_ref, after_line=after_line))

    ordered = _sorted(entries)
    unresolved = any(entry.outcome in ("still_present", "new") for entry in ordered)
    status = "findings" if unresolved else "verified"
    note = ""
    if unresolved:
        remaining = len([e for e in ordered if e.outcome == "still_present"])
        added = len([e for e in ordered if e.outcome == "new"])
        parts = []
        if remaining:
            parts.append(f"{remaining} still present")
        if added:
            parts.append(f"{added} newly reported")
        note = f"{' and '.join(parts)}; not a verified repair"
    return RepairVerification(status=status, comparisons=ordered,
                              before_total=before_total, after_total=after_total, note=note)


__all__ = [
    "COMPARISON_OUTCOMES",
    "COMPARISON_STATUSES",
    "FindingComparison",
    "RepairVerification",
    "compare_sdc_findings",
]