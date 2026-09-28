"""One error hierarchy for the whole agent.

Every error carries a message written for the engineer; the CLI prints `str(error)` as one line.
Two rules the hierarchy makes hard to break: a memory failure is never "no relevant memory", and an
LLM failure is never "the model found nothing relevant".
"""

from __future__ import annotations


class DebugAgentError(Exception):
    """Base class. Anything the CLI catches and prints as one line."""


# ---- input and validation -------------------------------------------------------------------------

class SchemaError(DebugAgentError, ValueError):
    """A value does not satisfy the contract. Lists every offending field at once."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class InputError(DebugAgentError):
    """The engineer's input is unusable (e.g. a blank description)."""


class NormalizationError(DebugAgentError):
    """The input contradicts itself (e.g. a hint disagrees with the text)."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class EvidenceError(DebugAgentError):
    """Engineer-supplied facts contradict each other."""


class VerificationError(DebugAgentError):
    """A step cannot proceed: a decision or answer the contract requires is missing or invalid."""


class SessionAborted(DebugAgentError):
    """The engineer stopped the session before it finished. Nothing is retained."""


# ---- memory ----------------------------------------------------------------------------------------

MEMORY_FAILURE_KINDS = ("unavailable", "auth", "schema")


class MemoryFailure(DebugAgentError):
    """Memory could not answer. Always reaches the engineer as an error, never as 'no memory found'."""

    def __init__(self, kind: str, message: str):
        if kind not in MEMORY_FAILURE_KINDS:
            raise ValueError(f"unknown MemoryFailure kind {kind!r}")
        super().__init__(f"memory {kind}: {message}")
        self.kind = kind


# ---- LLM -------------------------------------------------------------------------------------------

class LLMError(DebugAgentError):
    def __init__(self, message: str, *, error_class: str, attempts: list[dict] | None = None):
        super().__init__(message)
        self.error_class = error_class
        self.attempts = list(attempts or [])


class LLMConfigError(LLMError):
    """Bad request, unknown model, billing, or local misconfiguration. Never fails over."""


class LLMAuthError(LLMError):
    """Credentials rejected. Never fails over."""


class LLMUnavailable(LLMError):
    """No route could be reached or served a response."""


class StructuredOutputError(LLMError):
    """Routes answered, but no output passed local validation."""
