"""Retention service (MK7): turns a finished session into a MemoryCase and hands it to memory.

Mukul owns the call site; Rama's layer owns validation and the write, and alone decides `retained`.
Unknown environment values are dropped: a stored case never carries a guess.
"""

from __future__ import annotations

from debugagent.domain.investigation import Session
from debugagent.logging_setup import get_logger
from debugagent.ports.memory_port import MemoryPort, check_retention

log = get_logger(__name__)


def assemble_memory_case(session: Session) -> dict:
    """plan section 4: MemoryCase field mapping."""
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


class RetentionService:
    def __init__(self, memory: MemoryPort):
        self.memory = memory

    def retain(self, session: Session) -> dict:
        """Call only after every hypothesis has a decision and a resolution was reported (T5)."""
        if session.resolution is None or not session.verifications:
            raise ValueError("retain() requires engineer decisions and a reported resolution")
        decision = check_retention(self.memory.retain(assemble_memory_case(session)))
        log.info("retention retained=%s id=%s", decision["retained"], decision.get("memory_case_id"))
        return decision
