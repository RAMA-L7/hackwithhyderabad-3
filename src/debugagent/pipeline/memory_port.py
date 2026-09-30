"""The only seam between the pipeline and Rama's memory layer (docs/phase1-mukul-plan.md section 2).

Plain dicts shaped like the to_dict() output of Rama's dataclasses at rama-m0 @ 7254fc3, so the
MK9 adapter is a thin wrapper. Nothing here imports hindsight_client or rama-m0 code.

Everything crossing the seam is checked here (check_view / check_retention): a malformed view is
a MemoryFailure(kind="schema"), never quietly read as "no relevant memory".
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Protocol

RELEVANCE_CLASSES = ("relevant", "partial", "irrelevant", "contradictory", "stale")
# P3-2C adds two kinds so a caller can tell three situations apart:
#   "unavailable"  - the remote call is known to have failed without storing (clean retry)
#   "ambiguous"    - the remote outcome is UNKNOWN; the request may already have been applied
#   "persist"      - the remote write may have succeeded, but the local ledger could not be written
#                    (a cross-system atomicity gap; nothing was rolled back)
# The two new kinds are additive, so every pre-existing "unavailable"/"auth"/"schema" caller is
# unaffected.
FAILURE_KINDS = ("unavailable", "ambiguous", "persist", "auth", "schema")


class MemoryFailure(RuntimeError):
    """Memory could not answer. Must reach the engineer as an error, never as 'no memory found'."""

    def __init__(self, kind: str, message: str):
        if kind not in FAILURE_KINDS:
            raise ValueError(f"unknown MemoryFailure kind {kind!r}")
        super().__init__(f"memory {kind}: {message}")
        self.kind = kind


class MemoryPort(Protocol):
    def recall_and_classify(self, query: str) -> dict:
        """Return a MemoryView. Raises MemoryFailure."""

    def retain(self, case: dict) -> dict:
        """Store one MemoryCase dict; return a RetentionDecision dict. Raises MemoryFailure."""


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_candidate(item: Any, label: str, errors: list[str]) -> None:
    if not isinstance(item, dict):
        errors.append(f"{label}: expected object")
        return
    if not isinstance(item.get("case_id"), str) or not item["case_id"].strip():
        errors.append(f"{label}.case_id: expected non-empty string")
    if item.get("relevance_class") not in RELEVANCE_CLASSES:
        errors.append(f"{label}.relevance_class: expected one of {list(RELEVANCE_CLASSES)}")
    if not _number(item.get("score_final")):
        errors.append(f"{label}.score_final: expected number")
    env = item.get("environment")
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        errors.append(f"{label}.environment: expected object of strings")
    for key in ("reason", "text"):
        if not isinstance(item.get(key), str):
            errors.append(f"{label}.{key}: expected string")
    conflicts = item.get("conflicts_with")
    if not isinstance(conflicts, list) or not all(isinstance(x, str) for x in conflicts):
        errors.append(f"{label}.conflicts_with: expected array of strings")
    if item.get("outcome") is not None and not isinstance(item.get("outcome"), str):
        errors.append(f"{label}.outcome: expected string or null")


def check_view(view: Any) -> dict:
    """Validate a MemoryView at the boundary. Returns it unchanged, or raises MemoryFailure('schema')."""
    errors: list[str] = []
    if not isinstance(view, dict):
        raise MemoryFailure("schema", "MemoryView: expected object")
    for key in ("bank_id", "recalled_at"):
        if not isinstance(view.get(key), str):
            errors.append(f"MemoryView.{key}: expected string")

    report = view.get("report")
    if not isinstance(report, dict):
        errors.append("MemoryView.report: expected object")
    else:
        for group in ("candidates", "excluded"):
            items = report.get(group)
            if not isinstance(items, list):
                errors.append(f"MemoryView.report.{group}: expected array")
                continue
            for i, item in enumerate(items):
                _check_candidate(item, f"MemoryView.report.{group}[{i}]", errors)
        for key in ("top_score", "threshold_used"):
            if not _number(report.get(key)):
                errors.append(f"MemoryView.report.{key}: expected number")

    abstention = view.get("abstention")
    if not isinstance(abstention, dict):
        errors.append("MemoryView.abstention: expected object")
    else:
        if not isinstance(abstention.get("abstained"), bool):
            errors.append("MemoryView.abstention.abstained: expected boolean")
        if abstention.get("relevance_class") not in RELEVANCE_CLASSES:
            errors.append("MemoryView.abstention.relevance_class: invalid")
        if not isinstance(abstention.get("reason"), str):
            errors.append("MemoryView.abstention.reason: expected string")
        for key in ("top_score", "threshold_used"):
            if not _number(abstention.get(key)):
                errors.append(f"MemoryView.abstention.{key}: expected number")

    if errors:
        raise MemoryFailure("schema", "; ".join(errors))
    return view


def check_retention(decision: Any) -> dict:
    """Validate a RetentionDecision dict. Returns it unchanged, or raises MemoryFailure('schema')."""
    errors: list[str] = []
    if not isinstance(decision, dict):
        raise MemoryFailure("schema", "RetentionDecision: expected object")
    for key in ("retained", "validated"):
        if not isinstance(decision.get(key), bool):
            errors.append(f"RetentionDecision.{key}: expected boolean")
    if not isinstance(decision.get("reason"), str):
        errors.append("RetentionDecision.reason: expected string")
    for key in ("memory_case_id", "case_key"):
        if decision.get(key) is not None and not isinstance(decision.get(key), str):
            errors.append(f"RetentionDecision.{key}: expected string or null")
    if errors:
        raise MemoryFailure("schema", "; ".join(errors))
    return decision


class OfflineMemoryPort:
    """Pre-MK9 stand-in so the CLI can be rehearsed without Hindsight.

    Serves canned MemoryViews from a JSON file ({"views": [{"match": [terms], "view": {...}}], "default": {...}};
    the first entry whose terms all appear in the query wins) and appends retained cases to a local JSONL
    file. It is not Hindsight and does not validate cases like Rama's layer does, and says so in every
    retention reason. MK9 replaces it with the Hindsight adapter.
    """

    def __init__(self, views_path: str, retained_path: str):
        try:
            data = json.loads(Path(views_path).read_text())
        except FileNotFoundError:
            raise MemoryFailure("unavailable", f"offline memory file not found: {views_path}") from None
        except json.JSONDecodeError as exc:
            raise MemoryFailure("schema", f"offline memory file is not JSON: {exc}") from None
        if not isinstance(data, dict) or not isinstance(data.get("views"), list) or "default" not in data:
            raise MemoryFailure("schema", "offline memory file needs 'views' (list) and 'default'")
        self.views = data["views"]
        self.default = data["default"]
        self.retained_path = Path(retained_path)

    def recall_and_classify(self, query: str) -> dict:
        text = query.lower()
        for entry in self.views:
            if all(term.lower() in text for term in entry.get("match", [])):
                return copy.deepcopy(entry["view"])
        return copy.deepcopy(self.default)

    def retain(self, case: dict) -> dict:
        self.retained_path.parent.mkdir(parents=True, exist_ok=True)
        with self.retained_path.open("a") as handle:
            handle.write(json.dumps(case) + "\n")
        count = len(self.retained_path.read_text().splitlines())
        return {"retained": True, "reason": f"written to the offline file {self.retained_path} "
                                            "(not Hindsight; not validated by the memory layer)",
                "memory_case_id": f"offline-{count}", "validated": False, "case_key": None}
