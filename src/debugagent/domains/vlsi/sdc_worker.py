"""VLSI-1C: the deterministic SDC analysis a worker injects, behind a domain-neutral worker.

VLSI-1A parsed SDC into typed constraints and VLSI-1B analysed them for consistency. Both are
complete, tested, and used by nothing: `investigate()` runs the Memory Specialist and ignores every
other worker. This module is the seam that lets a worker reach them without either of those modules
learning that a worker exists.

## Why this file exists at all, given how thin it looks

The obvious alternative is for the worker to import `parse_sdc` and `analyze_sdc_constraints` itself.
That would work, and it would put a `debugagent.domains.vlsi` import inside `agents/` - the first
breach of the property the whole architecture rests on, that the core does not know what a domain is.
Nothing currently tests for that property outside `application/`, so the breach would be silent.

So the division of labour is:

    agents/sdc_analyzer_worker.py   generic worker. Task shape, authorisation, refusals, the
                                    success/partial/failed decision, and the Artifact mapping.
                                    Imports no domain module; takes `analyze` as a callable.
              |
              | injected at the composition root
              v
    domains/vlsi/sdc_worker.py      this file. Text in, typed findings out, plus the one mapping
                                    that is genuinely a domain decision: an SDC file is a `code`
                                    artifact, not a `log` and not `memory`.
              |
              v
    domains/vlsi/sdc_parser.py      VLSI-1A
    domains/vlsi/sdc_analyzer.py    VLSI-1B

`composition.py` holds the only `domains.vlsi` reference in the wiring, which is the same place it
already builds `FileSourcePort` and the two P6 workers. VLSI-2's STA analysis then arrives as a second
analyser behind this same generic worker rather than a fifth roster entry, which is what roadmap
section 2.3 asks for: a small number of workers with wide capability.

## The one place typed structure becomes text

`SdcAnalysisResult` is typed - a `VlsiFinding` carries `kind`, `severity`, `message`, `Provenance` and
`details`. An `Artifact` is not: it is exactly `kind`, `ref`, `content`, with `source` derived as
`ARTIFACT_SOURCE_PREFIX[kind] + ref`. So the finding's domain `kind` and its `severity` have no
structural home once the record crosses into the generic worker layer, and they must be written into
`content` as text or they are lost.

They are written into text deliberately and legibly rather than dropped:

    SDC critical: Clock 'core_clk' is defined 2 times with differing periods or targets.
                  [conflicting_constraint] at constraints/top.sdc:18

This is a real loss of type information and it is the accepted cost of one generic worker serving
several domains. It is bounded in three ways: the analyser still returns a typed result at this
boundary; the *location* stays structural in `ref` rather than being flattened into prose; and the
crossing happens at OBSERVATION trust, where a reader is entitled to a sentence rather than a
verdict. A number that lost its provenance would be dangerous here. A sentence that lost a severity
label is merely less well organised.

## What this module must never do

- Accept a file path. It takes text and a `ref`; opening the file is the worker's job, through the
  `SourcePort` it was given, so the repository boundary stays in one place.
- Invent a location. An unknown line stays unknown. The foundation milestone asserts this for
  `Provenance` and re-inventing a plausible line here would quietly undo it downstream, where nothing
  could see it happen.
- Reach an LLM, a tool, a socket, a subprocess or a clock. Everything below is a pure function of its
  arguments, which is what makes the worker's output reproducible and what makes it evidence-side.
- Decide anything. These are observations about a constraint file. What they mean is the engineer's
  call, and the generic worker is what keeps that boundary from being crossed.
"""

from __future__ import annotations

from dataclasses import dataclass

from debugagent.domains.vlsi.sdc_analyzer import SdcAnalysisResult, analyze_sdc_constraints
from debugagent.domains.vlsi.sdc_parser import parse_sdc

#: An SDC file is a repository source file, so it maps to the generic `code` artifact kind. This is a
#: domain decision and it is the only one: `Artifact.source` is derived by the generic layer as
#: `ARTIFACT_SOURCE_PREFIX[kind] + ref`, so nothing here builds a `code:` string, and
#: `Artifact.from_dict` rejects a `ref` that already carries the prefix.
SDC_ARTIFACT_KIND = "code"

#: Findings whose provenance carries no usable location report this instead of a guessed one.
UNKNOWN_LOCATION = "unknown location"


@dataclass(frozen=True)
class SdcObservation:
    """One analysis result flattened onto the generic worker's three artifact fields.

    `ref` and `content` map straight onto `Artifact.ref` / `Artifact.content`. The artifact `kind` is
    not a field here because it is a constant of this domain - `SDC_ARTIFACT_KIND` - and repeating it
    per observation would invite one caller to disagree with another.
    """

    ref: str
    content: str
    severity: str
    finding_kind: str

    def as_artifact_kwargs(self) -> dict[str, str]:
        """The exact `Artifact` keyword arguments this observation becomes."""
        return {"kind": SDC_ARTIFACT_KIND, "ref": self.ref, "content": self.content}


@dataclass(frozen=True)
class SdcAnalysis:
    """Everything the injected analyser hands back, and nothing more.

    `parsed_constraints` exists so a caller can tell "this file was read and is consistent" from "this
    file yielded no constraints at all", which is what happens when something that is not a constraint
    file is handed to the parser. It is the answer to a question only this domain can answer, and it is
    carried as a count rather than inferred by the caller from the observations - a caller that tried to
    infer it would be guessing from prose, and a caller that sniffed file extensions instead would be
    encoding a naming convention that no amount of evidence can confirm.
    """

    parsed_constraints: int
    result: SdcAnalysisResult
    observations: tuple[SdcObservation, ...]

    @property
    def recognised(self) -> bool:
        """Whether the parser understood the file as SDC at all.

        A non-empty constraint list is the only positive evidence of that. Zero constraints with zero
        issues is an empty file, which is a legitimate clean run; zero constraints with issues is a
        file that was not SDC, or an SDC file made entirely of commands this parser does not yet
        support - and those two deserve different answers, which is why both the count and the issues
        are exposed rather than a single boolean.
        """
        return self.parsed_constraints > 0


NO_CONSTRAINTS_KIND = "analysis_unavailable"


def no_constraints_observation(ref: str) -> SdcObservation:
    """Says outright that nothing was recognised, when nothing was.

    Emitted only when the parser produced no constraints at all. Without it, a file that is not a
    constraint file produces a bare list of parse issues, and a reader has to infer from a wall of
    parser complaints that the real answer is "this was never understood" rather than "this file has
    these specific problems". That inference is exactly the one a reader should not have to make.

    The severity is `critical` because it outranks everything else a run can report: every other
    observation in the same result is about a file this one says was not readable as SDC. The
    `analysis_unavailable` kind is the domain's own vocabulary for precisely this - evidence could not
    be produced here - rather than a new kind invented for the benefit of a display.
    """
    return SdcObservation(
        ref=ref,
        content=(f"SDC critical: no SDC constraints were recognised in {ref}, so nothing in it was "
                 f"analysed. The observations below are parser complaints, not engineering findings. "
                 f"[{NO_CONSTRAINTS_KIND}] at {ref} ({UNKNOWN_LOCATION})"),
        severity="critical",
        finding_kind=NO_CONSTRAINTS_KIND,
    )


def analyze_sdc_text(text: str, *, ref: str) -> SdcAnalysis:
    """Parse and analyse constraint text, deterministically.

    `ref` is the repository-relative identity recorded on every piece of provenance; the caller
    supplies it, so this function never learns a path and cannot open one.

    The parse and analyse steps are kept as the two separate VLSI-1A/VLSI-1B calls rather than being
    collapsed, because the parse/analyse boundary is structural on purpose: `analyze_sdc_constraints`
    takes a `SdcParseResult` and has no path by which it could re-tokenise a command and disagree with
    the parser about what the file said.
    """
    parsed = parse_sdc(text, ref=ref)
    result = analyze_sdc_constraints(parsed)
    observations = observations_for(result)
    if not parsed.constraints:
        # Prepended, not appended: it governs the ones below it, and a caveat that arrives after the
        # claims it qualifies is read as another claim.
        observations = (no_constraints_observation(ref),) + observations
    return SdcAnalysis(parsed_constraints=len(parsed.constraints), result=result,
                       observations=observations)


def _location(ref: str, line: int | None) -> str:
    """`path:line` when the line is known, `path` alone when it is not.

    There is deliberately no third option. An absent line is not a line 1, not a line 0 and not the
    start of the file; it is an absence, and rendering it as a location would let a reader believe the
    constraint sits somewhere specific when the analysis does not know.
    """
    return f"{ref}:{line}" if line is not None else ref


def _at(ref: str, line: int | None) -> str:
    return f" at {ref}:{line}" if line is not None else f" at {ref} ({UNKNOWN_LOCATION})"


def observation_for(finding) -> SdcObservation:
    """One `VlsiFinding` as an observation.

    Severity and the finding kind are written into the text because `Artifact` has nowhere else to
    put them. They are bracketed so a reader can tell which words are the analysis's classification
    and which are its explanation, rather than having to guess where the message ends.
    """
    provenance = finding.provenance
    return SdcObservation(
        ref=_location(provenance.ref, provenance.line),
        content=f"SDC {finding.severity}: {finding.message} [{finding.kind}]"
                f"{_at(provenance.ref, provenance.line)}",
        severity=finding.severity,
        finding_kind=finding.kind,
    )


def observation_for_parse_issue(issue) -> SdcObservation:
    """One `ParseIssue` as an observation.

    A parser issue is reported as an observation in its own right and not folded into the findings,
    because it means something different: a finding says the constraints are inconsistent, while a
    parse issue says a line of the file was not understood. Roadmap section 2.4 is explicit that tool
    output which fails to parse is a finding *about the tool run*, and a consumer that cannot tell the
    two apart will read a half-read file as a clean one.

    The kind is `parse_error` and the severity is `warning` regardless of the issue's own severity
    field: a parse issue is a statement about legibility, and letting an issue escalate to `critical`
    would let a truncated file read as an urgent engineering finding.
    """
    provenance = issue.provenance
    return SdcObservation(
        ref=_location(provenance.ref, provenance.line),
        content=f"SDC PARSE {issue.kind}: {issue.message}{_at(provenance.ref, provenance.line)}",
        severity="warning",
        finding_kind="parse_error",
    )


def observations_for(result: SdcAnalysisResult) -> tuple[SdcObservation, ...]:
    """Every observation a result carries, analysis findings first.

    Ordering is deterministic because both inputs are: `SdcAnalysisResult.findings` and
    `parser_issues` are each in source order, so two runs over the same text produce an equal tuple
    and a worker that reported them in a different order each time would be indistinguishable from one
    that is not reproducible.

    Parse issues come last rather than interleaved. The findings are the answer to the question that
    was asked; the issues are the caveat on how far that answer can be trusted, and a caveat reads
    better after the claim it qualifies than before it.
    """
    return tuple(observation_for(finding) for finding in result.findings) + tuple(
        observation_for_parse_issue(issue) for issue in result.parser_issues
    )