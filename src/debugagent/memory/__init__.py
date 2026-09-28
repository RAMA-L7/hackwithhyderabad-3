"""Memory layer for the debugging agent (Rama's Phase 1 area)."""

from debugagent.memory.matching import AbstentionPolicy, classify_candidates, normalize_query
from debugagent.memory.store import (
    MemoryAuthError,
    MemoryError,
    MemorySchemaError,
    MemoryStore,
    MemoryUnavailable,
)

__all__ = [
    "AbstentionPolicy",
    "MemoryAuthError",
    "MemoryError",
    "MemorySchemaError",
    "MemoryStore",
    "MemoryUnavailable",
    "classify_candidates",
    "normalize_query",
]
