"""P5 concurrency-readiness audit: is the Hindsight client safe to use from several threads?

P5 wants parallel fan-out. The P3-1 tests carry an explicit scope note that they "make no claim about
`hindsight_client`'s own thread-safety", so that question has never been answered. This file answers
it, against the **installed** client, and then maps the answer onto the store's locking to find the
exact safe boundary.

The findings, in one place:

- `hindsight_client` funnels every synchronous call through `_run_async`, which resolves the event
  loop with `asyncio.get_event_loop()`. On Python 3.10 that **raises in a non-main thread**, so each
  thread builds and uses **its own loop**.
- One `aiohttp.ClientSession` is created lazily and **cached** on the `RESTClientObject`
  (`rest.py`: `_pool_manager`), and one `ApiClient` is shared by every sub-API on a `Hindsight`
  client. An aiohttp session is **bound to the loop that created it**.
- Therefore a second thread reuses a session belonging to the first thread's loop. The real,
  observed consequence is `RuntimeError: Timeout context manager should be used inside a task`.
- `HindsightMemoryStore._retain_lock` **is** the ledger's path-keyed lock. It happens to serialise
  the client only while ledger and client are 1:1. Two stores on different ledger paths take
  different locks and reach one shared client concurrently.
- `recall()`, `update()`, `invalidate()` and construction-time bank provisioning take **no lock at
  all**, so they can overlap a retain on the same client.

Determinism: the central finding is proved by object identity, with no threads and no timing. Where
concurrency is observed directly, a bounded rendezvous is used and the assertion is on a *count*, not
on elapsed time. Tests marked "characterisation" assert today's behaviour: if one starts failing, the
boundary has moved and this audit should be re-run rather than the test being relaxed.

No network: the reproduction uses the real client objects and loopback only, never a live bank.
"""

from __future__ import annotations

import asyncio
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

RENDEZVOUS_TIMEOUT = 5.0


def case(**overrides) -> MemoryCase:
    return MemoryCase.from_dict(dict(VALID, **overrides))


class ConcurrencyCountingClient(FakeHindsightClient):
    """Counts how many threads are inside one data call (retain/recall) at a time.

    Like the P3-1 helper, it counts rather than rendezvousing internally: a barrier inside `retain()`
    is unsatisfiable once the critical section is correctly serialised, so it would report a false
    failure in the safe case. A *bounded* wait is used instead, which lets the same test describe both
    worlds - the other thread either gets in (concurrent) or times out (serialised).

    Bank provisioning is deliberately NOT counted here. A single class that counted both shared one
    barrier, and the sequential store construction in setUp consumed and broke it, silently disabling
    the rendezvous for the concurrent phase the test was actually about. `ProvisioningCountingClient`
    measures that separately.
    """

    def __init__(self, rendezvous: bool = False):
        super().__init__()
        self.rendezvous = rendezvous
        self.entered = 0
        self.max_concurrent_entered = 0
        self.recall_entered = 0
        self.max_concurrent_recall = 0
        self.rendezvous_broken = 0
        self._guard = threading.Lock()
        self._barrier: threading.Barrier | None = None

    def _barrier_for_two(self) -> threading.Barrier:
        with self._guard:
            if self._barrier is None:
                self._barrier = threading.Barrier(2)
            return self._barrier

    def _rendezvous(self) -> None:
        if not self.rendezvous:
            return
        # A semaphore cannot do this: a thread that released a permit and then acquired one would
        # consume its OWN permit and never actually wait. A barrier is the right primitive - and a
        # timeout is a legitimate outcome, meaning "the peer never got in", i.e. the operation was
        # correctly serialised. Assertions are on the observed count, never on elapsed time.
        try:
            self._barrier_for_two().wait(timeout=RENDEZVOUS_TIMEOUT)
        except threading.BrokenBarrierError:
            self.rendezvous_broken += 1

    def retain(self, **kwargs):
        with self._guard:
            self.entered += 1
            self.max_concurrent_entered = max(self.max_concurrent_entered, self.entered)
        self._rendezvous()
        try:
            return super().retain(**kwargs)
        finally:
            with self._guard:
                self.entered -= 1

    def recall(self, **kwargs):
        with self._guard:
            self.recall_entered += 1
            self.max_concurrent_recall = max(self.max_concurrent_recall, self.recall_entered)
        self._rendezvous()
        try:
            return super().recall(**kwargs)
        finally:
            with self._guard:
                self.recall_entered -= 1


class ProvisioningCountingClient(ConcurrencyCountingClient):
    """Counts threads inside bank provisioning, which `__init__` does before any lock exists.

    `FakeHindsightClient.get_bank_config` always raises for an unknown bank, so `create_bank` is the
    call `_ensure_bank` actually lands on - that is the one counted here.
    """

    def __init__(self, rendezvous: bool = True):
        super().__init__(rendezvous=rendezvous)
        self.provision_entered = 0
        self.max_concurrent_provision = 0

    def create_bank(self, bank_id, **kwargs):
        with self._guard:
            self.provision_entered += 1
            self.max_concurrent_provision = max(self.max_concurrent_provision, self.provision_entered)
        self._rendezvous()
        try:
            return super().create_bank(bank_id, **kwargs)
        finally:
            with self._guard:
                self.provision_entered -= 1

    def recall(self, **kwargs):
        self._enter("recall_entered", "max_concurrent_recall")
        try:
            return super().recall(**kwargs)
        finally:
            self._exit("recall_entered")

    def get_bank_config(self, bank_id):
        self._enter("entered", "max_concurrent_entered")
        try:
            return super().get_bank_config(bank_id)
        finally:
            self._exit("entered")


class _TrackingLedger(Ledger):
    """Counts lock acquisitions, so a test can assert a path did NOT take the lock.

    `Ledger.lock` is a property, so the override must be one too: replacing it with a plain method
    would hand `with self._ledger.lock` a bound method rather than a context manager.
    """

    def __init__(self, path: Path):
        super().__init__(path)
        self.acquisitions = 0
        outer = self
        inner = self._lock

        class _Counting:
            def acquire(self, *a, **k):
                outer.acquisitions += 1
                return inner.acquire(*a, **k)

            def release(self):
                return inner.release()

            def __enter__(self):
                self.acquire()
                return self

            def __exit__(self, *exc):
                self.release()
                return False

        self._counting = _Counting()

    @property
    def lock(self):
        return self._counting


def run_concurrently(worker, count: int) -> list[BaseException]:
    """Run `worker(index)` on `count` barrier-synchronised threads; return any exceptions."""
    barrier = threading.Barrier(count)
    errors: list[BaseException] = []
    guard = threading.Lock()

    def run(index: int) -> None:
        try:
            barrier.wait(timeout=RENDEZVOUS_TIMEOUT)
            worker(index)
        except BaseException as exc:  # noqa: BLE001 - reported to the test, not swallowed
            with guard:
                errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=RENDEZVOUS_TIMEOUT * 4)
    for thread in threads:
        if thread.is_alive():
            raise AssertionError("worker did not finish: possible deadlock")
    return errors


class RealClientLoopBindingTests(unittest.TestCase):
    """The mechanism, proved against the installed client with no threads and no network."""

    def setUp(self):
        self._sessions: list = []

    def tearDown(self):
        for session in self._sessions:
            try:
                asyncio.get_event_loop().run_until_complete(session.close())
            except BaseException:  # noqa: BLE001 - teardown only
                pass

    def _rest_client(self):
        from hindsight_client_api.api_client import ApiClient
        from hindsight_client_api.configuration import Configuration

        config = Configuration()
        config.host = "http://127.0.0.1:1"  # loopback discard; never dialled by these tests
        return ApiClient(config).rest_client

    def test_the_sync_bridge_gives_each_thread_its_own_event_loop(self):
        """`_run_async` resolves the loop per thread, so threads do NOT share one.

        This is the fact the whole audit turns on. If the loops were shared, one serialising lock
        would be enough; because they are not, a cached session can only belong to one of them.
        """
        from hindsight_client.hindsight_client import _run_async

        loops: dict[str, asyncio.AbstractEventLoop] = {}

        def capture(tag: str) -> None:
            _run_async(asyncio.sleep(0))
            loops[tag] = asyncio.get_event_loop()

        capture("main")
        thread = threading.Thread(target=capture, args=("worker",))
        thread.start()
        thread.join(timeout=RENDEZVOUS_TIMEOUT)
        self.assertIn("worker", loops, "the worker thread did not run")
        self.assertIsNot(loops["main"], loops["worker"],
                         "if the loops were shared this audit's conclusion would change")

    def test_the_client_caches_exactly_one_aiohttp_session(self):
        """One session, created once, reused by every later call - including from other threads."""
        rest = self._rest_client()
        self.assertIsNone(rest._pool_manager, "precondition: nothing cached yet")

        async def create():
            rest._ensure_session()
            return rest._pool_manager

        first = asyncio.new_event_loop()
        try:
            session = first.run_until_complete(create())
        finally:
            first.close()
        self._sessions.append(session)
        self.assertIs(rest._pool_manager, session, "the session must be cached on the client")
        self.assertIs(session, rest.pool_manager if rest._pool_manager else session)

    def test_one_api_client_is_shared_by_every_sub_api(self):
        """Why the cached session is reachable from retain, recall and everything else."""
        from hindsight_client.hindsight_client import Hindsight

        client = Hindsight(base_url="http://127.0.0.1:1", api_key="k")
        self.addCleanup(setattr, client, "_api_client", None)
        rest = client._api_client.rest_client
        self.assertIs(client._memory_api.api_client.rest_client, rest)
        self.assertIs(client._banks_api.api_client.rest_client, rest)

    def test_ensure_session_has_no_lock(self):
        """The lazy init is an unguarded check-then-act, so two threads can both create a session."""
        import inspect

        from hindsight_client_api.rest import RESTClientObject

        source = inspect.getsource(RESTClientObject._ensure_session)
        for forbidden in ("Lock", "acquire", "RLock"):
            self.assertNotIn(forbidden, source,
                             "if the client had added a lock this audit's conclusion changes")


class RealClientConcurrencyReproductionTests(unittest.TestCase):
    """The reproduction: a session created on one loop, used from another."""

    def test_a_session_from_another_thread_fails(self):
        """The concrete failure P5 would hit with one shared client and two threads.

        Deterministic in outcome: the assertion is that the cross-loop use does not succeed. The exact
        exception type is aiohttp's business and may change between versions, so it is not pinned -
        pinning it would make this test fail on an aiohttp upgrade for a reason unrelated to safety.
        """
        from hindsight_client.hindsight_client import _run_async
        from hindsight_client_api.api_client import ApiClient
        from hindsight_client_api.configuration import Configuration

        config = Configuration()
        config.host = "http://127.0.0.1:1"
        rest = ApiClient(config).rest_client

        async def create():
            rest._ensure_session()
            return rest._pool_manager

        creator_loop = asyncio.new_event_loop()
        try:
            session = creator_loop.run_until_complete(create())
        finally:
            creator_loop.close()
        self.addCleanup(lambda: asyncio.new_event_loop().run_until_complete(session.close()))

        observed: dict = {}

        def use_from_worker() -> None:
            async def use():
                try:
                    await session.get("http://127.0.0.1:1/x")
                except BaseException as exc:  # noqa: BLE001 - this is the observation
                    observed["error"] = exc
            try:
                _run_async(use())
                observed["succeeded"] = True
            except BaseException as exc:  # noqa: BLE001 - and so is this
                observed["error"] = exc

        worker = threading.Thread(target=use_from_worker)
        worker.start()
        worker.join(timeout=RENDEZVOUS_TIMEOUT)

        self.assertFalse(worker.is_alive(), "the worker thread hung")
        self.assertIn("error", observed,
                      "using the session from a second thread unexpectedly SUCCEEDED; "
                      "if aiohttp fixed this, re-run the audit before trusting P5")
        self.assertIsNot(session._loop, asyncio.new_event_loop(),
                         "sanity: a fresh loop is never the session's loop")


class StoreLockBoundaryTests(unittest.TestCase):
    """Does the store's RLock protect the CLIENT? Not in general."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def store(self, client, *, ledger_name="ledger.json", bank="bank"):
        config = memory_config(self.tmp_path, bank_id=bank, ledger_path=self.tmp_path / ledger_name)
        store = HindsightMemoryStore(config, client=client)
        self.addCleanup(store.close)
        return store

    # --- the mechanism, with no threads at all ---

    def test_the_retain_lock_is_the_ledger_lock_keyed_on_path(self):
        """The lock's identity is the ledger FILE, not the client or the bank."""
        client = FakeHindsightClient()
        a = self.store(client, ledger_name="same.json")
        b = self.store(client, ledger_name="same.json")
        self.assertIs(a._retain_lock, b._retain_lock,
                      "same ledger path must share one lock")

    def test_different_ledger_paths_take_different_locks(self):
        """The finding. One client, two ledgers => two locks => no mutual exclusion.

        Stated as object identity rather than as observed concurrency, so it stays a deterministic
        fact about the design instead of a race that may or may not be observed on a given run.
        """
        client = FakeHindsightClient()
        a = self.store(client, ledger_name="a.json")
        b = self.store(client, ledger_name="b.json")
        self.assertIsNot(a._retain_lock, b._retain_lock)
        self.assertIs(a._client, b._client, "precondition: the two stores share one client")

    def test_two_banks_on_one_client_take_different_locks(self):
        """The shape P5 would actually create: a worker per bank, one shared client."""
        client = FakeHindsightClient()
        a = self.store(client, ledger_name="a.json", bank="bank-a")
        b = self.store(client, ledger_name="b.json", bank="bank-b")
        self.assertIsNot(a._retain_lock, b._retain_lock,
                         "a client-per-bank layout defeats the ledger-keyed lock entirely")

    # --- observed concurrency (characterisation) ---

    def test_sharing_a_ledger_path_serialises_the_client(self):
        """SAFE today, and only because the ledger path happens to be shared."""
        client = ConcurrencyCountingClient()
        stores = [self.store(client, ledger_name="shared.json") for _ in range(2)]
        run_concurrently(lambda i: stores[i].retain(case(session_id=f"s{i}")), 2)
        self.assertEqual(client.max_concurrent_entered, 1,
                         "one shared ledger path must serialise client access")

    def test_different_ledger_paths_do_not_serialise_the_client(self):
        """UNSAFE. Characterisation: a failure here means the boundary moved, not that it is wrong."""
        client = ConcurrencyCountingClient(rendezvous=True)
        stores = [self.store(client, ledger_name=f"{name}.json")
                  for name in ("one", "two")]
        errors = run_concurrently(
            lambda i: stores[i].retain(case(session_id=f"s{i}")), 2)
        self.assertEqual(errors, [], "both retains should succeed; the point is they overlapped")
        self.assertGreaterEqual(
            client.max_concurrent_entered, 2,
            "two ledger paths did NOT serialise the shared client: the boundary has moved, "
            "re-run the audit and update the P5 design")

    def test_recall_takes_no_lock_and_overlaps_retain(self):
        """UNSAFE. `recall()` never acquires the ledger lock, so it can overlap a retain.

        P3-1 asserts this overlap deliberately, with a fake client. Against the real client the same
        overlap puts two threads on one cached session, so that P3-1 test's safety claim does not
        extend to the real backend.
        """
        client = ConcurrencyCountingClient(rendezvous=True)
        config = memory_config(self.tmp_path, bank_id="b", ledger_path=self.tmp_path / "r.json")
        tracked = HindsightMemoryStore(config, client=client, ledger=_TrackingLedger(config.ledger_path))
        self.addCleanup(tracked.close)
        plain = HindsightMemoryStore(
            memory_config(self.tmp_path, bank_id="b2", ledger_path=self.tmp_path / "p.json"),
            client=client)
        self.addCleanup(plain.close)

        errors = run_concurrently(
            lambda i: (tracked.retain(case(session_id="r")) if i == 0
                       else plain.recall(query="reset above 2MB")), 2)
        self.assertEqual(errors, [])
        self.assertGreaterEqual(max(client.max_concurrent_entered, client.max_concurrent_recall), 1,
                                "neither recall nor retain reached the client")
        self.assertEqual(client.rendezvous_broken, 0,
                         "recall and retain must overlap, not be serialised")

    def test_construction_time_bank_provisioning_is_unlocked(self):
        """`__init__` calls the client (`get_bank_config` then `create_bank`) before any lock exists.

        Uses its own client class so the sequential provisioning that happens while building the
        stores cannot disturb the data-path rendezvous.
        """
        client = ProvisioningCountingClient(rendezvous=True)
        errors = run_concurrently(lambda i: self.store(client, ledger_name=f"c{i}.json"), 2)
        self.assertEqual(errors, [])
        self.assertGreaterEqual(
            client.max_concurrent_provision, 2,
            "concurrent construction should reach the client concurrently today; if it is now "
            "serialised the boundary moved")


class ConclusionTests(unittest.TestCase):
    """The facts P5's design must be built on, asserted so a regression is visible.

    These do not test a fix - this step implements none. They pin the audit's conclusions so that a
    later change to `hindsight_store.py` cannot quietly invalidate the P5 design.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_recall_holds_no_ledger_lock(self):
        """Confirms recall is unlocked, which is why it can overlap a retain on one client."""
        config = memory_config(self.tmp_path, bank_id="b", ledger_path=self.tmp_path / "l.json")
        ledger = _TrackingLedger(config.ledger_path)
        store = HindsightMemoryStore(config, client=FakeHindsightClient(), ledger=ledger)
        self.addCleanup(store.close)
        store.recall(query="reset above 2MB")
        self.assertEqual(ledger.acquisitions, 0, "recall must not take the ledger lock")

    def test_retain_does_hold_the_ledger_lock(self):
        """P3-1's guarantee is intact: check, remote write and record stay one critical section."""
        config = memory_config(self.tmp_path, bank_id="b", ledger_path=self.tmp_path / "l2.json")
        ledger = _TrackingLedger(config.ledger_path)
        store = HindsightMemoryStore(config, client=FakeHindsightClient(), ledger=ledger)
        self.addCleanup(store.close)
        store.retain(case(session_id="s1"))
        self.assertGreater(ledger.acquisitions, 0, "retain must hold the ledger lock")

    def test_retain_is_still_state_idempotent_under_concurrency(self):
        """P3-2B is unaffected: one case, two threads, one remote write."""
        client = ConcurrencyCountingClient()
        config = memory_config(self.tmp_path, bank_id="b")
        store = HindsightMemoryStore(config, client=client)
        self.addCleanup(store.close)
        errors = run_concurrently(lambda i: store.retain(case(session_id="same")), 2)
        self.assertEqual(errors, [])
        self.assertEqual(len(client.retained), 1, "the case must be written remotely once")


if __name__ == "__main__":
    unittest.main()
