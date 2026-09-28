"""State of one investigation: what memory returned, what the agent proposed, what the engineer
decided, and the session that ties them together. Plain data; no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field

from debugagent.domain.models import (DebugInput, Evidence, Hypothesis, NormalizedDebugCase, Resolution,
                                      VerificationResult)


def compare_environment(past: dict[str, str], current: dict[str, str | None]) -> list[tuple[str, str, str | None]]:
    """Past-case environment keys that differ from, or are unknown in, the current environment:
    (key, past value, current value or None). Used for display and flagging; it never merges."""
    return [(key, value, current.get(key)) for key, value in sorted(past.items()) if current.get(key) != value]


@dataclass(frozen=True)
class MemoryContext:
    """Rama's classified recall, as received through the memory port. Relevance was decided there."""

    bank_id: str
    recalled_at: str
    query: str
    candidates: list[dict]
    excluded: list[dict]  # never cited, kept for the trace
    abstention: dict

    @property
    def abstained(self) -> bool:
        return self.abstention["abstained"]

    def citable(self) -> dict[str, dict]:
        """Candidates a hypothesis may cite. Empty when memory abstained, even if candidates are shown
        (abstained + contradictory: both sides are displayed, the proposal stays generic)."""
        return {} if self.abstained else {c["case_id"]: c for c in self.candidates}

    def candidate(self, case_id: str) -> dict:
        return next(c for c in self.candidates if c["case_id"] == case_id)

    def to_dict(self) -> dict:
        return {
            "bank_id": self.bank_id, "recalled_at": self.recalled_at, "query": self.query,
            "abstention": self.abstention,
            "candidates": [{k: c[k] for k in ("case_id", "relevance_class", "environment", "reason")}
                           for c in self.candidates],
            "excluded": [{k: c[k] for k in ("case_id", "relevance_class", "reason")} for c in self.excluded],
        }


@dataclass(frozen=True)
class Proposal:
    hypotheses: list[Hypothesis]
    provider: str
    model: str
    fallback_used: bool
    attempts: list[dict]

    def to_dict(self) -> dict:
        return {"provider": self.provider, "model": self.model, "fallback_used": self.fallback_used,
                "attempts": self.attempts, "hypotheses": [h.to_dict() for h in self.hypotheses]}


@dataclass(frozen=True)
class EngineerDecision:
    decision: str                 # accept | modify | reject
    claim: str | None = None      # engineer's reading of current evidence: supported | contradicted
    relevance_confirmed: bool = False
    note: str = ""


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
        def maybe(value):
            return value.to_dict() if value is not None else None

        return {
            "session_id": self.session_id,
            "input": self.raw.to_dict(),
            "case": maybe(self.case),
            "memory": maybe(self.memory),
            "evidence": maybe(self.evidence),
            "proposal": maybe(self.proposal),
            "verifications": [v.to_dict() for v in self.verifications],
            "resolution": maybe(self.resolution),
            "retention": self.retention,
            "trace": self.trace,
        }
