"""MK6 part 1: the current-case Evidence model.

Built only from what the engineer supplies now. There is deliberately no parameter through which
recalled memory could enter: memory informs, evidence verifies.
"""

from __future__ import annotations

from datetime import datetime, timezone

from debugagent.pipeline.types import Evidence, EvidenceItem, NormalizedDebugCase

OBSERVATION = "observation"


class EvidenceError(ValueError):
    """Engineer-supplied facts contradict each other."""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _item(name: str, value: str | None, source: str, captured_at: str) -> EvidenceItem:
    return EvidenceItem(name, value, source, captured_at, value is not None)


def _unknown(items: list[EvidenceItem]) -> list[str]:
    return [item.name for item in items if not item.known]


def build_evidence(case: NormalizedDebugCase, *, source: str = "engineer", captured_at: str | None = None) -> Evidence:
    """Environment facts (known or explicitly unknown) plus the observed symptoms."""
    at = captured_at or now_utc()
    items = [_item(key, value, source, at) for key, value in case.environment.items()]
    items += [_item(OBSERVATION, symptom, source, at) for symptom in case.symptoms]
    return Evidence(items, _unknown(items))


def add_facts(evidence: Evidence, facts, *, source: str = "engineer", captured_at: str | None = None) -> Evidence:
    """Append facts: a name -> value mapping, or (name, value) pairs so several observations can be given.
    Filling an unknown field replaces its placeholder; restating a known field with a different value fails
    closed. Returns a new Evidence; the original is unchanged."""
    at = captured_at or now_utc()
    items = list(evidence.items)
    for name, value in (facts.items() if isinstance(facts, dict) else facts):
        name = name.strip().lower()
        value = value.strip() if isinstance(value, str) and value.strip() else None
        if name == OBSERVATION:
            if value is not None:
                items.append(_item(name, value, source, at))
            continue
        existing = next((i for i, item in enumerate(items) if item.name == name), None)
        if existing is None:
            items.append(_item(name, value, source, at))
        elif not items[existing].known:
            items[existing] = _item(name, value, source, at)
        elif value is not None and items[existing].value != value:
            raise EvidenceError(f"{name}: already recorded as '{items[existing].value}', now given as '{value}'")
    return Evidence(items, _unknown(items))
