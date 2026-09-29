"""Hindsight Cloud adapter. All Hindsight behaviour is isolated here."""

from __future__ import annotations

import hashlib
from dataclasses import replace
import json
import os
import tempfile
import threading
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


# Environment keys carried in metadata. Metadata is stored verbatim; the case text is not (Hindsight
# extracts it into LLM-chosen facts, measured live 2026-09-28 to drop proxy/region per bank).
ENV_METADATA_KEYS = ("service", "runtime", "proxy", "region")
MAX_FAILED_APPROACHES_CHARS = 500


def case_metadata(case: MemoryCase, case_key: str) -> dict[str, str]:
    metadata = {
        "case_key": case_key,
        "outcome": case.outcome,
        **{key: case.environment.get(key, "unknown") for key in ENV_METADATA_KEYS},
        "root_cause_key": (case.root_cause or "unconfirmed").strip().lower()[:64],
    }
    if case.failed_approaches:
        # Stored verbatim so recall can always show it: live on 2026-09-28 Hindsight's extraction kept the
        # symptom, root cause and fix of a seed but not its failed approach, so Act 2 could not warn about it.
        metadata["failed_approaches"] = "; ".join(
            f"{f.approach} (why it failed: {f.why_failed})" for f in case.failed_approaches
        )[:MAX_FAILED_APPROACHES_CHARS]
    return metadata


def case_tags(case: MemoryCase) -> list[str]:
    tags = ["debugagent", f"outcome:{case.outcome}"]
    service = case.environment.get("service")
    if service:
        tags.append(f"service:{service}")
    return tags


MAX_FACTS_PER_CASE = 8  # bounds prompt size; a retained case produced 6 fact rows when measured live


def dedupe_by_case(items: list[RecallResult]) -> list[RecallResult]:
    """Collapse recall rows to one row per retained case: the best row's scores, all its facts.

    Measured live on 2026-09-27: a bank holding 6 retained cases returned 35 recall rows,
    i.e. Hindsight emits several rows per stored memory (different chunk text, different
    scores, same `case_key`). Returning those rows as separate cases would duplicate
    provenance and inflate the candidate list, so the contract's one-row-per-memory intent
    is enforced here. Ties on `score_final` fall back to `score_semantic`, then first seen.

    Measured live on 2026-09-28: the rows are not copies of one text. Hindsight extracts one
    retained case into separate facts (the symptom, the failed approach, the resolution).
    Keeping only the best row's text therefore dropped the failed approach and the fix before
    they reached the pipeline, so the agent could not warn against a known-failed approach and
    once recommended it. The distinct row texts are now joined, best row first; scores and
    ordering still come from the best row only.
    """
    rows: dict[str, list[RecallResult]] = {}
    for item in items:
        rows.setdefault(item.case_id, []).append(item)
    collapsed = []
    for case_rows in rows.values():
        ordered = sorted(case_rows, key=lambda r: (r.score_final, r.score_semantic or 0.0), reverse=True)
        facts = list(dict.fromkeys(t for t in (r.text.strip() for r in ordered) if t))[:MAX_FACTS_PER_CASE]
        collapsed.append(replace(ordered[0], text=" | ".join(facts)))
    return collapsed


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
    """Application-level idempotency record. Hindsight retain is not documented as idempotent.

    Thread safety (P3-1): all mutable state and the file write are guarded by a lock that is
    *keyed on the ledger path*, not on the instance. A per-instance lock would not help, because
    the normal construction path (`build_port()`) creates a fresh store -- and therefore a fresh
    Ledger -- on every call, and two instances on one path each hold their own stale snapshot.
    Sharing one lock per path makes read-modify-write atomic within this process.

    This is deliberately process-local. It does not coordinate separate processes, and it does not
    use file locking. The in-memory snapshot is still only as fresh as the last reload, so every
    guarded mutation re-reads the file first.

    NOT guaranteed: atomicity across the remote Hindsight write and this local ledger write. If
    `client.retain()` succeeds and the subsequent `_flush()` fails, the caller still sees an
    exception while the case is durably stored remotely, so a caller retrying on that exception
    can retain twice. That window cannot be closed with a local lock; it needs a durable remote-side
    idempotency key and is left to a later phase.
    """

    # Keyed by resolved path so that two Ledger objects on one file share one lock.
    _locks: dict[str, "threading.RLock"] = {}
    _locks_guard = threading.Lock()

    def __init__(self, path: Path):
        self.path = path
        self._lock = self._lock_for(path)
        self._entries: dict[str, str] = {}
        with self._lock:
            self._load()

    @classmethod
    def _lock_for(cls, path: Path) -> "threading.RLock":
        key = os.path.normcase(str(Path(path).resolve()))
        with cls._locks_guard:
            lock = cls._locks.get(key)
            if lock is None:
                lock = cls._locks[key] = threading.RLock()
            return lock

    @property
    def lock(self) -> "threading.RLock":
        """The path-shared reentrant lock guarding this ledger."""
        return self._lock

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

    def refresh(self) -> None:
        """Re-read the file so an update written by another Ledger on this path is seen.

        The caller must hold `lock`. A corrupt or unreadable file is ignored, matching `_load()`:
        the ledger is an optimisation, and refusing to retain because the local file is damaged
        would be worse than a possible duplicate.
        """
        self._load()

    def has(self, case_key: str) -> bool:
        with self._lock:
            return case_key in self._entries

    def get(self, case_key: str) -> str | None:
        with self._lock:
            return self._entries.get(case_key)

    def record(self, case_key: str, memory_id: str) -> None:
        with self._lock:
            self._load()
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
        # P3-1: one lock per ledger path, shared with any other Ledger on the same file, held
        # across the whole check-then-act below. It is reentrant so the nested `has()` and
        # `record()` calls do not deadlock.
        self._retain_lock = self._ledger.lock
        self._closed = False
        self._ensure_bank()

    @property
    def config(self) -> MemoryConfig:
        """The configuration this store was built with (read-only)."""
        return self._config

    def close(self) -> None:
        """Release the backend's HTTP resources. Idempotent; safe to call on an injected client.

        `hindsight_client.Hindsight` opens an aiohttp ClientSession (and its TCPConnector) on the
        first request. The client exposes a supported synchronous `close()`, so this is an explicit
        lifecycle call rather than leaving the session to be reclaimed at interpreter exit, which is
        what produced the "Unclosed client session" / "Unclosed connector" ResourceWarnings. The
        async `aclose()` exists too but this codebase is fully synchronous.
        """
        if self._closed:
            return
        self._closed = True
        closer = getattr(self._client, "close", None)
        if callable(closer):
            closer()

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
        failed_before: dict[str, str] = {}
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
                    environment={key: str(metadata.get(key, "unknown")) for key in ENV_METADATA_KEYS},
                    outcome=metadata.get("outcome"),
                    root_cause_key=metadata.get("root_cause_key"),
                    mentioned_at=_attr(raw, "mentioned_at"),
                )
            )
            if metadata.get("failed_approaches") and case_key:
                failed_before[str(case_key)] = str(metadata["failed_approaches"])
        collapsed = [
            replace(item, text=f"{item.text} | Failed before: {failed_before[item.case_id]}")
            if item.case_id in failed_before else item
            for item in dedupe_by_case(items)
        ]
        return RecallSet(
            items=collapsed,
            recalled_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            bank_id=self._config.bank_id,
        )

    def retain(self, case: MemoryCase) -> RetentionDecision:
        """Retain a case, skipping it if the idempotency ledger already records it.

        P3-1: the ledger check, the remote write and the ledger record are one critical section,
        so two concurrent retains of the same case cannot both pass the check and both write
        remotely. `refresh()` re-reads the file first, because a second store on the same path
        loaded its snapshot before the first store wrote.

        This serialises concurrent retains in one process and makes a lost local update
        impossible. It does NOT make the remote write and the local record atomic together: if the
        remote write succeeds and the ledger flush then fails, the caller receives an exception
        while the case is already stored remotely, and a retry could retain twice.
        """
        try:
            MemoryCase.from_dict(case.to_dict())
        except SchemaError as exc:
            raise MemorySchemaError(f"rejected invalid case: {exc.errors}") from exc

        session_id = case.session_id or "seed"
        case_key = compute_case_key(case, session_id)

        with self._retain_lock:
            self._ledger.refresh()

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
