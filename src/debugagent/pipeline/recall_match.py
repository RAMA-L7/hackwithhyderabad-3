"""MK2: recall past cases through the memory port.

Memory informs; it never verifies. Relevance was decided by Rama's classify_candidates behind the
port; this module only carries the result forward. Nothing recalled ever reaches Evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from debugagent.pipeline.memory_port import MemoryPort, check_view
from debugagent.pipeline.types import NormalizedDebugCase


def recall_query(case: NormalizedDebugCase) -> str:
    return "; ".join([case.problem_signature, *case.symptoms])


@dataclass(frozen=True)
class MemoryContext:
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
        """Candidates a hypothesis may cite. Empty when memory abstained, even if candidates are
        shown (abstained + contradictory: both sides are displayed, the proposal stays generic)."""
        return {} if self.abstained else {c["case_id"]: c for c in self.candidates}

    def candidate(self, case_id: str) -> dict:
        return next(c for c in self.candidates if c["case_id"] == case_id)


def recall(port: MemoryPort, case: NormalizedDebugCase) -> MemoryContext:
    """One recall. MemoryFailure propagates: an unreachable bank is never 'no memory found'."""
    query = recall_query(case)
    view = check_view(port.recall_and_classify(query))
    return MemoryContext(view["bank_id"], view["recalled_at"], query, view["report"]["candidates"],
                         view["report"]["excluded"], view["abstention"])


def compare_environment(past: dict[str, str], current: dict[str, str | None]) -> list[tuple[str, str, str | None]]:
    """Past-case environment keys that differ from, or are unknown in, the current environment:
    (key, past value, current value or None). For display and for flagging; it never merges."""
    return [(key, value, current.get(key)) for key, value in sorted(past.items()) if current.get(key) != value]
