"""MemoryStore interface — the only boundary the agent pipeline may use.

Hindsight-specific types never cross this boundary.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from debugagent.schemas import MemoryCase, RecallSet, RetentionDecision


class MemoryError(RuntimeError):
    """Base error for the memory layer."""


class MemoryUnavailable(MemoryError):
    """The memory backend could not be reached."""


class MemoryAuthError(MemoryError):
    """The memory backend rejected credentials."""


class MemorySchemaError(MemoryError):
    """A value did not satisfy the frozen schema; nothing was stored."""


@runtime_checkable
class MemoryStore(Protocol):
    def recall(
        self,
        *,
        query: str,
        tags: list[str] | None = None,
        types: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> RecallSet:
        """Return recalled cases with provenance. Never decides relevance."""

    def retain(self, case: MemoryCase) -> RetentionDecision:
        """Store a validated case. Idempotent on case_key. Never decides engineering outcomes."""

    def update(self, case_id: str, *, text: str, reason: str) -> None:
        """Correct a stored case through the backend curation API."""

    def invalidate(self, case_id: str, *, reason: str) -> None:
        """Retire a stale or superseded case reversibly."""
