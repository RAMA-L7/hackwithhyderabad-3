"""Frozen Phase 1 schemas — implemented verbatim from docs/m1-contract.md section 3.

Shared artefact: changes require both team members in the same commit.
Validation fails closed; no coercion of invalid values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

OUTCOME_CLASSES = ("resolved", "workaround", "escalated", "deferred")

RELEVANCE_CLASSES = ("relevant", "partial", "irrelevant", "contradictory", "stale")

HYPOTHESIS_RELEVANCE_STATES = ("supported", "conditional", "weak-reference", "generic")

VERIFICATION_STATUSES = ("supported", "contradicted", "insufficient_evidence")

ENGINEER_DECISIONS = ("accept", "modify", "reject")

ENV_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]*$")


class SchemaError(ValueError):
    """Raised when a value does not satisfy the frozen schema."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _err(errors: list[str], message: str) -> None:
    errors.append(message)


def _require_mapping(value: Any, label: str, errors: list[str]) -> dict:
    if not isinstance(value, dict):
        _err(errors, f"{label}: expected object, got {type(value).__name__}")
        return {}
    return value


def _require_str(value: Any, label: str, errors: list[str], *, allow_empty: bool = False) -> None:
    if not isinstance(value, str):
        _err(errors, f"{label}: expected string, got {type(value).__name__}")
        return
    if not allow_empty and not value.strip():
        _err(errors, f"{label}: must not be empty")


def _require_str_list(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, list):
        _err(errors, f"{label}: expected array, got {type(value).__name__}")
        return
    for index, item in enumerate(value):
        _require_str(item, f"{label}[{index}]", errors, allow_empty=True)


def _require_enum(value: Any, label: str, allowed: tuple[str, ...], errors: list[str]) -> None:
    if not isinstance(value, str):
        _err(errors, f"{label}: expected string, got {type(value).__name__}")
        return
    if value not in allowed:
        _err(errors, f"{label}: '{value}' is not one of {list(allowed)}")


def _require_number(value: Any, label: str, errors: list[str]) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _err(errors, f"{label}: expected number, got {type(value).__name__}")


@dataclass(frozen=True)
class FailedApproach:
    approach: str
    why_failed: str

    def to_dict(self) -> dict:
        return {"approach": self.approach, "why_failed": self.why_failed}

    @classmethod
    def from_dict(cls, data: Any, label: str, errors: list[str]) -> "FailedApproach":
        mapping = _require_mapping(data, label, errors)
        _require_str(mapping.get("approach"), f"{label}.approach", errors)
        _require_str(mapping.get("why_failed"), f"{label}.why_failed", errors)
        return cls(
            approach=str(mapping.get("approach", "")),
            why_failed=str(mapping.get("why_failed", "")),
        )


@dataclass(frozen=True)
class MemoryCase:
    problem_signature: str
    symptoms: list[str]
    environment: dict[str, str]
    observed_evidence: list[str]
    investigation_trace: list[str]
    failed_approaches: list[FailedApproach]
    root_cause: str | None
    resolution: str | None
    outcome: str
    verification_notes: str
    case_id: str | None = None
    session_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "problem_signature": self.problem_signature,
            "symptoms": list(self.symptoms),
            "environment": dict(self.environment),
            "observed_evidence": list(self.observed_evidence),
            "investigation_trace": list(self.investigation_trace),
            "failed_approaches": [item.to_dict() for item in self.failed_approaches],
            "root_cause": self.root_cause,
            "resolution": self.resolution,
            "outcome": self.outcome,
            "verification_notes": self.verification_notes,
            # Identity fields are optional (`str | None`), and from_dict() already accepts them.
            # Emitting them here keeps the round trip lossless: dropping them silently
            # discarded session identity, which is half of compute_case_key()'s input.
            "case_id": self.case_id,
            "session_id": self.session_id,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "MemoryCase":
        errors: list[str] = []
        mapping = _require_mapping(data, "MemoryCase", errors)
        for key in (
            "problem_signature",
            "symptoms",
            "environment",
            "observed_evidence",
            "investigation_trace",
            "failed_approaches",
            "root_cause",
            "resolution",
            "outcome",
            "verification_notes",
        ):
            if key not in mapping:
                _err(errors, f"MemoryCase.{key}: required field missing")

        _require_str(mapping.get("problem_signature"), "MemoryCase.problem_signature", errors)
        _require_str_list(mapping.get("symptoms"), "MemoryCase.symptoms", errors)

        environment = mapping.get("environment")
        if not isinstance(environment, dict):
            _err(errors, "MemoryCase.environment: expected object")
            environment = {}
        else:
            for key, value in environment.items():
                _require_str(key, "MemoryCase.environment key", errors)
                _require_str(value, f"MemoryCase.environment.{key}", errors)

        _require_str_list(mapping.get("observed_evidence"), "MemoryCase.observed_evidence", errors)
        _require_str_list(mapping.get("investigation_trace"), "MemoryCase.investigation_trace", errors)

        raw_failed = mapping.get("failed_approaches")
        failed: list[FailedApproach] = []
        if not isinstance(raw_failed, list):
            _err(errors, "MemoryCase.failed_approaches: expected array")
        else:
            for index, item in enumerate(raw_failed):
                failed.append(
                    FailedApproach.from_dict(item, f"MemoryCase.failed_approaches[{index}]", errors)
                )

        for optional_field in ("root_cause", "resolution"):
            value = mapping.get(optional_field)
            if value is not None and not isinstance(value, str):
                _err(errors, f"MemoryCase.{optional_field}: expected string or null")
            elif isinstance(value, str) and not value.strip():
                _err(errors, f"MemoryCase.{optional_field}: must be null or non-empty")

        _require_enum(mapping.get("outcome"), "MemoryCase.outcome", OUTCOME_CLASSES, errors)
        _require_str(mapping.get("verification_notes"), "MemoryCase.verification_notes", errors)

        if errors:
            raise SchemaError(errors)

        return cls(
            problem_signature=str(mapping["problem_signature"]),
            symptoms=list(mapping["symptoms"]),
            environment=dict(environment),
            observed_evidence=list(mapping["observed_evidence"]),
            investigation_trace=list(mapping["investigation_trace"]),
            failed_approaches=failed,
            root_cause=mapping.get("root_cause"),
            resolution=mapping.get("resolution"),
            outcome=str(mapping["outcome"]),
            verification_notes=str(mapping["verification_notes"]),
            case_id=mapping.get("case_id"),
            session_id=mapping.get("session_id"),
        )


@dataclass(frozen=True)
class RecallResult:
    case_id: str
    text: str
    score_final: float
    score_semantic: float | None = None
    score_keyword: float | None = None
    environment: dict[str, str] = field(default_factory=dict)
    outcome: str | None = None
    root_cause_key: str | None = None
    mentioned_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "text": self.text,
            "score_final": self.score_final,
            "score_semantic": self.score_semantic,
            "score_keyword": self.score_keyword,
            "environment": dict(self.environment),
            "outcome": self.outcome,
            "root_cause_key": self.root_cause_key,
            "mentioned_at": self.mentioned_at,
        }


@dataclass(frozen=True)
class RecallSet:
    items: list[RecallResult]
    recalled_at: str
    bank_id: str

    def to_dict(self) -> dict:
        return {
            "items": [item.to_dict() for item in self.items],
            "recalled_at": self.recalled_at,
            "bank_id": self.bank_id,
        }

    def provenance(self) -> list[str]:
        return [item.case_id for item in self.items]


@dataclass(frozen=True)
class MatchCandidate:
    case_id: str
    relevance_class: str
    score_final: float
    environment: dict[str, str]
    reason: str
    conflicts_with: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "relevance_class": self.relevance_class,
            "score_final": self.score_final,
            "environment": dict(self.environment),
            "reason": self.reason,
            "conflicts_with": list(self.conflicts_with),
        }


@dataclass(frozen=True)
class MatchReport:
    candidates: list[MatchCandidate]
    excluded: list[MatchCandidate]
    top_score: float
    threshold_used: float

    def to_dict(self) -> dict:
        return {
            "candidates": [item.to_dict() for item in self.candidates],
            "excluded": [item.to_dict() for item in self.excluded],
            "top_score": self.top_score,
            "threshold_used": self.threshold_used,
        }

    def provenance(self) -> list[str]:
        return [item.case_id for item in self.candidates]


@dataclass(frozen=True)
class AbstentionDecision:
    abstained: bool
    relevance_class: str
    top_score: float
    threshold_used: float
    reason: str

    def to_dict(self) -> dict:
        return {
            "abstained": self.abstained,
            "relevance_class": self.relevance_class,
            "top_score": self.top_score,
            "threshold_used": self.threshold_used,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RetentionDecision:
    retained: bool
    reason: str
    memory_case_id: str | None = None
    validated: bool = True
    case_key: str | None = None

    def to_dict(self) -> dict:
        return {
            "retained": self.retained,
            "reason": self.reason,
            "memory_case_id": self.memory_case_id,
            "validated": self.validated,
            "case_key": self.case_key,
        }
