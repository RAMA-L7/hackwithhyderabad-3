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


class MemoryPersistError(MemoryError):
    """LOCAL persistence failed, and the remote outcome of the same operation may differ.

    Raised when a local write fails after a remote write may already have succeeded - the
    idempotency ledger could not be updated. It deliberately does NOT imply the remote write was
    rolled back: it was not, and cannot be from here. The two systems are not in a transaction.

    Callers must not treat this as "nothing was stored": the remote document may exist while the
    local record does not.
    """


class MemoryAmbiguousError(MemoryUnavailable):
    """A remote operation whose outcome is UNKNOWN: the request may or may not have been applied.

    Distinct from a plain `MemoryUnavailable`, which means the remote call is known to have failed
    without storing. Raised only for transport failures where the request could have been
    transmitted before the connection broke (timeouts, connection resets, interrupted responses).

    A retry is state-idempotent at the remote document level (P3-2B: a fixed `document_id` with
    `update_mode="replace"` converges on one document), but a retry is still another remote
    operation and another extraction cost. This is NOT exactly-once.
    """


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
