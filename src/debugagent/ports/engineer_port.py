"""Engineer port: the human in the loop. The terminal controller implements it; tests script it.

The agent proposes; only this port decides.
"""

from __future__ import annotations

from typing import Protocol

from debugagent.domain.investigation import EngineerDecision, Session
from debugagent.domain.models import Evidence, Hypothesis


class EngineerPort(Protocol):
    def report(self, stage: str, session: Session) -> None:
        """A stage finished: 'memory', 'evidence', 'proposal', 'decision' or 'retention'.
        The implementation decides how to present it (the terminal renders the trust sections)."""

    def current_facts(self, evidence: Evidence) -> dict[str, str | None] | list[tuple[str, str | None]]:
        """Facts observed now: name -> value (None for 'unknown'), or (name, value) pairs; name 'observation'
        may repeat. Never recalled content."""

    def decide(self, hypothesis: Hypothesis, mismatched: list[str], missing: list[str]) -> EngineerDecision:
        """The engineer's verdict on one hypothesis."""

    def resolve(self, session: Session) -> dict | None:
        """Keyword answers for the resolution, or None when the issue was not resolved."""
