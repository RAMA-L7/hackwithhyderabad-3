"""P3-2 Phase D: integration coverage across the whole A -> B -> C contract.

Phases A, B and C each have unit coverage. This file covers the JOINTS, which were genuinely
uncovered:

  - retain -> real computed case_key -> Hindsight metadata -> recall dedupe, in one flow. Every
    existing recall test used a hardcoded `case_key` literal, so nothing proved the identity
    actually survives the metadata round trip.
  - P3-1's serialized retain COMPOSED WITH P3-2B's document identity. The concurrency suite used a
    client that does not model documents; the idempotency suite was single-threaded. Nothing proved
    that concurrent retains of one case still converge on one remote document.
  - ledger key == document_id == metadata case_key, i.e. one identity used consistently in all
    three places it appears.
  - the `session_id=None -> "seed"` fallback actually reaching `document_id` (the residual sharp
    edge flagged in the Phase A review, and the precondition for any later `document_id` work).

Deterministic: barriers and events, no sleeps, no wall-clock assertions.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

import support
from debugagent.memory.hindsight_store import (
    LEDGER_VERSION,
    HindsightMemoryStore,
    compute_case_key,
    compute_outcome_digest,
    dedupe_by_case,
)
from debugagent.memory.store import MemoryPersistError
from debugagent.pipeline.memory_adapter import HindsightMemoryPort
from debugagent.schemas import MemoryCase, RecallResult
from support import FakeHindsightClient, memory_config

BARRIER_TIMEOUT = 10.0

BASE = {
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
    return MemoryCase.from_dict(dict(BASE, **overrides))


class DocumentIdClient:
    """Fake Hindsight that stores one document per document_id, and can replay it on recall.

    `documents` is keyed by document_id, so a non-converging write shows up as a second entry.
    `calls` records every request, so a repeated write stays visible as a second CALL - the
    distinction between state-idempotence and exactly-once.
    """

    def __init__(self, recall_rows: list[dict] | None = None):
        self.documents: dict[str, dict] = {}
        self.calls: list[dict] = []
        self.banks: set[str] = set()
        self._recall_rows = recall_rows or []
        self.entered = 0
        self.max_concurrent_entered = 0
        self._guard = threading.Lock()

    def get_bank_config(self, bank_id: str) -> dict:
        if bank_id not in self.banks:
            raise RuntimeError("404 not found")
        return {"bank_id": bank_id}

    def create_bank(self, bank_id: str, **_kwargs) -> str:
        self.banks.add(bank_id)
        return bank_id

    def retain(self, **kwargs):
        with self._guard:
            self.entered += 1
            self.max_concurrent_entered = max(self.max_concurrent_entered, self.entered)
        try:
            self.calls.append(dict(kwargs))
            self.documents[str(kwargs.get("document_id"))] = dict(kwargs)
            return type("R", (), {"memory_id": f"mem-{len(self.calls)}", "success": True})()
        finally:
            with self._guard:
                self.entered -= 1

    def recall(self, **_kwargs):
        rows = self._recall_rows
        return type("R", (), {"results": [type("M", (), row)() for row in rows]})()

    def close(self) -> None:
        pass


class TempDirMixin(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def fresh_dir(self) -> Path:
        target = Path(tempfile.mkdtemp())
        self.addCleanup(tempfile.TemporaryDirectory(ignore_cleanup_errors=True).cleanup)
        return target


class IdentityToDocumentIdTests(TempDirMixin):
    """The identity computed by Phase A is the document id used by Phase B, in one flow."""

    def test_retain_sends_the_computed_case_key_as_document_id(self):
        client = DocumentIdClient()
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        payload = case(session_id="run-1")
        decision = store.retain(payload)

        expected = compute_case_key(payload, "run-1")
        self.assertEqual(decision.case_key, expected)
        self.assertEqual(client.calls[0]["document_id"], expected)
        self.assertEqual(client.calls[0]["update_mode"], "replace")

    def test_one_identity_is_used_in_all_three_places(self):
        """ledger key == metadata case_key == document_id."""
        client = DocumentIdClient()
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        decision = store.retain(case(session_id="run-1"))

        path = memory_config(self.tmp_path).ledger_path
        ledger = json.loads(path.read_text(encoding="utf-8"))["entries"]
        self.assertEqual(list(ledger), [decision.case_key])
        self.assertEqual(client.calls[0]["metadata"]["case_key"], decision.case_key)
        self.assertEqual(client.calls[0]["document_id"], decision.case_key)

    def test_identity_survives_serialization_into_the_remote_call(self):
        """Phase A's round-trip fix composed with Phase B: the sent document_id is the one a
        fresh parse of the same case produces."""
        client = DocumentIdClient()
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        original = case(session_id="run-1")
        store.retain(original)
        reparsed = MemoryCase.from_dict(original.to_dict())
        self.assertEqual(reparsed.session_id, "run-1")
        self.assertEqual(client.calls[0]["document_id"], compute_case_key(reparsed, "run-1"))

    def test_seed_fallback_reaches_document_id(self):
        """The `session_id=None` path still gets a real, stable document id."""
        client = DocumentIdClient()
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        sessionless = case()  # no session_id
        decision = store.retain(sessionless)
        self.assertIsNone(sessionless.session_id)
        self.assertEqual(decision.case_key, compute_case_key(sessionless, "seed"))
        self.assertEqual(client.calls[0]["document_id"], decision.case_key)

    def test_three_components_all_participate_in_the_document_id(self):
        """problem_signature, session_id and outcome_digest each move the document id."""
        client = DocumentIdClient()
        seen = set()

        def document_id_for(payload, session):
            target = Path(tempfile.mkdtemp())
            store = HindsightMemoryStore(memory_config(target), client=client)
            decision = store.retain(payload)
            seen.add(decision.case_key)
            return decision.case_key

        document_id_for(case(session_id="s1"), "s1")
        document_id_for(case(session_id="s2"), "s2")                       # session differs
        document_id_for(case(session_id="s1", problem_signature="other"), "s1")
        document_id_for(case(session_id="s1", root_cause="a different cause"), "s1")
        self.assertEqual(len(seen), 4, "each of the three components must change the document id")

    def test_outcome_digest_is_the_component_that_separates_outcomes(self):
        payload = case(session_id="s1")
        expected = compute_case_key(payload, "s1")
        manual = payload.problem_signature + "|" + "s1" + "|" + compute_outcome_digest(payload)
        import hashlib

        self.assertEqual(expected, hashlib.sha256(manual.encode("utf-8")).hexdigest()[:16])


class RecallDedupeIntegrationTests(TempDirMixin):
    """The computed identity survives into recall, where dedupe_by_case consumes it."""

    def test_computed_case_key_round_trips_into_recall_and_dedupes(self):
        client = DocumentIdClient()
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        decision = store.retain(case(session_id="run-1"))
        key = decision.case_key

        # Hindsight emits several rows per stored memory; they share the stored case_key.
        rows = [
            {"id": f"u{i}", "text": f"chunk {i}", "metadata": {"case_key": key},
             "scores": {"final": 0.9 - i * 0.05, "semantic": 0.8, "keyword": 0.7}}
            for i in range(3)
        ]
        client._recall_rows = rows
        result = store.recall(query="payload 502")
        self.assertEqual(len(result.items), 1, "three rows for one case must collapse to one")
        self.assertEqual(result.items[0].case_id, key)

    def test_two_different_outcomes_are_two_cases_at_recall(self):
        """The Phase A fix, observed at the recall boundary rather than the store boundary."""
        first_key = compute_case_key(case(session_id="run-1", root_cause="proxy limit"), "run-1")
        second_key = compute_case_key(
            case(session_id="run-1", root_cause="client TLS bug"), "run-1"
        )
        self.assertNotEqual(first_key, second_key)

        client = DocumentIdClient()
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        client._recall_rows = [
            {"id": "u1", "text": "proxy limit fact", "metadata": {"case_key": first_key},
             "scores": {"final": 0.9}},
            {"id": "u2", "text": "tls bug fact", "metadata": {"case_key": second_key},
             "scores": {"final": 0.8}},
        ]
        result = store.recall(query="502")
        self.assertEqual({item.case_id for item in result.items}, {first_key, second_key})

    def test_dedupe_still_uses_the_key_not_the_memory_row_id(self):
        results = [
            RecallResult(case_id="shared-key", text="a", score_final=0.9, score_semantic=0.5,
                         score_keyword=0.5, environment={}, outcome="resolved",
                         root_cause_key=None, mentioned_at=None),
            RecallResult(case_id="shared-key", text="b", score_final=0.4, score_semantic=0.5,
                         score_keyword=0.5, environment={}, outcome="resolved",
                         root_cause_key=None, mentioned_at=None),
        ]
        collapsed = dedupe_by_case(results)
        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0].score_final, 0.9)


class ConcurrencyCompositionTests(TempDirMixin):
    """P3-1 serialization composed with P3-2B document identity.

    The P3-1 suite proves the critical section; this proves the two features together still
    converge on ONE remote document when two threads retain the same case.
    """

    def _retain_on_threads(self, stores, payload, barrier_parties=None):
        parties = barrier_parties or len(stores)
        barrier = threading.Barrier(parties)
        decisions: list[object] = [None] * len(stores)
        errors: list[BaseException | None] = [None] * len(stores)

        def worker(index: int) -> None:
            barrier.wait(timeout=BARRIER_TIMEOUT)
            try:
                decisions[index] = stores[index].retain(payload)
            except BaseException as exc:  # noqa: BLE001 - asserted on by the caller
                errors[index] = exc

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(stores))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=BARRIER_TIMEOUT)
            self.assertFalse(thread.is_alive(), "worker did not finish: possible deadlock")
        return decisions, errors

    def test_concurrent_same_case_converges_on_one_remote_document(self):
        client = DocumentIdClient()
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        payload = case(session_id="run-1")

        decisions, errors = self._retain_on_threads([store, store], payload)

        self.assertEqual([e for e in errors if e is not None], [])
        self.assertEqual(len(client.documents), 1, "two threads must not create two documents")
        self.assertEqual(client.max_concurrent_entered, 1, "the retain critical section held")
        self.assertEqual(sorted(d.retained for d in decisions), [False, True])

    def test_concurrent_same_case_across_stores_converges(self):
        """Two stores, two empty ledgers: two remote CALLS, still one remote DOCUMENT."""
        client = DocumentIdClient()
        first = HindsightMemoryStore(memory_config(self.fresh_dir()), client=client)
        second = HindsightMemoryStore(memory_config(self.fresh_dir()), client=client)
        payload = case(session_id="run-1")

        decisions, errors = self._retain_on_threads([first, second], payload)

        self.assertEqual([e for e in errors if e is not None], [])
        self.assertEqual(len(client.documents), 1)
        self.assertEqual({d.case_key for d in decisions}, {next(iter(client.documents))})
        self.assertEqual(sorted(d.retained for d in decisions), [True, True])

    def test_concurrent_different_cases_get_separate_documents(self):
        client = DocumentIdClient()
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        first = case(session_id="run-1", root_cause="proxy limit")
        second = case(session_id="run-1", root_cause="client TLS bug")

        barrier = threading.Barrier(2)
        errors: list[BaseException | None] = [None, None]

        def worker(index: int) -> None:
            barrier.wait(timeout=BARRIER_TIMEOUT)
            try:
                store.retain(first if index == 0 else second)
            except BaseException as exc:  # noqa: BLE001
                errors[index] = exc

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=BARRIER_TIMEOUT)
            self.assertFalse(thread.is_alive())

        self.assertEqual([e for e in errors if e is not None], [])
        self.assertEqual(len(client.documents), 2, "distinct outcomes must not share a document")

    def test_state_idempotence_holds_while_operations_still_repeat(self):
        """The state/operation distinction, under concurrency."""
        client = DocumentIdClient()
        first = HindsightMemoryStore(memory_config(self.fresh_dir()), client=client)
        second = HindsightMemoryStore(memory_config(self.fresh_dir()), client=client)
        payload = case(session_id="run-1")

        self._retain_on_threads([first, second], payload)

        self.assertEqual(len(client.documents), 1, "state converged")
        self.assertEqual(len(client.calls), 2, "but two operations ran - not exactly-once")

    def test_persist_failure_under_concurrency_still_typed(self):
        """P3-1's lock and P3-2C's typed failure compose.

        Both threads reach the critical section; the first flush fails, and the second thread
        still completes. Whether the second one WRITES depends on ledger state, which the next
        test pins.
        """
        client = DocumentIdClient()
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        payload = case(session_id="run-1")

        original_flush = store._ledger._flush
        attempts = {"n": 0}

        def failing_flush(self=None):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise OSError(28, "No space left on device")
            return original_flush()

        store._ledger._flush = failing_flush  # type: ignore[method-assign]
        try:
            _decisions, errors = self._retain_on_threads([store, store], payload)
        finally:
            store._ledger._flush = original_flush  # type: ignore[method-assign]

        kinds = [type(e).__name__ if e is not None else None for e in errors]
        self.assertIn("MemoryPersistError", kinds, f"expected a typed failure, got {kinds}")
        self.assertEqual(len(client.documents), 1, "one remote document either way")

    def test_failed_flush_leaves_the_case_retained_in_memory_not_re_written(self):
        """Characterisation of the residual gap, pinned deliberately.

        `record()` mutates the in-memory dict before `_flush()` runs, so when the flush fails the
        in-memory ledger still holds the key while the FILE has no record. `refresh()` cannot clear
        it, because there is no file to read. A later retain of the same case therefore
        short-circuits with "already retained".

        That is CORRECT here, because the remote write did succeed - the claim is true and no
        duplicate remote write happens. The residual gap is durability, not correctness: the local
        record is in-memory only and is lost on restart. After a restart the case is re-retained,
        which is safe precisely because P3-2B makes the remote write converge via document_id.
        """
        client = DocumentIdClient()
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        payload = case(session_id="run-1")
        original_flush = store._ledger._flush
        store._ledger._flush = lambda self=None: (_ for _ in ()).throw(OSError(28, "full"))
        try:
            with self.assertRaises(MemoryPersistError):
                store.retain(payload)
        finally:
            store._ledger._flush = original_flush  # type: ignore[method-assign]

        self.assertEqual(len(client.documents), 1, "the remote write did happen")
        self.assertFalse(
            memory_config(self.tmp_path).ledger_path.is_file(), "no durable local record"
        )
        self.assertEqual(len(store._ledger._entries), 1, "but the in-memory record survives")

        again = store.retain(payload)
        self.assertFalse(again.retained)
        self.assertIn("already retained", again.reason)
        self.assertEqual(len(client.documents), 1, "and no duplicate remote write occurs")
        self.assertEqual(len(client.calls), 1, "the remote operation was not repeated")

    def test_after_restart_the_case_is_re_retained_but_still_one_document(self):
        """The durability gap, and why P3-2B makes it safe."""
        client = DocumentIdClient()
        data_dir = self.tmp_path
        original_flush = HindsightMemoryStore(
            memory_config(data_dir), client=client
        )._ledger._flush

        first_store = HindsightMemoryStore(memory_config(data_dir), client=client)
        first_store._ledger._flush = lambda self=None: (_ for _ in ()).throw(OSError(28, "full"))
        try:
            with self.assertRaises(MemoryPersistError):
                first_store.retain(case(session_id="run-1"))
        finally:
            first_store._ledger._flush = original_flush  # type: ignore[method-assign]

        # A fresh store has no in-memory record and no ledger file: it writes again.
        second_store = HindsightMemoryStore(memory_config(data_dir), client=client)
        decision = second_store.retain(case(session_id="run-1"))
        self.assertTrue(decision.retained)
        self.assertEqual(len(client.calls), 2, "the remote operation ran twice")
        self.assertEqual(len(client.documents), 1, "but the remote state converged to one document")

    def test_lock_is_released_when_retain_raises(self):
        """Directly: a failed retain must not leave the lock held."""
        client = DocumentIdClient()
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        store._ledger._flush = lambda self=None: (_ for _ in ()).throw(OSError(28, "full"))
        with self.assertRaises(MemoryPersistError):
            store.retain(case(session_id="run-1"))

        acquired = []
        probe = threading.Thread(
            target=lambda: (
                store._retain_lock.acquire(timeout=5),
                store._retain_lock.release(),
                acquired.append(True),
            )
        )
        probe.start()
        probe.join(timeout=BARRIER_TIMEOUT)
        self.assertFalse(probe.is_alive())
        self.assertEqual(acquired, [True], "the lock was still held after the failure")


class LedgerStateMatrixTests(TempDirMixin):
    """The five ledger states stay distinguishable after A/B/C."""

    def test_matrix_of_ledger_states(self):
        states = {
            "missing": None,
            "valid_empty": {"version": LEDGER_VERSION, "entries": {}},
            "valid": {"version": LEDGER_VERSION, "entries": {"k1": "m1"}},
            "corrupt_json": "not json at all",
            "corrupt_shape": {"version": LEDGER_VERSION},
            "wrong_version": {"version": 42, "entries": {}},
        }
        from debugagent.memory.hindsight_store import Ledger

        for name, content in states.items():
            with self.subTest(state=name):
                target = Path(tempfile.mkdtemp()) / "ledger.json"
                if content is not None:
                    body = content if isinstance(content, str) else json.dumps(content)
                    target.write_text(body, encoding="utf-8")
                if name in ("missing", "valid_empty", "valid"):
                    ledger = Ledger(target)
                    self.assertIsInstance(ledger, Ledger)
                else:
                    with self.assertRaises(MemoryPersistError):
                        Ledger(target)
                    self.assertTrue(target.is_file(), "a corrupt ledger must survive")

    def test_valid_ledger_survives_a_retain_and_keeps_version(self):
        client = DocumentIdClient()
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        store.retain(case(session_id="run-1"))
        path = memory_config(self.tmp_path).ledger_path
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], LEDGER_VERSION)
        self.assertEqual(list(path.parent.glob("*.tmp")), [], "os.replace left no temp file")


class PortContractMatrixTests(TempDirMixin):
    """The public seam reports the same distinctions the store makes."""

    def test_all_three_failure_kinds_are_reachable_through_the_port(self):
        from debugagent.pipeline.memory_port import MemoryFailure

        # persist
        class Flushing(FakeHindsightClient):
            def retain(self, **kwargs):
                self.retained.append(kwargs)
                return type("R", (), {"memory_id": "m", "success": True})()

        client = Flushing()
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        store._ledger._flush = lambda self=None: (_ for _ in ()).throw(OSError(28, "full"))
        with self.assertRaises(MemoryFailure) as ctx:
            HindsightMemoryPort(store).retain(case(session_id="run-1").to_dict())
        self.assertEqual(ctx.exception.kind, "persist")

        # ambiguous
        class TimingOut(FakeHindsightClient):
            def retain(self, **kwargs):
                raise TimeoutError("read timed out")

        with self.assertRaises(MemoryFailure) as ctx:
            HindsightMemoryPort(
                HindsightMemoryStore(memory_config(self.fresh_dir()), client=TimingOut())
            ).retain(case(session_id="run-1").to_dict())
        self.assertEqual(ctx.exception.kind, "ambiguous")

        # unavailable
        class Failing(FakeHindsightClient):
            def retain(self, **kwargs):
                raise type("ServiceException", (RuntimeError,), {"status": 503})("down")

        with self.assertRaises(MemoryFailure) as ctx:
            HindsightMemoryPort(
                HindsightMemoryStore(memory_config(self.fresh_dir()), client=Failing())
            ).retain(case(session_id="run-1").to_dict())
        self.assertEqual(ctx.exception.kind, "unavailable")

    def test_happy_path_is_unchanged_through_the_port(self):
        client = DocumentIdClient()
        port = HindsightMemoryPort(
            HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        )
        decision = port.retain(case(session_id="run-1").to_dict())
        self.assertTrue(decision["retained"])
        self.assertTrue(decision["validated"])
        self.assertEqual(client.calls[0]["document_id"], decision["case_key"])
        self.assertEqual(client.calls[0]["update_mode"], "replace")


class NoOverclaimIntegrationTests(unittest.TestCase):
    """The composed implementation must not claim exactly-once anywhere."""

    def test_retain_docstring_keeps_the_disclaimer(self):
        import inspect

        from debugagent.memory import hindsight_store

        doc = inspect.getsource(hindsight_store.HindsightMemoryStore.retain).lower()
        self.assertIn("not exactly-once", doc)
        self.assertIn("state-idempotent", doc)

    def test_no_retry_loop_exists_in_the_store(self):
        import inspect

        from debugagent.memory import hindsight_store

        source = inspect.getsource(hindsight_store)
        for marker in ("for attempt in", "while True", "max_retries", "backoff"):
            self.assertNotIn(marker, source, f"unexpected retry construct: {marker}")

    def test_no_async_or_operation_id_in_the_source(self):
        import inspect

        from debugagent.memory import hindsight_store

        source = inspect.getsource(hindsight_store)
        self.assertNotIn("retain_async", source)
        self.assertNotIn("operation_id", source)


if __name__ == "__main__":
    unittest.main()
