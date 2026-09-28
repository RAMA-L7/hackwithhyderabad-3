"""Hindsight Cloud adapter. All Hindsight behaviour is isolated here."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from debugagent.config import MemoryConfig
from debugagent.memory.store import (
    MemoryAuthError,
    MemorySchemaError,
    MemoryUnavailable,
)
from debugagent.schemas import (
    MemoryCase,
    RecallResult,
    RecallSet,
    RetentionDecision,
    SchemaError,
)

CONTEXT_LABEL = "debugagent-case"
LEDGER_VERSION = 1


def compute_case_key(case: MemoryCase, session_id: str) -> str:
    payload = f"{case.problem_signature}|{session_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def case_metadata(case: MemoryCase, case_key: str) -> dict[str, str]:
    return {
        "case_key": case_key,
        "outcome": case.outcome,
        "service": case.environment.get("service", "unknown"),
        "runtime": case.environment.get("runtime", "unknown"),
        "root_cause_key": (case.root_cause or "unconfirmed").strip().lower()[:64],
    }


def case_tags(case: MemoryCase) -> list[str]:
    tags = ["debugagent", f"outcome:{case.outcome}"]
    service = case.environment.get("service")
    if service:
        tags.append(f"service:{service}")
    return tags


def dedupe_by_case(items: list[RecallResult]) -> list[RecallResult]:
    """Collapse recall rows to one row per retained case, keeping the best row.

    Measured live on 2026-09-27: a bank holding 6 retained cases returned 35 recall rows,
    i.e. Hindsight emits several rows per stored memory (different chunk text, different
    scores, same `case_key`). Returning those rows as separate cases would duplicate
    provenance and inflate the candidate list, so the contract's one-row-per-memory intent
    is enforced here. Ties on `score_final` fall back to `score_semantic`, then first seen.
    """
    best: dict[str, RecallResult] = {}
    for item in items:
        current = best.get(item.case_id)
        if current is None or (item.score_final, item.score_semantic or 0.0) > (
            current.score_final,
            current.score_semantic or 0.0,
        ):
            best[item.case_id] = item
    return list(best.values())


def render_content(case: MemoryCase) -> str:
    failed = "; ".join(
        f"{item.approach} ({item.why_failed})" for item in case.failed_approaches
    ) or "none recorded"
    evidence = "; ".join(case.observed_evidence) or "none recorded"
    environment = ", ".join(f"{k}={v}" for k, v in sorted(case.environment.items())) or "unstated"
    return (
        f"Problem: {case.problem_signature}. "
        f"Symptoms: {'; '.join(case.symptoms)}. "
        f"Environment: {environment}. "
        f"Investigation trace: {' -> '.join(case.investigation_trace)}. "
        f"Failed approaches: {failed}. "
        f"Root cause: {case.root_cause or 'not confirmed'}. "
        f"Resolution: {case.resolution or 'not recorded'}. "
        f"Outcome: {case.outcome}. "
        f"Evidence references: {evidence}. "
        f"Verification notes: {case.verification_notes}."
    )


class Ledger:
    """Application-level idempotency record. Hindsight retain is not documented as idempotent."""

    def __init__(self, path: Path):
        self.path = path
        self._entries: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if data.get("version") != LEDGER_VERSION:
            return
        entries = data.get("entries")
        if isinstance(entries, dict):
            self._entries = {str(k): str(v) for k, v in entries.items()}

    def has(self, case_key: str) -> bool:
        return case_key in self._entries

    def get(self, case_key: str) -> str | None:
        return self._entries.get(case_key)

    def record(self, case_key: str, memory_id: str) -> None:
        self._entries[case_key] = memory_id
        self._flush()

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": LEDGER_VERSION, "entries": self._entries}
        handle, temp_name = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, sort_keys=True)
            os.replace(temp_name, self.path)
        except OSError:
            Path(temp_name).unlink(missing_ok=True)
            raise


def _attr(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _score(item: Any, name: str) -> float | None:
    scores = _attr(item, "scores")
    if scores is None:
        return None
    value = scores.get(name) if isinstance(scores, dict) else getattr(scores, name, None)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


class HindsightMemoryStore:
    """MemoryStore backed by Hindsight Cloud."""

    def __init__(self, config: MemoryConfig, client: Any | None = None, ledger: Ledger | None = None):
        self._config = config
        self._client = client if client is not None else self._build_client(config)
        self._ledger = ledger if ledger is not None else Ledger(config.ledger_path)
        self._ensure_bank()

    @staticmethod
    def _build_client(config: MemoryConfig) -> Any:
        if not config.configured:
            missing = ", ".join(config.missing_fields())
            raise MemoryUnavailable(f"hindsight not configured (missing: {missing})")
        try:
            from hindsight_client import Hindsight
        except Exception as exc:  # noqa: BLE001
            raise MemoryUnavailable(f"hindsight_client not importable ({type(exc).__name__})") from exc
        try:
            if config.hindsight_api_key:
                return Hindsight(base_url=config.hindsight_url, api_key=config.hindsight_api_key)
            return Hindsight(base_url=config.hindsight_url)
        except Exception as exc:  # noqa: BLE001
            raise MemoryUnavailable(f"hindsight client construction failed ({type(exc).__name__})") from exc

    def _ensure_bank(self) -> None:
        try:
            self._client.get_bank_config(self._config.bank_id)
            return
        except Exception:
            pass
        try:
            self._client.create_bank(self._config.bank_id)
        except Exception as exc:  # noqa: BLE001
            raise MemoryUnavailable(f"bank provisioning failed ({type(exc).__name__})") from exc

    def _classify_backend_error(self, exc: Exception, operation: str) -> MemoryUnavailable:
        status = getattr(exc, "status", None)
        name = type(exc).__name__
        text = str(exc)[:200]
        if status in (401, 403) or "Unauthorized" in name:
            return MemoryAuthError(f"{operation} rejected credentials ({name})")
        return MemoryUnavailable(f"{operation} failed ({name}: {text})")

    def recall(
        self,
        *,
        query: str,
        tags: list[str] | None = None,
        types: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> RecallSet:
        normalized_query = (query or "").strip()
        if not normalized_query:
            raise MemorySchemaError("recall requires a non-empty query")
        call_types = list(types) if types else list(self._config.recall_types)
        try:
            response = self._client.recall(
                bank_id=self._config.bank_id,
                query=normalized_query,
                types=call_types,
                max_tokens=max_tokens or self._config.max_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            raise self._classify_backend_error(exc, "recall") from exc

        raw_items = _attr(response, "results", []) or []
        items: list[RecallResult] = []
        for raw in raw_items:
            metadata = _attr(raw, "metadata", {}) or {}
            if not isinstance(metadata, dict):
                metadata = {}
            case_key = metadata.get("case_key")
            text = _attr(raw, "text", "") or ""
            final_score = _score(raw, "final")
            items.append(
                RecallResult(
                    case_id=str(case_key or f"unknown:{_attr(raw, 'id', 'n/a')}"),
                    text=str(text),
                    score_final=float(final_score) if final_score is not None else 0.0,
                    score_semantic=_score(raw, "semantic"),
                    score_keyword=_score(raw, "keyword"),
                    environment={
                        "service": str(metadata.get("service", "unknown")),
                        "runtime": str(metadata.get("runtime", "unknown")),
                    },
                    outcome=metadata.get("outcome"),
                    root_cause_key=metadata.get("root_cause_key"),
                    mentioned_at=_attr(raw, "mentioned_at"),
                )
            )
        return RecallSet(
            items=dedupe_by_case(items),
            recalled_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            bank_id=self._config.bank_id,
        )

    def retain(self, case: MemoryCase) -> RetentionDecision:
        try:
            MemoryCase.from_dict(case.to_dict())
        except SchemaError as exc:
            raise MemorySchemaError(f"rejected invalid case: {exc.errors}") from exc

        session_id = case.session_id or "seed"
        case_key = compute_case_key(case, session_id)

        if self._ledger.has(case_key):
            existing = self._ledger.get(case_key)
            return RetentionDecision(
                retained=False,
                reason="skipped: case already retained (idempotency ledger)",
                memory_case_id=existing,
                validated=True,
                case_key=case_key,
            )

        metadata = case_metadata(case, case_key)
        try:
            response = self._client.retain(
                bank_id=self._config.bank_id,
                content=render_content(case),
                context=CONTEXT_LABEL,
                metadata=metadata,
                tags=case_tags(case),
            )
        except Exception as exc:  # noqa: BLE001
            raise self._classify_backend_error(exc, "retain") from exc

        memory_id = str(_attr(response, "memory_id", None) or _attr(response, "id", "") or case_key)
        self._ledger.record(case_key, memory_id)
        return RetentionDecision(
            retained=True,
            reason="retained",
            memory_case_id=memory_id,
            validated=True,
            case_key=case_key,
        )

    def update(self, case_id: str, *, text: str, reason: str) -> None:
        if not case_id or not text.strip() or not reason.strip():
            raise MemorySchemaError("update requires case_id, text and reason")
        try:
            self._client.update_memory(
                bank_id=self._config.bank_id,
                memory_id=case_id,
                update_memory_request={"text": text, "reason": reason},
            )
        except Exception as exc:  # noqa: BLE001
            raise self._classify_backend_error(exc, "update") from exc

    def invalidate(self, case_id: str, *, reason: str) -> None:
        if not case_id or not reason.strip():
            raise MemorySchemaError("invalidate requires case_id and reason")
        try:
            self._client.update_memory(
                bank_id=self._config.bank_id,
                memory_id=case_id,
                update_memory_request={"state": "invalidated", "reason": reason},
            )
        except Exception as exc:  # noqa: BLE001
            raise self._classify_backend_error(exc, "invalidate") from exc
