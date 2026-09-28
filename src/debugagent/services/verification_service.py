"""Verification service (MK6 parts 2-3): the verification gate and the resolution.

Preconditions are checked against Evidence only. A recalled environment may name which fields differ
(mismatched_environment_fields) but never satisfies a check or upgrades a status. The engineer decides.
"""

from __future__ import annotations

from debugagent.domain.errors import VerificationError
from debugagent.domain.investigation import EngineerDecision, MemoryContext, compare_environment
from debugagent.domain.models import Evidence, Hypothesis, Resolution, VerificationResult

VERDICTS = ("supported", "contradicted", "insufficient_evidence")


class VerificationService:
    def gaps(self, hypothesis: Hypothesis, evidence: Evidence, memory: MemoryContext) -> tuple[list[str], list[str]]:
        """(mismatched, missing). mismatched: cited-case environment fields that differ from, or are unknown
        in, current evidence. missing: what current evidence lacks for a verdict (plan Q4)."""
        known = evidence.known()
        mismatched: list[str] = []
        missing: list[str] = [] if any(item.known for item in evidence.items) else ["no current evidence recorded"]
        for case_id in hypothesis.supporting_case_ids:
            for key, _past, now in compare_environment(memory.candidate(case_id)["environment"], known):
                if key not in mismatched:
                    mismatched.append(key)
                if now is None and key not in missing:
                    missing.append(key)
        return mismatched, missing

    def verify(self, hypotheses: list[Hypothesis], evidence: Evidence, memory: MemoryContext,
               decisions: dict[str, EngineerDecision]) -> list[VerificationResult]:
        return [self._verify_one(h, evidence, memory, decisions.get(h.ref)) for h in hypotheses]

    def _verify_one(self, hypothesis: Hypothesis, evidence: Evidence, memory: MemoryContext,
                    decision: EngineerDecision | None) -> VerificationResult:
        if decision is None:
            raise VerificationError(f"{hypothesis.ref}: no engineer decision recorded; nothing proceeds without one")
        mismatched, missing = self.gaps(hypothesis, evidence, memory)
        # memory never fills a gap: missing evidence means insufficient_evidence, however strong the match
        status = "insufficient_evidence" if missing else decision.claim
        if status not in VERDICTS:
            raise VerificationError(f"{hypothesis.ref}: state whether current evidence supports or contradicts it")
        return VerificationResult.from_dict({
            "hypothesis_ref": hypothesis.ref,
            "status": status,
            "engineer_decision": decision.decision,
            "relevance_confirmed": bool(hypothesis.supporting_case_ids) and decision.relevance_confirmed,
            "mismatched_environment_fields": mismatched,
            "engineer_note": decision.note,
        })

    def build_resolution(self, hypotheses: list[Hypothesis], verifications: list[VerificationResult], *,
                         action_taken: str, observed_result: str, root_cause_confirmed: str | None, outcome: str,
                         failed_approaches: list[dict] | tuple = (), evidence_refs: list[str] | tuple = ()) -> Resolution:
        """The engineer's report, never inferred. An accepted hypothesis that current evidence contradicted
        is recorded as a failed approach instead of being dropped."""
        bad_refs = [ref for ref in evidence_refs if "://" not in ref]
        if bad_refs:
            raise VerificationError(f"evidence references must look like log://... or report://..., not pasted text: {bad_refs}")
        failed = [dict(item) for item in failed_approaches] + self._contradicted_attempts(hypotheses, verifications)
        return Resolution.from_dict({
            "action_taken": action_taken, "observed_result": observed_result,
            "root_cause_confirmed": root_cause_confirmed or None, "outcome": outcome,
            "failed_approaches_this_session": failed, "evidence_refs": list(evidence_refs),
        })

    @staticmethod
    def _contradicted_attempts(hypotheses: list[Hypothesis], verifications: list[VerificationResult]) -> list[dict]:
        by_ref = {h.ref: h for h in hypotheses}
        return [{"approach": by_ref[v.hypothesis_ref].recommended_next_step,
                 "why_failed": v.engineer_note or f"current evidence contradicted {v.hypothesis_ref}: "
                                                  f"{by_ref[v.hypothesis_ref].hypothesis}"}
                for v in verifications if v.status == "contradicted" and v.engineer_decision in ("accept", "modify")]
