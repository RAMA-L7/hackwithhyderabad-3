"""Loop test support: import path wiring and an offline FakeMemoryPort (no Hindsight, no rama-m0 code)."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from debugagent.pipeline.memory_port import MemoryFailure  # noqa: E402


def candidate(case_id, relevance_class="relevant", *, environment=None, conflicts_with=None,
              text="synthetic past case", outcome="resolved", score_final=0.9, reason="test reason"):
    return {
        "case_id": case_id,
        "relevance_class": relevance_class,
        "score_final": score_final,
        "environment": environment or {"service": "media-uploader", "proxy": "nginx-1.25"},
        "reason": reason,
        "conflicts_with": conflicts_with or [],
        "text": text,
        "outcome": outcome,
    }


def view(candidates=(), excluded=(), *, abstained=False, relevance_class="relevant",
         reason="test abstention reason"):
    return {
        "bank_id": "fake-bank",
        "recalled_at": "2026-09-28T00:00:00+00:00",
        "report": {"candidates": list(candidates), "excluded": list(excluded),
                   "top_score": 0.9 if candidates else 0.0, "threshold_used": 0.05},
        "abstention": {"abstained": abstained, "relevance_class": relevance_class,
                       "top_score": 0.9 if candidates else 0.0, "threshold_used": 0.05, "reason": reason},
    }


RELEVANT_VIEW = view([candidate("3f9a1c07b2e4d815")],
                     [candidate("a7c2e9f04b1d6e38", "irrelevant", environment={"service": "batch-runner"})])
PARTIAL_VIEW = view([candidate("seed-001", "partial", environment={"service": "orders-api"})],
                    relevance_class="partial")
CONTRADICTORY_VIEW = view(
    [candidate("seed-001", "contradictory", environment={"service": "orders-api"},
               conflicts_with=["same service 'orders-api' with different root cause", "seed-002"]),
     candidate("seed-002", "contradictory", environment={"service": "orders-api"},
               conflicts_with=["same service 'orders-api' with different root cause", "seed-001"])],
    abstained=True, relevance_class="contradictory")
ABSTAINED_VIEW = view(abstained=True, relevance_class="irrelevant",
                      reason="memory returned no candidates; proceeding without historical memory")


class FakeMemoryPort:
    """Canned MemoryView for recall; retain() is a spy. `fail` raises a MemoryFailure on every call."""

    def __init__(self, memory_view=None, *, fail: MemoryFailure | None = None, retained: bool = True):
        self.view = copy.deepcopy(memory_view if memory_view is not None else RELEVANT_VIEW)
        self.fail = fail
        self.retained_flag = retained
        self.queries: list[str] = []
        self.retained: list[dict] = []

    def recall_and_classify(self, query: str) -> dict:
        self.queries.append(query)
        if self.fail:
            raise self.fail
        return copy.deepcopy(self.view)

    def retain(self, case: dict) -> dict:
        if self.fail:
            raise self.fail
        self.retained.append(copy.deepcopy(case))
        return {"retained": self.retained_flag, "reason": "fake store", "memory_case_id": "fake-fact-1",
                "validated": True, "case_key": "fake-key-1"}
