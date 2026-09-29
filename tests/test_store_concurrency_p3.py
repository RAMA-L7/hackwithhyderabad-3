"""P3-0/P3-1 regression tests: Ledger and HindsightMemoryStore concurrency safety.

These tests reproduce the races identified in the P3 compatibility audit, then pin the
behaviour that the P3-1 synchronisation must guarantee:

* two `Ledger` objects on one path must not lose each other's updates;
* concurrent `Ledger.record()` calls must not lose an update or corrupt the file;
* a concurrent same-case `retain()` must produce exactly ONE remote write;
* concurrent different-case `retain()` calls must both be durable in the ledger.

Determinism: no sleeps. `threading.Barrier` fixes the interleaving, and
`_RendezvousClient` blocks inside `retain()` until every participant has arrived, so
both threads are provably inside the critical section at the same time. A
`threading.Barrier` that is never satisfied raises `BrokenBarrierError` rather than
hanging, so a regression fails loudly instead of stalling the suite.

Scope note: these tests make no claim about `hindsight_client`'s own thread-safety. The
client is an injected offline fake; the guarantee under test is our own Ledger/store
synchronisation only.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

import support
from debugagent.memory.hindsight_store import HindsightMemoryStore, Ledger
from debugagent.schemas import MemoryCase
from support import FakeHindsightClient, memory_config

VALID = {
    "problem_signature": "connection reset above 2MB on orders-api",
    "symptoms": ["reset on large payloads"],
    "environment": {"service": "orders-api", "runtime": "python3.10"},
    "observed_evidence": ["report://orders-api/2026-02-11/payload-matrix.csv"],
    "investigation_trace": ["payload size sweep"],
    "failed_approaches": [{"approach": "raised client timeout", "why_failed": "proxy closed first"}],
    "root_cause": "proxy request size limit",
    "resolution": "raised max_request_bytes to 8MB",
    "outcome": "resolved",
    "verification_notes": "confirmed by re-running the sweep",
}

BARRIER_TIMEOUT = 10.0


def case(**overrides) -> MemoryCase:
    return MemoryCase.from_dict(dict(VALID, **overrides))


def ledger_entries(path: Path) -> dict[str, str]:
    """Read the on-disk ledger the way another process would."""
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))["entries"]


class _TrackingLock:
    """Delegating lock wrapper that counts acquisitions, so a test can assert that a
    particular code path did NOT take the lock. A real `RLock` cannot be monkeypatched
    (`_thread.RLock.acquire` is read-only), hence the wrapper."""

    def __init__(self, inner):
        self._inner = inner
        self.acquisitions = 0

    def acquire(self, *args, **kwargs):
        self.acquisitions += 1
        return self._inner.acquire(*args, **kwargs)

    def release(self):
        return self._inner.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc_info):
        self.release()
        return False


class _TrackingLedger(Ledger):
    """Ledger whose public `lock` is a counting wrapper around the real path-shared lock."""

    def __init__(self, path: Path):
        super().__init__(path)
        self.tracker = _TrackingLock(self._lock)

    @property
    def lock(self):
        return self.tracker


class _CountingClient(FakeHindsightClient):
    """Counts remote retain calls and how many threads are inside one at once.

    It deliberately does NOT rendezvous internally. A barrier inside `retain()` would be
    unsatisfiable once the critical section is correctly serialised: the second thread cannot
    reach the client at all, so the barrier would time out and report a false failure. Counting
    concurrency instead lets the same test describe both worlds.
    """

    def __init__(self, first_entered: threading.Event | None = None, hold_first: threading.Event | None = None):
        super().__init__()
        self.entered = 0
        self.max_concurrent_entered = 0
        self._guard = threading.Lock()
        self._first_entered = first_entered
        self._hold_first = hold_first

    def retain(self, **kwargs):
        with self._guard:
            self.entered += 1
            self.max_concurrent_entered = max(self.max_concurrent_entered, self.entered)
            first = self.entered == 1
        if first:
            if self._first_entered is not None:
                self._first_entered.set()
            if self._hold_first is not None:
                self._hold_first.wait(timeout=BARRIER_TIMEOUT)
        try:
            return super().retain(**kwargs)
        finally:
            with self._guard:
                self.entered -= 1


class LedgerConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.ledger_path = self.tmp_path / "ledger.json"

    def _record_concurrently(self, ledgers: list[Ledger], keys: list[str]) -> list[BaseException | None]:
        """Run `ledgers[i].record(keys[i], ...)` on a barrier-synchronised thread each."""
        barrier = threading.Barrier(len(ledgers))
        errors: list[BaseException | None] = [None] * len(ledgers)

        def worker(index: int) -> None:
            barrier.wait(timeout=BARRIER_TIMEOUT)
            try:
                ledgers[index].record(keys[index], f"mem-{keys[index]}")
            except BaseException as exc:  # noqa: BLE001 - recorded and asserted on by the caller
                errors[index] = exc

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(ledgers))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=BARRIER_TIMEOUT)
            self.assertFalse(thread.is_alive(), "worker did not finish: possible deadlock")
        return errors

    def test_two_ledgers_on_one_path_do_not_lose_updates(self):
        """Two Ledger objects on one path each hold their own snapshot: the second
        write used to clobber the first, because nothing reloaded the file."""
        first = Ledger(self.ledger_path)
        first.record("case-a", "mem-a")
        second = Ledger(self.ledger_path)
        second.record("case-b", "mem-b")

        self.assertEqual(ledger_entries(self.ledger_path), {"case-a": "mem-a", "case-b": "mem-b"})

    def test_concurrent_record_on_one_ledger_keeps_every_entry(self):
        ledger = Ledger(self.ledger_path)
        keys = [f"case-{i}" for i in range(4)]
        errors = self._record_concurrently([ledger] * 4, keys)

        self.assertEqual([e for e in errors if e is not None], [])
        self.assertEqual(ledger_entries(self.ledger_path), {k: f"mem-{k}" for k in keys})

    def test_concurrent_record_across_two_ledgers_keeps_every_entry(self):
        """The lost-update race, made deterministic with a barrier."""
        ledgers = [Ledger(self.ledger_path), Ledger(self.ledger_path)]
        keys = ["case-a", "case-b"]
        errors = self._record_concurrently(ledgers, keys)

        self.assertEqual([e for e in errors if e is not None], [])
        self.assertEqual(ledger_entries(self.ledger_path), {k: f"mem-{k}" for k in keys})

    def test_ledger_file_is_valid_json_after_concurrent_writes(self):
        ledgers = [Ledger(self.ledger_path) for _ in range(3)]
        keys = ["case-a", "case-b", "case-c"]
        self._record_concurrently(ledgers, keys)

        entries = ledger_entries(self.ledger_path)  # raises if the file is not valid JSON
        self.assertEqual(len(entries), 3)

    def test_record_is_observed_by_a_later_ledger_on_the_same_path(self):
        """A Ledger constructed after a write sees it, and `refresh()` picks up a write made
        by an already-constructed Ledger. `retain()` relies on the latter."""
        earlier = Ledger(self.ledger_path)  # snapshot taken BEFORE the write
        first = Ledger(self.ledger_path)
        first.record("case-a", "mem-a")

        later = Ledger(self.ledger_path)
        self.assertTrue(later.has("case-a"))
        self.assertEqual(later.get("case-a"), "mem-a")

        self.assertFalse(earlier.has("case-a"), "precondition: this snapshot predates the write")
        with earlier.lock:
            earlier.refresh()
        self.assertTrue(earlier.has("case-a"))

    def test_has_does_not_write_to_the_file(self):
        ledger = Ledger(self.ledger_path)
        ledger.record("case-a", "mem-a")
        before = self.ledger_path.read_bytes()
        ledger.has("case-a")
        ledger.get("case-a")
        self.assertEqual(self.ledger_path.read_bytes(), before)


class StoreRetainRaceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _retain_concurrently(self, store: HindsightMemoryStore, cases: list[MemoryCase]):
        barrier = threading.Barrier(len(cases))
        decisions: list[object] = [None] * len(cases)
        errors: list[BaseException | None] = [None] * len(cases)

        def worker(index: int) -> None:
            barrier.wait(timeout=BARRIER_TIMEOUT)
            try:
                decisions[index] = store.retain(cases[index])
            except BaseException as exc:  # noqa: BLE001 - recorded and asserted on by the caller
                errors[index] = exc

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(cases))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=BARRIER_TIMEOUT)
            self.assertFalse(thread.is_alive(), "worker did not finish: possible deadlock")
        return decisions, errors

    def test_concurrent_same_case_retain_writes_remotely_once(self):
        """The check-then-act race: `has()` -> remote `retain()` -> `record()`.

        The first thread is held inside the remote call until the second thread has had a
        chance to run. Serialised, the second thread is still blocked on the store lock and
        then finds the ledger already written; unserialised, it slips past `has()` and both
        write remotely. `max_concurrent_entered == 1` is the direct statement of the guarantee.
        """
        first_inside = threading.Event()
        release_first = threading.Event()
        client = _CountingClient(first_entered=first_inside, hold_first=release_first)
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        same = case(session_id="run-a")

        def worker(index: int) -> None:
            if index == 1:
                first_inside.wait(timeout=BARRIER_TIMEOUT)
            decisions[index] = store.retain(same)

        decisions: list[object] = [None, None]
        errors: list[BaseException | None] = [None, None]
        barrier = threading.Barrier(2)

        def run(index: int) -> None:
            barrier.wait(timeout=BARRIER_TIMEOUT)
            try:
                worker(index)
            except BaseException as exc:  # noqa: BLE001 - recorded and asserted on by the caller
                errors[index] = exc

        def releaser() -> None:
            first_inside.wait(timeout=BARRIER_TIMEOUT)
            release_first.set()

        threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
        threads.append(threading.Thread(target=releaser))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=BARRIER_TIMEOUT)
            self.assertFalse(thread.is_alive(), "worker did not finish: possible deadlock")

        self.assertEqual([e for e in errors if e is not None], [])
        self.assertEqual(client.max_concurrent_entered, 1, "two threads were inside retain() at once")
        self.assertEqual(len(client.retained), 1, "same case was retained remotely more than once")
        self.assertEqual(sorted(d.retained for d in decisions), [False, True])
        self.assertEqual(
            sorted(d.reason for d in decisions if not d.retained),
            ["skipped: case already retained (idempotency ledger)"],
        )

    def test_concurrent_same_case_retain_across_two_stores_writes_remotely_once(self):
        """Two stores on one ledger path: this is what `build_port()` produces per call.

        Each store has its own Ledger snapshot, so a per-instance lock would not help. The
        lock is keyed on the ledger path, so both stores serialise and share state.
        """
        first_inside = threading.Event()
        release_first = threading.Event()
        client = _CountingClient(first_entered=first_inside, hold_first=release_first)
        config = memory_config(self.tmp_path)
        first = HindsightMemoryStore(config, client=client)
        second = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        same = case(session_id="run-a")

        decisions: list[object] = [None, None]
        errors: list[BaseException | None] = [None, None]
        barrier = threading.Barrier(2)

        def run(index: int) -> None:
            store = first if index == 0 else second
            barrier.wait(timeout=BARRIER_TIMEOUT)
            if index == 1:
                first_inside.wait(timeout=BARRIER_TIMEOUT)
            try:
                decisions[index] = store.retain(same)
            except BaseException as exc:  # noqa: BLE001
                errors[index] = exc

        def releaser() -> None:
            first_inside.wait(timeout=BARRIER_TIMEOUT)
            release_first.set()

        threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
        threads.append(threading.Thread(target=releaser))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=BARRIER_TIMEOUT)
            self.assertFalse(thread.is_alive(), "worker did not finish: possible deadlock")

        self.assertEqual([e for e in errors if e is not None], [])
        self.assertEqual(len(client.retained), 1, "same case was retained remotely more than once")
        self.assertEqual(sorted(d.retained for d in decisions), [False, True])

    def test_concurrent_different_case_retain_keeps_both_ledger_entries(self):
        """Two different cases, one store: both must be durable, and neither caller may
        see an exception caused by the other's ledger flush."""
        client = _CountingClient()
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)

        decisions, errors = self._retain_concurrently(
            store, [case(session_id="run-a"), case(session_id="run-b")]
        )

        self.assertEqual([e for e in errors if e is not None], [])
        self.assertEqual([d.retained for d in decisions], [True, True])
        self.assertEqual(len(client.retained), 2)
        entries = ledger_entries(memory_config(self.tmp_path).ledger_path)
        self.assertEqual(len(entries), 2, "a concurrent ledger flush lost an entry")

    def test_sequential_retains_are_unaffected(self):
        """The lock must not change single-threaded behaviour."""
        client = FakeHindsightClient()
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        first = store.retain(case(session_id="run-a"))
        second = store.retain(case(session_id="run-a"))

        self.assertTrue(first.retained)
        self.assertFalse(second.retained)
        self.assertEqual(len(client.retained), 1)


class RecallUnaffectedTests(unittest.TestCase):
    """recall() must not acquire the store lock: it touches no ledger state, and locking
    it would serialise reads for no correctness gain."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_recall_does_not_acquire_the_ledger_lock(self):
        """recall() must not take the store lock. It touches no ledger state, so locking it
        would serialise reads for no correctness gain."""
        ledger = _TrackingLedger(memory_config(self.tmp_path).ledger_path)
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=FakeHindsightClient(), ledger=ledger)

        store.recall(query="connection reset")
        self.assertEqual(ledger.tracker.acquisitions, 0, "recall() took the ledger lock; it must not")

        store.retain(case(session_id="run-a"))
        self.assertGreater(ledger.tracker.acquisitions, 0, "retain() must take the lock")

    def test_recall_runs_concurrently_with_retain(self):
        client = FakeHindsightClient()
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        store.retain(case(session_id="run-a"))

        barrier = threading.Barrier(2)
        errors: list[BaseException | None] = [None, None]

        def do_recall() -> None:
            barrier.wait(timeout=BARRIER_TIMEOUT)
            try:
                store.recall(query="connection reset")
            except BaseException as exc:  # noqa: BLE001
                errors[0] = exc

        def do_retain() -> None:
            barrier.wait(timeout=BARRIER_TIMEOUT)
            try:
                store.retain(case(session_id="run-b"))
            except BaseException as exc:  # noqa: BLE001
                errors[1] = exc

        threads = [threading.Thread(target=do_recall), threading.Thread(target=do_retain)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=BARRIER_TIMEOUT)
            self.assertFalse(thread.is_alive(), "worker did not finish: possible deadlock")

        self.assertEqual([e for e in errors if e is not None], [])


if __name__ == "__main__":
    unittest.main()
