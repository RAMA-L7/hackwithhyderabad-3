"""Investigation service (MK7): one session, end to end.

normalize -> recall -> EVIDENCE -> hypothesize -> engineer verifies -> engineer resolves -> retain.
Retention happens only after every hypothesis has an engineer decision AND the engineer reported a
resolution (T5). The engineer is a port, so the terminal controller and the tests drive the same code.
"""

from __future__ import annotations

import uuid
from typing import Callable

from debugagent.domain.investigation import Session
from debugagent.domain.models import DebugInput
from debugagent.logging_setup import get_logger, session_context
from debugagent.ports.engineer_port import EngineerPort
from debugagent.services.evidence_service import EvidenceService
from debugagent.services.hypothesis_service import HypothesisService
from debugagent.services.normalization_service import NormalizationService
from debugagent.services.recall_service import RecallService
from debugagent.services.retention_service import RetentionService
from debugagent.services.verification_service import VerificationService

log = get_logger(__name__)
OnStep = Callable[[Session], None]


class InvestigationService:
    def __init__(self, *, normalizer: NormalizationService, recall: RecallService, evidence: EvidenceService,
                 hypotheses: HypothesisService, verification: VerificationService, retention: RetentionService):
        self.normalizer = normalizer
        self.recall = recall
        self.evidence = evidence
        self.hypotheses = hypotheses
        self.verification = verification
        self.retention = retention

    def run(self, raw: DebugInput, engineer: EngineerPort, *, session_id: str | None = None,
            on_step: OnStep | None = None) -> Session:
        """`on_step(session)` is called after each stage so a caller can persist progress."""
        session = Session(session_id or uuid.uuid4().hex[:12], raw)
        with session_context(session.session_id):
            log.info("session started")
            for stage in (self._normalize, self._recall, self._gather_evidence, self._propose, self._verify,
                          self._resolve):
                session.trace.append(stage(session, engineer))
                if on_step:
                    on_step(session)
            if session.resolution is None:
                engineer.report("retention", session)
                log.info("session not resolved; nothing retained")
                return session
            session.retention = self.retention.retain(session)
            session.trace.append(f"retention: retained={session.retention['retained']} "
                                 f"id={session.retention.get('memory_case_id')}")
            engineer.report("retention", session)
            if on_step:
                on_step(session)
            log.info("session finished")
        return session

    # ---- stages: each updates the session, reports its stage, and returns one trace line ----

    def _normalize(self, s: Session, engineer: EngineerPort) -> str:
        s.case = self.normalizer.normalize(s.raw)
        return f"normalized: {s.case.problem_signature}"

    def _recall(self, s: Session, engineer: EngineerPort) -> str:
        s.memory = self.recall.recall(s.case)
        engineer.report("memory", s)
        return (f"recalled {len(s.memory.candidates)} candidate(s), {len(s.memory.excluded)} excluded; "
                f"abstained={s.memory.abstained} ({s.memory.abstention['relevance_class']})")

    def _gather_evidence(self, s: Session, engineer: EngineerPort) -> str:
        s.evidence = self.evidence.build(s.case)
        facts = engineer.current_facts(s.evidence)
        if facts:
            s.evidence = self.evidence.add_facts(s.evidence, facts)
        engineer.report("evidence", s)
        known = sum(item.known for item in s.evidence.items)
        return f"evidence: {known} known item(s), unknown: {s.evidence.unknown_fields or 'none'}"

    def _propose(self, s: Session, engineer: EngineerPort) -> str:
        s.proposal = self.hypotheses.generate(s.case, s.memory)
        engineer.report("proposal", s)
        return (f"proposed {len(s.proposal.hypotheses)} hypothesis(es) via {s.proposal.provider} "
                f"(fallback_used={s.proposal.fallback_used}): "
                + ", ".join(f"{h.ref} {h.relevance_state} cites {h.supporting_case_ids or '[]'}"
                            for h in s.proposal.hypotheses))

    def _verify(self, s: Session, engineer: EngineerPort) -> str:
        decisions = {}
        for h in s.proposal.hypotheses:
            mismatched, missing = self.verification.gaps(h, s.evidence, s.memory)
            decisions[h.ref] = engineer.decide(h, mismatched, missing)
        s.verifications = self.verification.verify(s.proposal.hypotheses, s.evidence, s.memory, decisions)
        engineer.report("decision", s)
        return "decisions: " + ", ".join(f"{v.hypothesis_ref} {v.engineer_decision}/{v.status}" for v in s.verifications)

    def _resolve(self, s: Session, engineer: EngineerPort) -> str:
        answers = engineer.resolve(s)
        if answers is None:
            return "not resolved; nothing retained"
        s.resolution = self.verification.build_resolution(s.proposal.hypotheses, s.verifications, **answers)
        return (f"resolved: {s.resolution.outcome}; root cause "
                f"{'confirmed' if s.resolution.root_cause_confirmed else 'unconfirmed'}; "
                f"{len(s.resolution.failed_approaches_this_session)} failed approach(es)")
