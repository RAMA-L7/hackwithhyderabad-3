"""MK7: one investigation, end to end. Owns the seam calls and the retention call site.

normalize -> recall -> EVIDENCE -> hypothesize -> engineer verifies -> engineer resolves -> retain.
retain() is called only after every hypothesis has an engineer decision AND the engineer reported a
resolution (T5). The Engineer is injected, so the CLI and the tests drive the same code.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol

from debugagent.pipeline.evidence import add_facts, build_evidence
from debugagent.pipeline.hypothesize import Proposal, generate_hypotheses
from debugagent.pipeline.memory_port import MemoryPort, check_retention
from debugagent.pipeline.normalize import normalize
from debugagent.pipeline.recall_match import MemoryContext, recall
from debugagent.pipeline.render import (render_decision, render_evidence, render_memory, render_proposal,
                                        render_retention)
from debugagent.pipeline.types import DebugInput, Evidence, Hypothesis, NormalizedDebugCase, Resolution, VerificationResult
from debugagent.pipeline.verify import EngineerDecision, build_resolution, evidence_gaps, verify


class Engineer(Protocol):
    def show(self, text: str) -> None: ...
    def current_facts(self, evidence: Evidence) -> dict[str, str | None]: ...
    def decide(self, hypothesis: Hypothesis, mismatched: list[str], missing: list[str]) -> EngineerDecision: ...
    def resolve(self, session: "Session") -> dict | None:
        """Keyword answers for build_resolution, or None when the issue was not resolved."""


@dataclass
class Session:
    session_id: str
    raw: DebugInput
    case: NormalizedDebugCase | None = None
    memory: MemoryContext | None = None
    evidence: Evidence | None = None
    proposal: Proposal | None = None
    verifications: list[VerificationResult] = field(default_factory=list)
    resolution: Resolution | None = None
    retention: dict | None = None
    trace: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        memory = self.memory
        return {
            "session_id": self.session_id,
            "input": self.raw.to_dict(),
            "case": self.case.to_dict() if self.case else None,
            "memory": None if memory is None else {
                "bank_id": memory.bank_id, "recalled_at": memory.recalled_at, "query": memory.query,
                "abstention": memory.abstention,
                "candidates": [{k: c[k] for k in ("case_id", "relevance_class", "environment", "reason")} for c in memory.candidates],
                "excluded": [{k: c[k] for k in ("case_id", "relevance_class", "reason")} for c in memory.excluded]},
            "evidence": self.evidence.to_dict() if self.evidence else None,
            "proposal": None if self.proposal is None else {
                "provider": self.proposal.provider, "model": self.proposal.model,
                "fallback_used": self.proposal.fallback_used, "attempts": self.proposal.attempts,
                "hypotheses": [h.to_dict() for h in self.proposal.hypotheses]},
            "verifications": [v.to_dict() for v in self.verifications],
            "resolution": self.resolution.to_dict() if self.resolution else None,
            "retention": self.retention,
            "trace": self.trace,
        }


def assemble_memory_case(session: Session) -> dict:
    """plan section 4. Unknown environment values are dropped: a stored case never carries a guess."""
    case, resolution, evidence = session.case, session.resolution, session.evidence
    environment = {item.name: item.value for item in evidence.items if item.known and item.name in case.environment}
    notes = "; ".join(f"{v.hypothesis_ref} {v.engineer_decision}, evidence {v.status}"
                      + (f": {v.engineer_note}" if v.engineer_note else "") for v in session.verifications)
    return {
        "problem_signature": case.problem_signature,
        "symptoms": list(case.symptoms),
        "environment": environment,
        "observed_evidence": list(resolution.evidence_refs),
        "investigation_trace": list(session.trace),
        "failed_approaches": [f.to_dict() for f in resolution.failed_approaches_this_session],
        "root_cause": resolution.root_cause_confirmed,
        "resolution": f"{resolution.action_taken} (observed: {resolution.observed_result})",
        "outcome": resolution.outcome,
        "verification_notes": notes or "engineer recorded no notes",
        "session_id": session.session_id,
    }


def investigate(raw: DebugInput, port: MemoryPort, llm, engineer: Engineer, *, session_id: str | None = None,
                on_step=None) -> Session:
    """Run one session. `on_step(session)` is called after each stage so a caller can persist progress."""
    s = Session(session_id or uuid.uuid4().hex[:12], raw)

    def step(note: str) -> None:
        s.trace.append(note)
        if on_step:
            on_step(s)

    s.case = normalize(raw)
    step(f"normalized: {s.case.problem_signature}")

    s.memory = recall(port, s.case)
    engineer.show(render_memory(s.memory, s.case.environment))
    step(f"recalled {len(s.memory.candidates)} candidate(s), {len(s.memory.excluded)} excluded; "
         f"abstained={s.memory.abstained} ({s.memory.abstention['relevance_class']})")

    s.evidence = build_evidence(s.case)
    facts = engineer.current_facts(s.evidence)
    if facts:
        s.evidence = add_facts(s.evidence, facts)
    engineer.show(render_evidence(s.evidence))
    step(f"evidence: {sum(i.known for i in s.evidence.items)} known item(s), unknown: {s.evidence.unknown_fields or 'none'}")

    s.proposal = generate_hypotheses(s.case, s.memory, llm)
    engineer.show(render_proposal(s.proposal))
    step(f"proposed {len(s.proposal.hypotheses)} hypothesis(es) via {s.proposal.provider} "
         f"(fallback_used={s.proposal.fallback_used}): "
         + ", ".join(f"{h.ref} {h.relevance_state} cites {h.supporting_case_ids or '[]'}" for h in s.proposal.hypotheses))

    decisions = {}
    for h in s.proposal.hypotheses:
        mismatched, missing = evidence_gaps(h, s.evidence, s.memory)
        decisions[h.ref] = engineer.decide(h, mismatched, missing)
    s.verifications = verify(s.proposal.hypotheses, s.evidence, s.memory, decisions)
    engineer.show(render_decision(s.proposal.hypotheses, s.verifications))
    step("decisions: " + ", ".join(f"{v.hypothesis_ref} {v.engineer_decision}/{v.status}" for v in s.verifications))

    answers = engineer.resolve(s)
    if answers is None:
        engineer.show(render_retention(None, "the engineer did not report a resolution"))
        step("not resolved; nothing retained")
        return s
    s.resolution = build_resolution(s.proposal.hypotheses, s.verifications, **answers)
    step(f"resolved: {s.resolution.outcome}; root cause {'confirmed' if s.resolution.root_cause_confirmed else 'unconfirmed'}; "
         f"{len(s.resolution.failed_approaches_this_session)} failed approach(es)")

    s.retention = check_retention(port.retain(assemble_memory_case(s)))
    engineer.show(render_retention(s.retention))
    step(f"retention: retained={s.retention['retained']} id={s.retention.get('memory_case_id')}")
    return s
