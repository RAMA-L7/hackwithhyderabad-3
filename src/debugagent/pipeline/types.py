"""Mukul-side Phase 1 types (m1-contract.md section 3, on rama-m0 @ 7254fc3).

Names match the contract so they can be promoted into the shared schemas.py at MK9.
Validation fails closed: from_dict collects every error and raises SchemaError; nothing is coerced.

Proposed contract extensions (docs/phase1-mukul-plan.md section 5, pending Rama's confirmation):
  Q1  Resolution.outcome        - MemoryCase.outcome is required, Resolution had no source for it
  Q5  Hypothesis.ref ("H1"...)  - what VerificationResult.hypothesis_ref points at
  Q4  EvidenceItem.name         - which fact an item is, so verify() can see what is unknown
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

HYPOTHESIS_RELEVANCE_STATES = ("supported", "conditional", "weak-reference", "generic")
VERIFICATION_STATUSES = ("supported", "contradicted", "insufficient_evidence")
ENGINEER_DECISIONS = ("accept", "modify", "reject")
OUTCOME_CLASSES = ("resolved", "workaround", "escalated", "deferred")
HYPOTHESIS_REF_RE = re.compile(r"^H[1-9][0-9]*$")


class SchemaError(ValueError):
    """A value does not satisfy the contract."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class _Check:
    """Collects every violation for one object so the error lists all offending fields."""

    def __init__(self, data: Any, label: str):
        self.label = label
        self.errors: list[str] = []
        if isinstance(data, dict):
            self.data = data
        else:
            self.data = {}
            self.fail(f"expected object, got {type(data).__name__}")

    def fail(self, message: str, key: str | None = None) -> None:
        self.errors.append(f"{self.label}{'.' + key if key else ''}: {message}")

    def has(self, key: str) -> bool:
        if key not in self.data:
            self.fail("required field missing", key)
            return False
        return True

    def text(self, key: str, *, required: bool = True, allow_empty: bool = False, nullable: bool = False):
        if key not in self.data:
            if required:
                self.fail("required field missing", key)
            return None if nullable else ""
        value = self.data[key]
        if value is None and nullable:
            return None
        if not isinstance(value, str):
            self.fail(f"expected string, got {type(value).__name__}", key)
        elif not allow_empty and not value.strip():
            self.fail("must not be empty" if not nullable else "must be null or non-empty", key)
        return value

    def str_list(self, key: str, *, required: bool = True) -> list:
        if key not in self.data:
            if required:
                self.fail("required field missing", key)
            return []
        value = self.data[key]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            self.fail("expected array of strings", key)
            return []
        return list(value)

    def enum(self, key: str, allowed: tuple[str, ...]) -> str:
        value = self.text(key)
        if isinstance(value, str) and value and value not in allowed:
            self.fail(f"'{value}' is not one of {list(allowed)}", key)
        return value

    def flag(self, key: str) -> bool:
        if self.has(key) and not isinstance(self.data[key], bool):
            self.fail("expected boolean", key)
        return self.data.get(key) is True

    def done(self) -> None:
        if self.errors:
            raise SchemaError(self.errors)


class _Dict:
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DebugInput(_Dict):
    description: str
    measurements: list[str] = field(default_factory=list)
    environment_hints: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> "DebugInput":
        c = _Check(data, "DebugInput")
        description = c.text("description")
        measurements = c.str_list("measurements", required=False)
        hints = c.data.get("environment_hints", {})
        if not isinstance(hints, dict) or not all(
            isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip() for k, v in hints.items()
        ):
            c.fail("expected object of non-empty strings", "environment_hints")
            hints = {}
        c.done()
        return cls(description, measurements, dict(hints))


@dataclass(frozen=True)
class NormalizedDebugCase(_Dict):
    problem_signature: str
    symptoms: list[str]
    environment: dict[str, str | None]  # None = the engineer did not state it; never invented
    raw_description: str
    source_case_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> "NormalizedDebugCase":
        c = _Check(data, "NormalizedDebugCase")
        signature = c.text("problem_signature")
        symptoms = c.str_list("symptoms")
        environment = c.data.get("environment") if c.has("environment") else {}
        if not isinstance(environment, dict) or not all(
            isinstance(k, str) and k.strip() and (v is None or (isinstance(v, str) and v.strip()))
            for k, v in environment.items()
        ):
            c.fail("expected object of non-empty strings or null", "environment")
            environment = {}
        raw = c.text("raw_description")
        sources = c.str_list("source_case_ids", required=False)
        c.done()
        return cls(signature, symptoms, dict(environment), raw, sources)


@dataclass(frozen=True)
class EvidenceItem(_Dict):
    name: str                # e.g. "service", "measurement"
    value: str | None        # None exactly when known is False
    source: str              # who/what supplied it now, e.g. "engineer"
    captured_at: str         # ISO-8601 UTC
    known: bool

    @classmethod
    def from_dict(cls, data: Any, label: str = "EvidenceItem") -> "EvidenceItem":
        c = _Check(data, label)
        name = c.text("name")
        value = c.text("value", nullable=True)
        source = c.text("source")
        captured_at = c.text("captured_at")
        known = c.flag("known")
        if known and value is None:
            c.fail("known item must carry a value", "value")
        if not known and value is not None:
            c.fail("unknown item must have value null", "value")
        c.done()
        return cls(name, value, source, captured_at, known)


@dataclass(frozen=True)
class Evidence(_Dict):
    """Current-case observations only. Recalled memory never enters this model."""

    items: list[EvidenceItem]
    unknown_fields: list[str] = field(default_factory=list)

    def known(self) -> dict[str, str]:
        return {item.name: item.value for item in self.items if item.known and item.value is not None}

    @classmethod
    def from_dict(cls, data: Any) -> "Evidence":
        c = _Check(data, "Evidence")
        raw_items = c.data.get("items") if c.has("items") else []
        items: list[EvidenceItem] = []
        if not isinstance(raw_items, list):
            c.fail("expected array", "items")
        else:
            for i, raw in enumerate(raw_items):
                try:
                    items.append(EvidenceItem.from_dict(raw, f"Evidence.items[{i}]"))
                except SchemaError as exc:
                    c.errors.extend(exc.errors)
        unknown = c.str_list("unknown_fields", required=False)
        c.done()
        return cls(items, unknown)


@dataclass(frozen=True)
class Hypothesis(_Dict):
    ref: str
    hypothesis: str
    supporting_case_ids: list[str]
    relevance_state: str
    refutation_conditions: list[str]
    recommended_next_step: str

    @classmethod
    def from_dict(cls, data: Any) -> "Hypothesis":
        c = _Check(data, "Hypothesis")
        ref = c.text("ref")
        if ref and not HYPOTHESIS_REF_RE.match(ref):
            c.fail(f"'{ref}' must look like H1, H2, ...", "ref")
        text = c.text("hypothesis")
        cited = c.str_list("supporting_case_ids")
        state = c.enum("relevance_state", HYPOTHESIS_RELEVANCE_STATES)
        # contract 3.6: generic <=> no citations. A generic hypothesis must never look memory-informed.
        if state == "generic" and cited:
            c.fail("generic hypothesis must not cite cases", "supporting_case_ids")
        elif state in HYPOTHESIS_RELEVANCE_STATES and state != "generic" and not cited:
            c.fail(f"'{state}' requires at least one supporting case", "supporting_case_ids")
        refutations = c.str_list("refutation_conditions")
        next_step = c.text("recommended_next_step")
        c.done()
        return cls(ref, text, cited, state, refutations, next_step)


@dataclass(frozen=True)
class VerificationResult(_Dict):
    hypothesis_ref: str
    status: str
    engineer_decision: str
    relevance_confirmed: bool
    mismatched_environment_fields: list[str] = field(default_factory=list)
    engineer_note: str = ""

    @classmethod
    def from_dict(cls, data: Any) -> "VerificationResult":
        c = _Check(data, "VerificationResult")
        ref = c.text("hypothesis_ref")
        status = c.enum("status", VERIFICATION_STATUSES)
        # engineer_decision is required: a status without a recorded decision is a contract violation (T4)
        decision = c.enum("engineer_decision", ENGINEER_DECISIONS)
        confirmed = c.flag("relevance_confirmed")
        mismatched = c.str_list("mismatched_environment_fields", required=False)
        note = c.text("engineer_note", required=False, allow_empty=True)
        c.done()
        return cls(ref, status, decision, confirmed, mismatched, note)


@dataclass(frozen=True)
class FailedApproach(_Dict):
    approach: str
    why_failed: str


@dataclass(frozen=True)
class Resolution(_Dict):
    """Comes from the engineer, never inferred by the agent."""

    action_taken: str
    observed_result: str
    root_cause_confirmed: str | None
    outcome: str
    failed_approaches_this_session: list[FailedApproach] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> "Resolution":
        c = _Check(data, "Resolution")
        action = c.text("action_taken")
        observed = c.text("observed_result")
        root_cause = c.text("root_cause_confirmed", nullable=True)  # key required, null allowed
        outcome = c.enum("outcome", OUTCOME_CLASSES)
        failed: list[FailedApproach] = []
        raw_failed = c.data.get("failed_approaches_this_session", [])
        if not isinstance(raw_failed, list):
            c.fail("expected array", "failed_approaches_this_session")
        else:
            for i, raw in enumerate(raw_failed):
                sub = _Check(raw, f"Resolution.failed_approaches_this_session[{i}]")
                item = FailedApproach(sub.text("approach"), sub.text("why_failed"))
                c.errors.extend(sub.errors)
                failed.append(item)
        refs = c.str_list("evidence_refs", required=False)
        c.done()
        return cls(action, observed, root_cause, outcome, failed, refs)
