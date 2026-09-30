"""P3-2C regression tests: failure legibility for retention.

Three failure modes are made distinguishable, because a caller must be able to tell them apart:

  - LOCAL persistence failed after a possibly-successful remote write (`persist`). The remote
    document may already exist; nothing was rolled back.
  - Remote outcome UNKNOWN (`ambiguous`) - the request may already have been applied.
  - Remote call known to have failed without storing (`unavailable`) - a clean retry.

Plus: `success=False` is not recorded as a successful retain, and a corrupt ledger is surfaced
rather than silently read as empty.

Deterministic: no sleeps, no threads, no timing assertions. Failures are injected by replacing
one method or raising a named exception.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import support
from debugagent.memory.hindsight_store import (
    LEDGER_VERSION,
    HindsightMemoryStore,
    Ledger,
)
from debugagent.memory.store import (
    MemoryAmbiguousError,
    MemoryAuthError,
    MemoryError,
    MemoryPersistError,
    MemoryUnavailable,
)
from debugagent.pipeline.memory_adapter import HindsightMemoryPort
from debugagent.pipeline.memory_port import MemoryFailure
from debugagent.schemas import MemoryCase
from support import FakeHindsightClient, memory_config

VALID = {
    "problem_signature": "orders-api returns HTTP 502 for payloads above 2MB",
    "symptoms": ["502 for large payloads"],
    "environment": {"service": "orders-api", "runtime": "python3.10"},
    "observed_evidence": ["report://orders-api/payload-matrix.csv"],
    "investigation_trace": ["payload size sweep"],
    "failed_approaches": [],
    "root_cause": "proxy request size limit",
    "resolution": "raised max_request_bytes to 8MB",
    "outcome": "resolved",
    "verification_notes": "confirmed by re-running the sweep",
}


def case(**overrides) -> MemoryCase:
    return MemoryCase.from_dict(dict(VALID, **overrides))


class _FlushingFails(FakeHindsightClient):
    """Client whose remote retain succeeds, while the local flush is made to fail."""

    def __init__(self):
        super().__init__()
        self._store: HindsightMemoryStore | None = None

    def install(self, store: HindsightMemoryStore) -> None:
        self._store = store
        store._ledger._flush = _raise_oserror  # type: ignore[method-assign]

    def retain(self, **kwargs):
        self.retained.append(kwargs)
        return type("R", (), {"memory_id": "mem-remote-1"})()


def _raise_oserror(self=None) -> None:
    raise OSError(28, "No space left on device")


class PersistenceFailureTests(unittest.TestCase):
    """A/C. Remote success followed by local flush failure."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.client = _FlushingFails()
        self.store = HindsightMemoryStore(memory_config(self.tmp_path), client=self.client)
        self.client.install(self.store)

    def ledger_entries(self) -> dict:
        path = memory_config(self.tmp_path).ledger_path
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))["entries"]

    def test_flush_oserror_raises_a_typed_persistence_failure(self):
        with self.assertRaises(MemoryPersistError) as ctx:
            self.store.retain(case(session_id="run-1"))
        self.assertIn("ledger", str(ctx.exception))

    def test_raw_oserror_does_not_escape_the_store(self):
        try:
            self.store.retain(case(session_id="run-1"))
        except MemoryPersistError:
            pass
        except OSError as exc:  # pragma: no cover - the failure mode being prevented
            self.fail(f"raw OSError leaked through the store: {exc}")
        else:  # pragma: no cover
            self.fail("expected MemoryPersistError")

    def test_remote_operation_happened_before_the_local_failure(self):
        with self.assertRaises(MemoryPersistError):
            self.store.retain(case(session_id="run-1"))
        self.assertEqual(len(self.client.retained), 1, "the remote write did happen")

    def test_no_ledger_entry_is_recorded(self):
        with self.assertRaises(MemoryPersistError):
            self.store.retain(case(session_id="run-1"))
        self.assertEqual(self.ledger_entries(), {})

    def test_message_states_the_remote_write_may_have_succeeded(self):
        with self.assertRaises(MemoryPersistError) as ctx:
            self.store.retain(case(session_id="run-1"))
        message = str(ctx.exception).lower()
        self.assertIn("may", message)
        self.assertIn("not rolled back", message)

    def test_does_not_claim_exactly_once_or_rollback(self):
        with self.assertRaises(MemoryPersistError) as ctx:
            self.store.retain(case(session_id="run-1"))
        message = str(ctx.exception).lower()
        self.assertNotIn("exactly-once", message)
        self.assertNotIn("rolled back the remote", message)
        self.assertNotIn("guarantee", message)

    def test_document_id_was_still_sent(self):
        """P3-2B preserved: the failure path does not drop the remote identity."""
        with self.assertRaises(MemoryPersistError):
            self.store.retain(case(session_id="run-1"))
        self.assertEqual(len(self.client.retained), 1)
        self.assertIn("document_id", self.client.retained[0])
        self.assertEqual(self.client.retained[0]["update_mode"], "replace")

    def test_port_converts_persistence_failure_to_its_contract_kind(self):
        port = HindsightMemoryPort(self.store)
        with self.assertRaises(MemoryFailure) as ctx:
            port.retain(case(session_id="run-1").to_dict())
        self.assertEqual(ctx.exception.kind, "persist")

    def test_persistence_failure_is_a_memory_layer_error(self):
        with self.assertRaises(MemoryError):
            self.store.retain(case(session_id="run-1"))


class AmbiguousRemoteTests(unittest.TestCase):
    """E. A remote outcome that is UNKNOWN is distinct from a clean remote failure."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def store_with(self, exc: Exception) -> HindsightMemoryStore:
        class Failing(FakeHindsightClient):
            def retain(self, **kwargs):
                raise exc

        return HindsightMemoryStore(memory_config(self.tmp_path), client=Failing())

    def test_read_timeout_is_ambiguous(self):
        store = self.store_with(TimeoutError("read timed out"))
        with self.assertRaises(MemoryAmbiguousError) as ctx:
            store.retain(case(session_id="run-1"))
        self.assertIn("UNKNOWN", str(ctx.exception))

    def test_connection_reset_is_ambiguous(self):
        store = self.store_with(ConnectionResetError("connection reset by peer"))
        with self.assertRaises(MemoryAmbiguousError):
            store.retain(case(session_id="run-1"))

    def test_urllib3_read_timeout_is_ambiguous(self):
        from urllib3 import exceptions as ue

        store = self.store_with(ue.ReadTimeoutError(None, "https://bank", "timed out"))
        with self.assertRaises(MemoryAmbiguousError):
            store.retain(case(session_id="run-1"))

    def test_urllib3_protocol_error_is_ambiguous(self):
        from urllib3 import exceptions as ue

        store = self.store_with(ue.ProtocolError("connection aborted"))
        with self.assertRaises(MemoryAmbiguousError):
            store.retain(case(session_id="run-1"))

    def test_connection_refused_is_a_clean_failure(self):
        """A connection never established cannot have stored anything."""
        from urllib3 import exceptions as ue

        store = self.store_with(ue.NewConnectionError(None, "connection refused"))
        with self.assertRaises(MemoryUnavailable) as ctx:
            store.retain(case(session_id="run-1"))
        self.assertNotIsInstance(ctx.exception, MemoryAmbiguousError)

    def test_connect_timeout_is_a_clean_failure(self):
        from urllib3 import exceptions as ue

        store = self.store_with(ue.ConnectTimeoutError("connect timed out"))
        with self.assertRaises(MemoryUnavailable) as ctx:
            store.retain(case(session_id="run-1"))
        self.assertNotIsInstance(ctx.exception, MemoryAmbiguousError)

    def test_name_resolution_failure_is_a_clean_failure(self):
        import socket

        from urllib3 import exceptions as ue

        store = self.store_with(
            ue.NameResolutionError("bank.invalid", None, socket.gaierror("no such host"))
        )
        with self.assertRaises(MemoryUnavailable) as ctx:
            store.retain(case(session_id="run-1"))
        self.assertNotIsInstance(ctx.exception, MemoryAmbiguousError)

    def test_http_error_response_is_a_clean_failure(self):
        """The service answered, so the outcome is known even though it failed."""
        store = self.store_with(_status_error(503, "Service Unavailable"))
        with self.assertRaises(MemoryUnavailable) as ctx:
            store.retain(case(session_id="run-1"))
        self.assertNotIsInstance(ctx.exception, MemoryAmbiguousError)

    def test_auth_error_still_wins_over_ambiguity(self):
        store = self.store_with(_status_error(401, "Unauthorized"))
        with self.assertRaises(MemoryAuthError):
            store.retain(case(session_id="run-1"))

    def test_generic_exception_is_a_clean_failure(self):
        store = self.store_with(ValueError("something odd"))
        with self.assertRaises(MemoryUnavailable) as ctx:
            store.retain(case(session_id="run-1"))
        self.assertNotIsInstance(ctx.exception, MemoryAmbiguousError)

    def test_ambiguous_is_distinct_from_unavailable_as_a_type(self):
        self.assertTrue(issubclass(MemoryAmbiguousError, MemoryUnavailable))
        self.assertNotEqual(MemoryAmbiguousError, MemoryUnavailable)

    def test_port_maps_ambiguous_to_its_own_kind(self):
        store = self.store_with(TimeoutError("read timed out"))
        port = HindsightMemoryPort(store)
        with self.assertRaises(MemoryFailure) as ctx:
            port.retain(case(session_id="run-1").to_dict())
        self.assertEqual(ctx.exception.kind, "ambiguous")

    def test_port_maps_clean_failure_to_unavailable(self):
        store = self.store_with(_status_error(503, "Service Unavailable"))
        port = HindsightMemoryPort(store)
        with self.assertRaises(MemoryFailure) as ctx:
            port.retain(case(session_id="run-1").to_dict())
        self.assertEqual(ctx.exception.kind, "unavailable")

    def test_ambiguous_and_clean_map_to_different_kinds(self):
        kinds = set()
        for exc in (TimeoutError("t"), _status_error(503, "down")):
            store = self.store_with(exc)
            port = HindsightMemoryPort(store)
            with self.assertRaises(MemoryFailure) as ctx:
                port.retain(case(session_id="run-1").to_dict())
            kinds.add(ctx.exception.kind)
        self.assertEqual(kinds, {"ambiguous", "unavailable"})

    def test_ambiguous_leaves_no_ledger_entry(self):
        store = self.store_with(TimeoutError("read timed out"))
        with self.assertRaises(MemoryAmbiguousError):
            store.retain(case(session_id="run-1"))
        path = memory_config(self.tmp_path).ledger_path
        self.assertFalse(path.is_file(), "no local record for an unknown remote outcome")

    def test_ambiguous_does_not_claim_exactly_once(self):
        store = self.store_with(TimeoutError("read timed out"))
        with self.assertRaises(MemoryAmbiguousError) as ctx:
            store.retain(case(session_id="run-1"))
        self.assertNotIn("exactly-once", str(ctx.exception).lower())

    def test_ambiguous_does_not_add_a_retry_loop(self):
        """No automatic retry anywhere: one call, one failure."""
        calls = []

        class Counting(FakeHindsightClient):
            def retain(self, **kwargs):
                calls.append(1)
                raise TimeoutError("read timed out")

        store = HindsightMemoryStore(memory_config(self.tmp_path), client=Counting())
        with self.assertRaises(MemoryAmbiguousError):
            store.retain(case(session_id="run-1"))
        self.assertEqual(len(calls), 1)


class SuccessFlagTests(unittest.TestCase):
    """F. `success=False` is not recorded as a successful retain."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def store_returning(self, **fields) -> HindsightMemoryStore:
        class Responding(FakeHindsightClient):
            def retain(self, **kwargs):
                self.retained.append(kwargs)
                return type("R", (), dict(fields))()

        return HindsightMemoryStore(memory_config(self.tmp_path), client=Responding())

    def ledger_entries(self) -> dict:
        path = memory_config(self.tmp_path).ledger_path
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))["entries"]

    def test_success_false_is_not_recorded(self):
        store = self.store_returning(success=False, memory_id="mem-x")
        with self.assertRaises(MemoryUnavailable) as ctx:
            store.retain(case(session_id="run-1"))
        self.assertIn("success=False", str(ctx.exception))
        self.assertEqual(self.ledger_entries(), {}, "a failed retain must not be recorded")

    def test_success_true_is_recorded(self):
        store = self.store_returning(success=True, memory_id="mem-x")
        decision = store.retain(case(session_id="run-1"))
        self.assertTrue(decision.retained)
        self.assertEqual(len(self.ledger_entries()), 1)

    def test_absent_success_attribute_is_treated_as_success(self):
        """Backwards compatible with clients/fakes that do not report the flag at all."""
        store = self.store_returning(memory_id="mem-x")
        decision = store.retain(case(session_id="run-1"))
        self.assertTrue(decision.retained)

    def test_installed_client_response_model_carries_success(self):
        """Confirms the distinction exists in the real client contract."""
        from hindsight_client import RetainResponse

        self.assertIn("success", RetainResponse.model_fields)

    def test_port_maps_success_false_to_unavailable(self):
        store = self.store_returning(success=False, memory_id="mem-x")
        port = HindsightMemoryPort(store)
        with self.assertRaises(MemoryFailure) as ctx:
            port.retain(case(session_id="run-1").to_dict())
        self.assertEqual(ctx.exception.kind, "unavailable")


class LedgerIntegrityTests(unittest.TestCase):
    """G/H. Corrupt ledger surfaced; missing ledger is normal first use."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.path = self.tmp_path / "ledger.json"

    def test_missing_ledger_is_normal_first_use(self):
        self.assertFalse(self.path.is_file())
        ledger = Ledger(self.path)
        self.assertFalse(ledger.has("anything"))

    def test_first_retain_with_no_ledger_creates_it(self):
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=FakeHindsightClient())
        self.assertTrue(store.retain(case(session_id="run-1")).retained)
        self.assertTrue(memory_config(self.tmp_path).ledger_path.is_file())

    def test_valid_empty_ledger_is_accepted(self):
        self.path.write_text(json.dumps({"version": LEDGER_VERSION, "entries": {}}), encoding="utf-8")
        self.assertFalse(Ledger(self.path).has("anything"))

    def test_malformed_ledger_is_surfaced(self):
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(MemoryPersistError) as ctx:
            Ledger(self.path)
        self.assertIn("malformed", str(ctx.exception))

    def test_corrupt_ledger_is_not_silently_empty(self):
        self.path.write_text("[]", encoding="utf-8")
        with self.assertRaises(MemoryPersistError):
            Ledger(self.path)

    def test_ledger_without_entries_key_is_surfaced(self):
        self.path.write_text(json.dumps({"version": LEDGER_VERSION}), encoding="utf-8")
        with self.assertRaises(MemoryPersistError):
            Ledger(self.path)

    def test_incompatible_version_is_surfaced(self):
        self.path.write_text(json.dumps({"version": 999, "entries": {"a": "b"}}), encoding="utf-8")
        with self.assertRaises(MemoryPersistError) as ctx:
            Ledger(self.path)
        self.assertIn("version", str(ctx.exception))

    def test_corrupt_ledger_is_never_overwritten(self):
        original = "{not json"
        self.path.write_text(original, encoding="utf-8")
        with self.assertRaises(MemoryPersistError):
            Ledger(self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)

    def test_corrupt_ledger_survives_a_failed_retain_attempt(self):
        original = "{not json"
        self.path.write_text(original, encoding="utf-8")
        # The store cannot even be constructed against a corrupt ledger, so the failure surfaces
        # before any remote call; the file must still be intact afterwards.
        with self.assertRaises(MemoryPersistError):
            store = HindsightMemoryStore(memory_config(self.tmp_path), client=FakeHindsightClient())
            store.retain(case(session_id="run-1"))
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)

    def test_store_construction_surfaces_a_corrupt_ledger(self):
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(MemoryPersistError):
            HindsightMemoryStore(memory_config(self.tmp_path), client=FakeHindsightClient())

    def test_valid_ledger_still_writes_with_safe_replace(self):
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=FakeHindsightClient())
        store.retain(case(session_id="run-1"))
        path = memory_config(self.tmp_path).ledger_path
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], LEDGER_VERSION)
        self.assertEqual(len(list(path.parent.glob("*.tmp"))), 0, "no temp file left behind")

    def test_corrupt_ledger_message_does_not_claim_rollback(self):
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(MemoryPersistError) as ctx:
            Ledger(self.path)
        self.assertNotIn("exactly-once", str(ctx.exception).lower())


def _status_error(status: int, reason: str) -> Exception:
    """An error carrying an HTTP status, like the generated client's ApiException."""
    return type(
        "ServiceException",
        (RuntimeError,),
        {"status": status, "__init__": lambda self: RuntimeError.__init__(self, reason)},
    )( )


if __name__ == "__main__":
    unittest.main()
