"""Evidence service (MK6 part 1): the current-case Evidence model.

Built only from what the engineer supplies now. No method takes recalled memory: memory informs,
evidence verifies.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Mapping

from debugagent.domain.errors import EvidenceError
from debugagent.domain.models import Evidence, EvidenceItem, NormalizedDebugCase

OBSERVATION = "observation"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EvidenceService:
    def __init__(self, source: str = "engineer", clock=now_utc):
        self.source = source
        self.clock = clock

    def build(self, case: NormalizedDebugCase) -> Evidence:
        """Environment facts (known or explicitly unknown) plus the observed symptoms."""
        at = self.clock()
        items = [self._item(key, value, at) for key, value in case.environment.items()]
        items += [self._item(OBSERVATION, symptom, at) for symptom in case.symptoms]
        return Evidence(items, self._unknown(items))

    def add_facts(self, evidence: Evidence,
                  facts: Mapping[str, str | None] | Iterable[tuple[str, str | None]]) -> Evidence:
        """Append facts (a mapping, or name/value pairs so several observations can be given). Filling an
        unknown field replaces its placeholder; restating a known field with a different value fails closed.
        Returns a new Evidence; the original is unchanged."""
        at = self.clock()
        items = list(evidence.items)
        for raw_name, raw_value in (facts.items() if isinstance(facts, Mapping) else facts):
            name = raw_name.strip().lower()
            value = raw_value.strip() if isinstance(raw_value, str) and raw_value.strip() else None
            if name == OBSERVATION:
                if value is not None:
                    items.append(self._item(name, value, at))
                continue
            index = next((i for i, item in enumerate(items) if item.name == name), None)
            if index is None:
                items.append(self._item(name, value, at))
            elif not items[index].known:
                items[index] = self._item(name, value, at)
            elif value is not None and items[index].value != value:
                raise EvidenceError(f"{name}: already recorded as '{items[index].value}', now given as '{value}'")
        return Evidence(items, self._unknown(items))

    def _item(self, name: str, value: str | None, captured_at: str) -> EvidenceItem:
        return EvidenceItem(name, value, self.source, captured_at, value is not None)

    @staticmethod
    def _unknown(items: list[EvidenceItem]) -> list[str]:
        return [item.name for item in items if not item.known]
