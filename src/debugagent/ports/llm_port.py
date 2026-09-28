"""LLM port: what the hypothesis service needs from a language model. The router adapter implements it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(frozen=True)
class StructuredResult:
    data: dict            # validated against the schema and the caller's check; never raw text
    provider: str
    model: str
    fallback_used: bool
    attempts: list[dict]


class LLMPort(Protocol):
    def complete_structured(self, prompt: str, *, schema: dict, name: str = "response",
                            check: Callable[[dict], list[str]] | None = None) -> StructuredResult:
        """Return validated JSON or raise an LLMError. `check` failures count as invalid output."""
