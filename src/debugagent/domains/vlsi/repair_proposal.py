"""VLSI-1D.4a: authoring a semantic SDC repair proposal.

`repair.py` (VLSI-1D.3b) defines what a repair IS and refuses to render one it cannot justify. This module
answers the question one step earlier: **where does the intent come from?**

    deterministic findings  ->  this module  ->  SdcRepair  ->  repair.py renders it

## Why this is a SEPARATE call, and not a field on the hypothesis schema

The worker stage runs BEFORE hypotheses are generated, and deliberately keeps its output out of the
proposal prompt - `build_prompt` receives only the case and the memory context, so no worker output
reaches it. That is an existing guarantee, and it is load-bearing: a hypothesis formed from recalled
cases and the reported issue must not be anchored to whatever a tool happened to report.

So the model cannot propose a repair inside `RESPONSE_SCHEMA`. At that point in the run it has never
been shown an SDC finding, and a `repair_proposals` field there would invite it to invent one. Adding
the findings to `build_prompt` instead would work and would quietly dissolve that guarantee.

A separate call keeps both properties: `hypothesize.py` is untouched, and the ONLY thing the repair
prompt receives is the deterministic findings - the same evidence the renderer will later demand.

## The prompt teaches the refusal

The rules below tell the model, explicitly, that **omitting a value it does not know is the correct
answer.** That is not politeness. A `period` the model supplies is an engineering claim it cannot
support from a finding; a `period` it omits becomes `missing_required_value` at render time, which is
visible and safe. Without that instruction a model asked to fill a schema tends to fill it, and the
result would be a syntactically perfect, physically meaningless constraint that no downstream check
could catch.

This mirrors `citation_check`: the model may only cite recalled case ids, so the same rule holds here -
it may only propose repairs for clocks the analysis actually reported.

## What leaves this module

`SdcRepair` records, or structured refusals naming why a proposal was not honoured. Nothing is
rendered, nothing is proposed to an engineer, nothing is written. Whether a repair is renderable is
`repair.py`'s question, and this module never answers it on its behalf - a proposal that omits a period
is a VALID `SdcRepair` here and an `RepairUnavailable` there, and collapsing those two would hide which
layer said no.

No LLM is imported: `llm` is injected, exactly as in `pipeline/hypothesize.py`. No filesystem, no writer,
no memory, no pipeline import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .findings import VlsiFinding
from .provenance import VlsiContractError
from .repair import REPAIR_OPERATIONS, SdcRepair
from .sdc_analyzer import SdcAnalysisResult

#: The wire schema the model must satisfy. `additionalProperties: false` everywhere, and `operation` is
#: an enum, so the model cannot widen the vocabulary by spelling it differently.
#:
#: `period`, `unit` and `objects` are OPTIONAL and that is the point: the model omits what it cannot
#: justify, and the renderer refuses rather than inventing. Making them required would force a value into
#: every response and turn an honest gap into a fabrication.
REPAIR_PROPOSAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["repair_proposals"],
    "properties": {
        "repair_proposals": {
            "type": "array",
            "minItems": 0,
            "maxItems": 5,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["target", "operation", "clock"],
                "properties": {
                    "target": {"type": "string", "minLength": 1},
                    "operation": {"type": "string", "enum": list(REPAIR_OPERATIONS)},
                    "clock": {"type": "string", "minLength": 1},
                    "period": {"type": "string"},
                    "unit": {"type": "string"},
                    "objects": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
    },
}

RULES = """You propose CONSTRAINT REPAIRS for constraint files, from the deterministic findings below.

Rules:
- Propose only for a clock named in REPORTED FINDINGS. Never invent a clock, a file or a finding.
- operation must be one of: {operations}.
  - define_clock addresses a `missing_constraint`: the clock is referenced but never defined.
  - remove_duplicate_clock addresses a `duplicate_constraint`: the same clock is defined more than
    once with identical period and targets, so one copy is redundant.
- A `conflicting_constraint` has NO repair. Its definitions disagree, and choosing between them is an
  engineering decision, not a derivation. Do not propose anything for it.
- Supply period and objects ONLY if the findings state them. If you do not know the period or the
  target port, OMIT the field. A proposal missing them is refused by the renderer, which is correct and
  safe. A period you invent is a fabricated timing constraint that nothing downstream can detect.
- NEVER write SDC, Tcl, a command, a diff or file content. You state an operation and its facts in
  fields; a deterministic renderer composes the text.
- Return only JSON matching the schema, with exactly its fields: add no others."""

#: Why a proposal did not become an `SdcRepair`. Each is reachable from the schema-valid response this
#: module accepts, so none of them is decoration.
PROPOSAL_REFUSAL_REASONS = (
    "incomplete_evidence",   # the analysis did not fully understand the file
    "invalid_repair",        # the entry failed SdcRepair's own contract
    "duplicate_proposal",    # the same operation was proposed twice for one clock
)


@dataclass(frozen=True)
class ProposalRefusal:
    """One proposal that did not become a repair, and why. Carries no repair fields to act on."""

    reason: str
    detail: str
    index: int = -1
    clock: str = ""

    def to_dict(self) -> dict:
        return {"reason": self.reason, "detail": self.detail, "index": self.index,
                "clock": self.clock}


@dataclass(frozen=True)
class RepairProposalSet:
    """Everything one repair-authoring call produced: the repairs, and the refusals beside them.

    Refusals are kept rather than dropped so that "the model proposed nothing" is distinguishable from
    "the model proposed three things and two were refused" - the first means the findings did not
    suggest a repair, the second means something went wrong and should be visible.
    """

    repairs: tuple[SdcRepair, ...] = ()
    refusals: tuple[ProposalRefusal, ...] = ()
    provider: str = ""
    model: str = ""
    fallback_used: bool = False
    attempts: tuple[dict, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.repairs

    def to_dict(self) -> dict:
        return {"repairs": [repair.to_dict() for repair in self.repairs],
                "refusals": [refusal.to_dict() for refusal in self.refusals],
                "provider": self.provider, "model": self.model,
                "fallback_used": self.fallback_used, "attempts": list(self.attempts)}


def _finding_line(finding: VlsiFinding) -> str:
    location = f"{finding.provenance.ref}:{finding.provenance.line}" if finding.provenance.line \
        else (finding.provenance.ref or "unknown location")
    facts = ", ".join(f"{key}={value}" for key, value in sorted(finding.details_dict.items()))
    return (f"- [{finding.kind}] {finding.identity}\n"
            f"  at {location}\n"
            f"  {finding.message}\n"
            f"  facts: {facts or 'none recorded'}")


def build_repair_prompt(baseline: SdcAnalysisResult) -> str:
    """The repair-authoring prompt: the rules, and nothing but the findings as evidence.

    Only deterministic findings appear. No recalled case, no symptom text, no patch - a repair proposal is
    justified by what the analysis observed about the file, and nothing else is evidence for it.
    """
    findings = baseline.findings
    body = "\n".join(_finding_line(finding) for finding in findings) or "- none reported"
    return "\n".join([RULES.format(operations=", ".join(REPAIR_OPERATIONS)), "",
                      "REPORTED FINDINGS", body, ""])


def clock_check(baseline: SdcAnalysisResult):
    """Reject a proposal naming a clock the analysis never reported.

    The mirror of `citation_check`: a hypothesis may cite only recalled cases, and a repair may target
    only a reported clock. A model that names an unreported clock is reasoning from nothing, and the
    renderer would refuse it later anyway - but refusing it HERE means the router retries the call with
    the violation reported, so a recoverable mistake can become a correct answer.
    """
    known = {str(finding.details_dict.get("clock", "")) for finding in baseline.findings}
    known.discard("")

    def check(data: dict) -> list[str]:
        problems = []
        for index, item in enumerate(data.get("repair_proposals", ())):
            clock = str(item.get("clock", ""))
            if clock not in known:
                problems.append(
                    f"repair_proposals[{index}] targets clock {clock!r}, which is not named in any "
                    f"reported finding (reported: {sorted(known)})")
        return problems

    return check


def generate_repair_proposals(baseline: SdcAnalysisResult, llm: Any) -> RepairProposalSet:
    """Ask the model for semantic repair proposals, and keep only what survives validation.

    `llm` is anything with `complete_structured`'s signature, injected exactly as in
    `pipeline/hypothesize.py`. LLM errors propagate: a failed call is never reported as "the model found
    no repair", because those are different facts and only one of them is true.

    An incomplete analysis returns a refusal without calling the model at all. When the parser reported
    anything, negative conclusions are provisional, so asking a model to repair a condition that may not
    exist would invite it to act on a phantom.
    """
    if baseline is None or not baseline.complete:
        status = getattr(baseline, "status", "absent")
        return RepairProposalSet(refusals=(ProposalRefusal(
            reason="incomplete_evidence",
            detail=(f"the analysis status is {status!r}; no repair may be proposed from an analysis that "
                    f"did not fully understand the file")),))

    result = llm.complete_structured(build_repair_prompt(baseline), schema=REPAIR_PROPOSAL_SCHEMA,
                                     name="repair_proposals", check=clock_check(baseline))

    repairs: list[SdcRepair] = []
    refusals: list[ProposalRefusal] = []
    claimed: set[tuple[str, str]] = set()
    for index, raw in enumerate(result.data.get("repair_proposals", ())):
        clock = str(raw.get("clock", ""))
        try:
            repair = SdcRepair.parse(raw, f"repair_proposals[{index}]")
        except VlsiContractError as exc:
            refusals.append(ProposalRefusal(reason="invalid_repair", detail=str(exc), index=index,
                                            clock=clock))
            continue
        key = (repair.operation, repair.clock)
        if key in claimed:
            # Two proposals for one operation on one clock are ambiguous, and which one was meant is not
            # recoverable. Refusing the later one keeps the first deterministically rather than picking.
            refusals.append(ProposalRefusal(
                reason="duplicate_proposal",
                detail=(f"operation {repair.operation!r} was already proposed for clock "
                        f"{repair.clock!r}; only one proposal per operation and clock is honoured"),
                index=index, clock=repair.clock))
            continue
        claimed.add(key)
        repairs.append(repair)

    return RepairProposalSet(repairs=tuple(repairs), refusals=tuple(refusals),
                             provider=result.provider, model=result.model,
                             fallback_used=result.fallback_used, attempts=tuple(result.attempts))


__all__ = [
    "PROPOSAL_REFUSAL_REASONS",
    "REPAIR_PROPOSAL_SCHEMA",
    "RULES",
    "ProposalRefusal",
    "RepairProposalSet",
    "build_repair_prompt",
    "clock_check",
    "generate_repair_proposals",
]