"""The worker stage: what the P6 workers contribute to one investigation.

P6 built three workers and left them with nothing to do - `investigate()` ran the Memory Specialist and
ignored the other two. This module is the seam that connects all three to the flow, through the existing
`Coordinator`, and turns their results into ONE structured record.

The trust boundary is the whole point of this file, and it is enforced by WHERE each result is allowed
to go rather than by a warning attached to it:

| Result | Stands for | May become | Must never |
|---|---|---|---|
| Memory delegation | knowledge of PAST cases | context for the engineer | evidence, a decision, a verdict |
| Verifier findings | observed facts about the CURRENT system | an EVIDENCE-labelled report section | an `Evidence` item, a verification status, a verdict |
| Patch proposals | candidate changes | a PROPOSAL section | an applied change, a verified fix |

Note what "verifier results are current-system EVIDENCE" does and does not mean here. It places them on
the evidence SIDE of the boundary - they describe the system as it is, unlike a recalled case - and it
deliberately does not mean they enter `Evidence`. `evidence.py` and `verify.py` are untouched: the
verifier's observations are reported alongside them, never inside them. An observation the engineer has
not confirmed is still an observation, and folding it into the evidence set would let a worker's read of
a file stand as a verified fact about the system.

Determinism: findings are RANKED by a stable key and ties broken by (kind, ref), so the same repository
and issue always produce the same ordering regardless of which worker finished first. Nothing here
consults a clock or a completion order.

Authority: this module never authorises. It builds `TaskSpec`s through the P1 seam and hands them to
`Coordinator.fan_out`, so `authorize()` runs before every dispatch, refusals stay refusals, and memory
work still serialises through the shared `MemoryLane` exactly as before.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

from debugagent.agents.coordinator import FanOutResult, TaskOutcome
from debugagent.agents.tasks import SubAgentResult, TaskSpec

#: How many findings are carried in the section. A cap, not a truncation of engineering truth: the
#: count of what was dropped is reported alongside, so a truncated list cannot read as a complete one.
MAX_REPORTED_FINDINGS = 20


@dataclass(frozen=True)
class RankedFinding:
    """One verifier observation, with the deterministic rank it was given.

    `score` counts how many of the issue's own environment values and symptoms appear in the
    observation. It is a cheap, explainable relevance signal - not a judgement - and it is stable for a
    given repository and issue, which is what makes the ordering reproducible.
    """

    kind: str
    ref: str
    content: str
    score: int
    task_id: str
    source: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "ref": self.ref, "content": self.content, "score": self.score,
                "task_id": self.task_id, "source": self.source}


def _relevance_terms(case: Any) -> tuple[str, ...]:
    """The issue's own values, lower-cased, used only to rank - never to assert anything."""
    terms: list[str] = []
    for name, value in (getattr(case, "environment", None) or {}).items():
        if value:
            terms.append(str(value).lower())
    for symptom in (getattr(case, "symptoms", None) or ()):
        # Word-ish tokens, punctuation stripped at the edges. Splitting on whitespace alone kept the
        # trailing separator - "fail;" and "ECONNRESET;" - so a term could never match the same word
        # appearing in a log line without that punctuation, and every score came back zero.
        for token in re.findall(r"[a-z0-9][a-z0-9._+-]*", str(symptom).lower()):
            if len(token) > 3:
                terms.append(token)
    # Sorted and de-duplicated so the term list - and therefore every score - is order-independent.
    return tuple(sorted({term for term in terms if term}))


def _scorable_text(content: str, signature: str) -> str:
    """The part of an observation that is evidence of anything: the quoted material.

    Every worker observation opens with a fixed sentence that echoes the issue signature - "OBSERVED in
    <file> for issue '<signature>'. Current-system observation...". Scoring the whole string therefore
    counted the TEMPLATE: every finding scored identically, however irrelevant its content, and a reader
    was told the file matched two terms when in fact the boilerplate had matched them. The audit found
    this by ranking a deliberately unrelated file first and watching it tie with the real culprit.

    So the echoed signature is removed before matching. What remains is what the worker actually read.
    When nothing in that matches, the score is honestly zero and the deterministic `(kind, ref, task_id)`
    tiebreak orders the findings - which is the correct outcome, not a gap.
    """
    lowered = content.lower()
    if signature:
        lowered = lowered.replace(signature.lower(), " ")
    return lowered


def rank_findings(observations: Sequence[tuple[SubAgentResult, Any]], case: Any) -> list[RankedFinding]:
    """Rank observations deterministically.

    Ordering is `(-score, kind, ref, task_id, content)`: the most issue-relevant first, then a total
    order that no two findings can tie on. That last part matters - a sort that can tie would make the
    output depend on the order the workers happened to finish in, so `content` is in the key rather
    than left to be an arbitrary tiebreak. Two byte-identical findings are then genuinely the same
    finding, and a stable sort leaves them in the order they arrived, which is itself deterministic.
    """
    terms = _relevance_terms(case)
    signature = getattr(case, "problem_signature", "") or ""
    findings: list[RankedFinding] = []
    for result, _outcome in observations:
        for observation in result.observations:
            lowered = _scorable_text(observation.content, signature)
            score = sum(1 for term in terms if term in lowered)
            findings.append(RankedFinding(
                kind=observation.kind, ref=observation.ref, content=observation.content,
                score=score, task_id=result.task_id, source=observation.source))
    findings.sort(key=lambda finding: (-finding.score, finding.kind, finding.ref, finding.task_id,
                                       finding.content))
    return findings


def _outcome_records(outcomes: Sequence[TaskOutcome]) -> list[dict]:
    """Per-task records, in INPUT order, keeping every outcome distinguishable.

    A refusal is recorded as `refused` and never as `failed`: collapsing the two would report a rule
    saying no as a thing going wrong, which is the confusion the P1 vocabulary exists to prevent.
    """
    return [outcome.to_dict() for outcome in outcomes]


def _refusal_records(outcomes: Sequence[TaskOutcome]) -> list[dict]:
    return [{"task_id": outcome.task_id, "agent": outcome.agent,
             "error": f"{type(outcome.error).__name__}: "
                      f"{' '.join(str(outcome.error).split())[:200]}"}
            for outcome in outcomes if outcome.refused]


@dataclass(frozen=True)
class WorkerStage:
    """Everything the workers contributed to one investigation, in one record.

    `verifier` and `patches` are separate because they mean different things and are rendered
    separately; they are held together here so a session carries ONE structured worker result rather
    than three loosely related fields.
    """

    verifier: dict | None
    patches: dict | None

    @property
    def ran(self) -> bool:
        return self.verifier is not None or self.patches is not None

    def to_dict(self) -> dict:
        record: dict[str, Any] = {}
        if self.verifier is not None:
            record["verifier"] = self.verifier
        if self.patches is not None:
            record["patches"] = self.patches
        return record


def _verifier_section(fan: FanOutResult, findings: Sequence[RankedFinding]) -> dict:
    reported = list(findings[:MAX_REPORTED_FINDINGS])
    return {
        "agent": _agent_of(fan),
        "boundary": ("current-system observations. NOT an Evidence item, NOT a verification, and not a "
                     "verdict: the engineer confirms them, exactly as they confirm any observation"),
        "findings": [finding.to_dict() for finding in reported],
        "findings_total": len(findings),
        "findings_truncated": len(findings) > len(reported),
        "outcomes": _outcome_records(fan.outcomes),
        "refusals": _refusal_records(fan.outcomes),
        "summary": fan.summary(),
    }


def _patches_section(fan: FanOutResult) -> dict:
    return {
        "agent": _agent_of(fan),
        "boundary": ("PROPOSALS. Nothing here is applied, written, committed or verified, and no patch is "
                     "ever applied automatically. Applying one is an engineer decision"),
        "proposals": [result.to_dict() for result in fan.results if result.ok],
        "outcomes": _outcome_records(fan.outcomes),
        "refusals": _refusal_records(fan.outcomes),
        "summary": fan.summary(),
    }


def _agent_of(fan: FanOutResult) -> str:
    for outcome in fan.outcomes:
        return outcome.agent
    return ""


def plan_tasks(case: Any, *, verifier_targets: Sequence[str] = (),
               patch_requests: Sequence[tuple[str, str]] = ()) -> tuple[list[TaskSpec], list[TaskSpec]]:
    """Turn caller INTENT into worker tasks, one task per target for failure isolation.

    The caller says "look at these files" and "propose these changes"; this module says which task
    shape that becomes. That split is deliberate - `investigate()` should not have to know a worker's
    schema, and a worker should not be told what to look at by the flow that dispatched it.

    One task per target, not one task listing all of them: a missing or unreadable file then fails on
    its own and leaves the other files' findings intact, instead of one bad path discarding a whole
    stage.

    Nothing is invented here. A patch request must arrive with both a target and a proposed body,
    because a worker choosing its own target is a worker deciding what to change.
    """
    from debugagent.agents.code_log_verifier import build_verifier_task
    from debugagent.agents.patch_generator import build_patch_task

    signature = getattr(case, "problem_signature", "")
    symptoms = tuple(getattr(case, "symptoms", ()) or ())

    verifier_tasks = [
        build_verifier_task(task_id=f"verify:{target}", case_signature=signature,
                            targets=(target,), symptoms=symptoms)
        for target in verifier_targets]

    patch_tasks = [
        build_patch_task(task_id=f"patch:{target}", case_signature=signature,
                         target=target, proposed=proposed)
        for target, proposed in patch_requests]

    return verifier_tasks, patch_tasks


def run_worker_stage(repository: Any, case: Any, *, verifier_targets: Sequence[str] = (),
                     patch_requests: Sequence[tuple[str, str]] = (),
                     timeout: float | None = None) -> WorkerStage:
    """Run the verifier and patcher through the Coordinator and structure what comes back.

    A stage with no targets in one of the two channels records `None` for that channel rather than an
    empty section, so a session that never asked for patches does not grow a "no patches" section that
    reads like a finding. Which targets exist is the caller's decision.

    The Coordinator comes from the repository runtime, so the dispatch goes through the same
    `authorize()` seam - and, in a memory session, the same `MemoryLane` - as every other delegation.
    """
    verifier_tasks, patch_tasks = plan_tasks(case, verifier_targets=verifier_targets,
                                            patch_requests=patch_requests)

    verifier_section = None
    if verifier_tasks:
        fan = repository.coordinator.fan_out(tuple(verifier_tasks), timeout=timeout)
        observations = [(outcome.result, outcome) for outcome in fan.outcomes
                        if outcome.result is not None and outcome.result.ok]
        verifier_section = _verifier_section(fan, rank_findings(observations, case))

    patches_section = None
    if patch_tasks:
        fan = repository.coordinator.fan_out(tuple(patch_tasks), timeout=timeout)
        patches_section = _patches_section(fan)

    return WorkerStage(verifier=verifier_section, patches=patches_section)
