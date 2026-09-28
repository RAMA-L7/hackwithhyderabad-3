"""Environment-driven configuration for the memory layer. Secrets are read, never printed."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BANK_ID = "debugagent"
DEFAULT_MAX_TOKENS = 1024
DEFAULT_MIN_FINAL_SCORE = 0.05
DEFAULT_WEAK_REFERENCE_FLOOR = 0.6
DEFAULT_STALE_AFTER_DAYS = 365
# Provisional, raised from 0.70 on 2026-09-28 from every semantic score measured when `final` was below its
# floor: unrelated cases 0.641, 0.678 (Rama's rehearsals), 0.7016, 0.7084 (fresh-bank rehearsal: batch-runner
# admitted for a billing issue, media-uploader for an orders-api issue); vague 0.766 (M0); genuine collapsed
# match 0.78 (M1, 26+ cases) and 0.788 (unit tests). 0.75 rejects every unrelated case measured and keeps every
# genuine fallback match. Still a calibration parameter, not a validated constant (Phase 2, item 2.1).
DEFAULT_SEMANTIC_FLOOR = 0.75


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = _get(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = _get(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class MemoryConfig:
    hindsight_url: str
    hindsight_api_key: str
    bank_id: str
    max_tokens: int
    min_final_score: float
    weak_reference_floor: float
    stale_after_days: int
    recall_types: tuple[str, ...]
    ledger_path: Path
    semantic_floor: float = DEFAULT_SEMANTIC_FLOOR

    @property
    def configured(self) -> bool:
        return bool(self.hindsight_url)

    def missing_fields(self) -> list[str]:
        missing = []
        if not self.hindsight_url:
            missing.append("HINDSIGHT_URL")
        if not self.hindsight_api_key:
            missing.append("HINDSIGHT_API_KEY")
        return missing


def load_memory_config(data_dir: str | None = None) -> MemoryConfig:
    base = Path(data_dir) if data_dir else Path(os.environ.get("DEBUGAGENT_DATA_DIR", "data"))
    ledger = base / "memory_ledger.json"
    return MemoryConfig(
        hindsight_url=_get("HINDSIGHT_URL"),
        hindsight_api_key=_get("HINDSIGHT_API_KEY"),
        bank_id=_get("HINDSIGHT_BANK_ID", DEFAULT_BANK_ID),
        max_tokens=_int("HINDSIGHT_RECALL_MAX_TOKENS", DEFAULT_MAX_TOKENS),
        min_final_score=_float("DEBUGAGENT_MIN_FINAL_SCORE", DEFAULT_MIN_FINAL_SCORE),
        weak_reference_floor=_float("DEBUGAGENT_WEAK_REFERENCE_FLOOR", DEFAULT_WEAK_REFERENCE_FLOOR),
        stale_after_days=_int("DEBUGAGENT_STALE_AFTER_DAYS", DEFAULT_STALE_AFTER_DAYS),
        semantic_floor=_float("DEBUGAGENT_SEMANTIC_FLOOR", DEFAULT_SEMANTIC_FLOOR),
        recall_types=("world", "experience"),
        ledger_path=ledger,
    )


def redact(value: str) -> str:
    return "<set:redacted>" if value else "<unset>"
