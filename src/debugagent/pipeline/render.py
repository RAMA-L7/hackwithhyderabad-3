"""Plain-text rendering of the four trust sections: MEMORY / EVIDENCE / PROPOSAL / DECISION (C4).

Rules held here: scores are never shown; recalled environments are shown verbatim as past
conditions; a hypothesis is never styled as verified.
"""

from __future__ import annotations

import textwrap

from debugagent.pipeline.hypothesize import Proposal
from debugagent.pipeline.recall_match import MemoryContext, compare_environment
from debugagent.pipeline.types import Evidence, Hypothesis, VerificationResult

WIDTH = 78
CLASS_LABEL = {"relevant": "relevant", "partial": "partial · weak reference", "contradictory": "contradictory",
               "irrelevant": "irrelevant", "stale": "stale"}
STATE_LABEL = {"supported": "memory-backed", "conditional": "memory-backed · past cases disagree",
               "weak-reference": "weak reference to memory", "generic": "generic · no memory used"}


def section(title: str, subtitle: str, lines: list[str]) -> str:
    rule = "━" * WIDTH
    return "\n".join([rule, f"{title}  ·  {subtitle}", rule, *lines, ""])


def _wrap(text: str, indent: str = "  ") -> list[str]:
    return textwrap.wrap(" ".join(text.split()), WIDTH, initial_indent=indent, subsequent_indent=indent) or [indent]


def _env(environment: dict) -> str:
    return ", ".join(f"{k}={v if v is not None else 'unknown'}" for k, v in environment.items()) or "none recorded"


def render_worker_verifier(report: dict) -> str:
    """The Code/Log Verifier's findings: current-system OBSERVATIONS, ranked, never decisions.

    Deliberately separate from `render_evidence`, and deliberately never fed one. These are what a
    worker read in a file or a log; the engineer still confirms them, exactly as they confirm any
    observation. Folding them into the evidence set would let a worker's read of a file stand as a
    verified fact about the system.
    """
    lines = ["Code/Log Verifier: observations of the CURRENT system. Not an evidence item, not a "
             "verification, and not a verdict - you confirm these."]
    findings = report.get("findings") or []
    if not findings:
        # A clean stage that found nothing says so, rather than rendering as though it had.
        lines += ["", "No observation was reported. That is a clean run with nothing to show, not a "
                      "claim that the current system was verified."]
        return "\n".join(lines)
    for index, finding in enumerate(findings, start=1):
        lines += ["", f"[{finding['kind']}] {index}. {finding['ref']}"]
        if finding.get("score"):
            lines.append(f"    (matches {finding['score']} term(s) from this issue)")
        lines += _wrap(finding.get("content", ""), "    ")
    if report.get("findings_truncated"):
        lines += ["", f"Showing {len(findings)} of {report['findings_total']} findings; the rest were "
                      f"not shown."]
    for refusal in report.get("refusals") or []:
        lines += ["", f"REFUSED: {refusal['task_id']} - {refusal['error']}"]
    return "\n".join(lines)


def render_patch_proposals(report: dict) -> None | str:
    """The Patch Generator's proposals. Nothing here is applied, and nothing is verified.

    The wording is deliberate and load-bearing: a proposal is a candidate change, so the section must
    never read as a fix, a diff that was applied, or a change anyone has tested.
    """
    lines = ["Patch Generator: PROPOSALS only. Nothing here has been applied, written, committed or "
             "run, and no proposal is applied automatically."]
    proposals = [result for result in (report.get("proposals") or [])]
    if not proposals:
        lines += ["", "No proposal was produced."]
        return "\n".join(lines)
    for result in proposals:
        lines += ["", f"[proposal] {result.get('task_id')}"]
        for observation in result.get("observations") or []:
            lines += _wrap(observation.get("content", ""), "    ")
    for refusal in report.get("refusals") or []:
        lines += ["", f"REFUSED: {refusal['task_id']} - {refusal['error']}"]
    return "\n".join(lines)


def render_memory_delegation(report: dict) -> str:
    """P4: the Memory Specialist's own report, shown as MEMORY and labelled as such.

    Deliberately a separate function from `render_memory`, and deliberately never fed an
    `Evidence`: the worker's observations are knowledge retrieved through the delegation seam, and
    this renders them without ever presenting them as current-system evidence.
    """
    status = report.get("status", "failed")
    header = (f"Memory Specialist ({report.get('agent', 'memory_specialist')}): {status}."
              " Past experience, not evidence about this issue.")
    if status == "failed":
        return "\n".join([header, "", f"Memory unavailable: {report.get('failure_detail') or 'unknown'}.",
                          "The proposal will be generic."])
    lines = [header]
    for observation in report.get("observations") or []:
        lines += ["", f"[memory] {observation.get('ref')}"]
        lines += _wrap(observation.get("content", ""), "    ")
    return "\n".join(lines)


def render_memory(memory: MemoryContext, current_env: dict) -> str:
    lines = [f"Past cases from bank '{memory.bank_id}'. Prior experience, not evidence about this issue."]
    if memory.abstained:
        lines += ["", f"No usable memory: {memory.abstention['reason']}.", "The proposal will be generic."]
    for c in memory.candidates:
        outcome = f" · outcome {c['outcome']}" if c.get("outcome") else ""
        lines += ["", f"[{CLASS_LABEL[c['relevance_class']]}] case {c['case_id']}{outcome}",
                  f"  original environment: {_env(c['environment'])}"]
        diffs = compare_environment(c["environment"], current_env)
        lines.append("  differs from now: " + (", ".join(f"{k} (then {p}, now {n or 'unknown'})" for k, p, n in diffs)
                                               or "nothing recorded differs"))
        lines.append(f"  why recalled: {c['reason']}")
        if c["conflicts_with"]:
            lines.append(f"  conflicts with: {'; '.join(c['conflicts_with'])}")
        lines += ["  past case:"] + _wrap(c["text"], "    ")
    if memory.excluded:
        lines += ["", "Excluded from the proposal, kept in the trace: "
                  + ", ".join(f"{c['case_id']} [{c['relevance_class']}]" for c in memory.excluded)]
    return section("MEMORY", "what happened before", lines)


def render_evidence(evidence: Evidence) -> str:
    lines = ["Current facts, supplied now. Only these can verify a hypothesis."]
    for item in evidence.items:
        value = item.value if item.known else "unknown"
        lines.append(f"  {item.name}: {value}   ({item.source}, {item.captured_at})")
    if evidence.unknown_fields:
        lines.append(f"Unknown now: {', '.join(evidence.unknown_fields)}")
    return section("EVIDENCE", "what is true now", lines)


def render_proposal(proposal: Proposal) -> str:
    lines = [f"served by {proposal.provider} / {proposal.model} · fallback_used={proposal.fallback_used}",
             "Unverified proposals. Nothing below has been checked yet."]
    for h in proposal.hypotheses:
        lines += ["", f"{h.ref} [{STATE_LABEL[h.relevance_state]}]"] + _wrap(h.hypothesis)
        lines.append(f"  cites: {', '.join(h.supporting_case_ids) or 'no past case'}")
        lines += ["  would be refuted by:"] + [line for r in h.refutation_conditions for line in _wrap(f"- {r}", "    ")]
        lines += ["  next step:"] + _wrap(h.recommended_next_step, "    ")
    return section("PROPOSAL", "what the agent suggests", lines)


def render_decision(hypotheses: list[Hypothesis], verifications: list[VerificationResult]) -> str:
    by_ref = {h.ref: h for h in hypotheses}
    lines = ["Engineer decisions, recorded against current evidence."]
    for v in verifications:
        lines += ["", f"{v.hypothesis_ref}: engineer {v.engineer_decision} · evidence {v.status.replace('_', ' ')}"]
        if by_ref[v.hypothesis_ref].supporting_case_ids:
            lines.append(f"  past case relevance confirmed by engineer: {'yes' if v.relevance_confirmed else 'no'}")
        if v.mismatched_environment_fields:
            lines.append(f"  differs from the cited case: {', '.join(v.mismatched_environment_fields)}")
        if v.engineer_note:
            lines += _wrap(f"note: {v.engineer_note}")
    return section("DECISION", "what the engineer decided", lines)


def render_retention(retention: dict | None, reason: str = "") -> str:
    if retention is None:
        return section("RETENTION", "memory write", [f"Nothing retained: {reason}"])
    status = "retained" if retention["retained"] else "not retained"
    lines = [f"{status}: {retention['reason']}"]
    if retention.get("memory_case_id"):
        lines.append(f"memory id: {retention['memory_case_id']}")
    return section("RETENTION", "memory write", lines)
