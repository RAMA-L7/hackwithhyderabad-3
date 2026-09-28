"""Deterministic seed loading with idempotency reporting."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from debugagent.memory.store import MemoryStore
from debugagent.schemas import MemoryCase, SchemaError

DEFAULT_SEED_FILE = Path(__file__).with_name("cases.json")


@dataclass
class SeedLoadReport:
    inserted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "inserted": list(self.inserted),
            "skipped": list(self.skipped),
            "rejected": list(self.rejected),
        }


def load_seed_file(path: Path | str | None = None) -> list[MemoryCase]:
    source = Path(path) if path else DEFAULT_SEED_FILE
    payload = json.loads(source.read_text(encoding="utf-8"))
    entries = payload.get("cases")
    if not isinstance(entries, list) or not entries:
        raise ValueError("seed file contains no cases")
    cases: list[MemoryCase] = []
    for index, entry in enumerate(entries):
        try:
            cases.append(MemoryCase.from_dict(entry))
        except SchemaError as exc:
            raise ValueError(f"seed case #{index} invalid: {exc.errors}") from exc
    return cases


def load_seed_cases(store: MemoryStore, path: Path | str | None = None) -> SeedLoadReport:
    report = SeedLoadReport()
    for case in load_seed_file(path):
        try:
            decision = store.retain(case)
        except Exception as exc:  # noqa: BLE001
            report.rejected.append(f"{case.case_id or case.problem_signature[:40]}: {exc}")
            continue
        label = case.case_id or case.problem_signature[:40]
        if decision.retained:
            report.inserted.append(label)
        else:
            report.skipped.append(f"{label}: {decision.reason}")
    return report
