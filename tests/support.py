"""Shared test support: import path wiring and an offline fake Hindsight client."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class FakeScores:
    def __init__(self, final: float, semantic: float | None = None, keyword: float | None = None):
        self.final = final
        self.semantic = semantic
        self.keyword = keyword


class FakeMemory:
    def __init__(self, text: str, metadata: dict, scores: FakeScores, mentioned_at: str | None = None):
        self.text = text
        self.metadata = metadata
        self.scores = scores
        self.mentioned_at = mentioned_at
        self.type = "world"
        self.id = "fake-id"


class FakeRetainResponse:
    def __init__(self, memory_id: str):
        self.memory_id = memory_id


class FakeHindsightClient:
    def __init__(self, recall_results: list[FakeMemory] | None = None):
        self.retained: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.banks: set[str] = set()
        self.created_banks: list[str] = []
        self.recall_calls: list[dict[str, Any]] = []
        self._recall_results = recall_results or []
        self.closed = False

    def get_bank_config(self, bank_id: str) -> dict:
        if bank_id not in self.banks:
            raise RuntimeError("404 not found")
        return {"bank_id": bank_id}

    def create_bank(self, bank_id: str, **_kwargs: Any) -> str:
        self.banks.add(bank_id)
        self.created_banks.append(bank_id)
        return bank_id

    def retain(self, **kwargs: Any) -> FakeRetainResponse:
        self.retained.append(kwargs)
        return FakeRetainResponse(memory_id=f"mem-{len(self.retained)}")

    def recall(self, **kwargs: Any) -> Any:
        self.recall_calls.append(kwargs)
        results = self._recall_results if self._recall_results is not None else []
        return type("R", (), {"results": results})()

    def update_memory(self, **kwargs: Any) -> Any:
        self.updates.append(kwargs)
        return None

    def close(self) -> None:
        self.closed = True


def memory_config(tmp_dir: Path, **overrides: Any):
    from debugagent.config import MemoryConfig

    defaults = dict(
        hindsight_url="https://memory.invalid",
        hindsight_api_key="test-key-not-real",
        bank_id="test-bank",
        max_tokens=1024,
        min_final_score=0.05,
        weak_reference_floor=0.6,
        stale_after_days=365,
        recall_types=("world", "experience"),
        ledger_path=tmp_dir / "ledger.json",
    )
    defaults.update(overrides)
    return MemoryConfig(**defaults)
