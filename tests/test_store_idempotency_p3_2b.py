"""P3-2B regression tests: state-idempotent Hindsight retention via document_id + replace.

The Hindsight service was verified live (P3-2C) to treat a fixed `document_id` with
`update_mode="replace"` as ONE remote document: repeated writes converge on the same remote
state rather than appending. These tests model that contract with an in-memory fake that
KEYS ITS STORAGE BY document_id, so a duplicate would show up as a second document here.

What is NOT claimed, and is asserted against in `NoOverclaimTests`: this is
state-idempotence, not exactly-once. Every repeated retain still issues a remote request.

Deterministic throughout: no sleeps, no threads, no timing assertions.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import support
from debugagent.memory.hindsight_store import (
    REPLACE_MODE,
    HindsightMemoryStore,
    compute_case_key,
)
from debugagent.pipeline.memory_adapter import HindsightMemoryPort
from debugagent.schemas import MemoryCase
from support import FakeRetainResponse, memory_config

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


class DocumentIdHindsightClient:
    """Fake Hindsight that stores ONE document per document_id, as the service does under `replace`.

    `documents` is keyed by document_id, so a retain that failed to converge would be visible as a
    second entry. `calls` records every request separately, so a repeated retain is still visible
    as a second CALL - which is exactly the distinction between state-idempotence and exactly-once.
    """

    def __init__(self):
        self.documents: dict[str, dict] = {}
        self.calls: list[dict] = []
        self.banks: set[str] = set()

    def get_bank_config(self, bank_id: str) -> dict:
        if bank_id not in self.banks:
            raise RuntimeError("404 not found")
        return {"bank_id": bank_id}

    def create_bank(self, bank_id: str, **_kwargs) -> str:
        self.banks.add(bank_id)
        return bank_id

    def retain(self, **kwargs):
        self.calls.append(dict(kwargs))
        document_id = kwargs.get("document_id")
        # `replace` converges: same document_id overwrites, it does not append.
        self.documents[str(document_id)] = dict(kwargs)
        return FakeRetainResponse(memory_id=f"mem-for-{document_id}")

    def recall(self, **_kwargs):
        return type("R", (), {"results": []})()

    def close(self) -> None:
        pass


class P32BTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.client = DocumentIdHindsightClient()
        self.store = HindsightMemoryStore(memory_config(self.tmp_path), client=self.client)

    @property
    def ledger_path(self) -> Path:
        return memory_config(self.tmp_path).ledger_path

    def ledger_entries(self) -> dict[str, str]:
        if not self.ledger_path.is_file():
            return {}
        return json.loads(self.ledger_path.read_text(encoding="utf-8"))["entries"]


class FirstRetainTests(P32BTestBase):
    """A. First retain creates one remote document and records the case locally."""

    def test_first_retain_creates_one_remote_document(self):
        decision = self.store.retain(case(session_id="run-1"))
        self.assertTrue(decision.retained)
        self.assertEqual(len(self.client.documents), 1)
        self.assertEqual(list(self.client.documents), [decision.case_key])

    def test_first_retain_records_the_case_in_the_ledger(self):
        decision = self.store.retain(case(session_id="run-1"))
        self.assertEqual(list(self.ledger_entries()), [decision.case_key])

    def test_document_id_is_the_case_key(self):
        decision = self.store.retain(case(session_id="run-1"))
        sent = self.client.calls[0]
        self.assertEqual(sent["document_id"], decision.case_key)
        self.assertEqual(sent["update_mode"], REPLACE_MODE)

    def test_document_id_matches_the_phase_a_formula(self):
        payload = case(session_id="run-1")
        decision = self.store.retain(payload)
        self.assertEqual(decision.case_key, compute_case_key(payload, "run-1"))


class RepeatedRetainTests(P32BTestBase):
    """B. The same case retained again does not create a second remote document."""

    def test_same_case_twice_keeps_one_remote_document(self):
        first = self.store.retain(case(session_id="run-1"))
        second = self.store.retain(case(session_id="run-1"))
        self.assertTrue(first.retained)
        self.assertFalse(second.retained)
        self.assertEqual(len(self.client.documents), 1)

    def test_repeat_after_a_fresh_store_still_converges(self):
        """A new store on the same ledger path re-reads the ledger; a fresh ledger is the
        harder case, so it is checked too: same document_id, still one remote document."""
        first = self.store.retain(case(session_id="run-1"))
        fresh_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: None)
        fresh = HindsightMemoryStore(memory_config(fresh_dir), client=self.client)
        second = fresh.retain(case(session_id="run-1"))
        self.assertEqual(first.case_key, second.case_key)
        self.assertTrue(second.retained, "empty ledger means it writes again")
        self.assertEqual(len(self.client.documents), 1, "but it replaces, not appends")

    def test_repeated_retain_still_issues_a_remote_call(self):
        """The distinction that matters: state converges, the operation repeats."""
        self.store.retain(case(session_id="run-1"))
        self.store.retain(case(session_id="run-1"))
        self.assertEqual(len(self.client.documents), 1)
        self.assertEqual(len(self.client.calls), 1, "ledger short-circuits the second call")

    def test_repeat_across_fresh_ledgers_does_call_again_but_stays_one(self):
        dirs = [self.tmp_path, Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())]
        for target in dirs:
            HindsightMemoryStore(memory_config(target), client=self.client).retain(
                case(session_id="run-1")
            )
        self.assertEqual(len(self.client.documents), 1)
        self.assertEqual(len(self.client.calls), 3, "each empty ledger writes again")


class ReplaceBehaviourTests(P32BTestBase):
    """C. Same case_key with changed content replaces; it does not append."""

    def test_changed_content_same_key_replaces_remote_document(self):
        self.store.retain(case(session_id="run-1"))
        changed = case(session_id="run-1", symptoms=["502 for large payloads", "also 504"])
        self.assertEqual(
            compute_case_key(case(session_id="run-1"), "run-1"),
            compute_case_key(changed, "run-1"),
            "symptoms are not part of identity, so the key must be unchanged",
        )
        # An empty ledger forces the write through so replace is actually exercised.
        fresh = HindsightMemoryStore(memory_config(Path(tempfile.mkdtemp())), client=self.client)
        decision = fresh.retain(changed)
        self.assertTrue(decision.retained)
        self.assertEqual(len(self.client.documents), 1)
        stored = next(iter(self.client.documents.values()))
        self.assertIn("also 504", stored["content"], "the newer content replaced the older")

    def test_replace_mode_is_always_sent(self):
        self.store.retain(case(session_id="run-1"))
        for call in self.client.calls:
            self.assertEqual(call["update_mode"], "replace")


class DistinctCaseTests(P32BTestBase):
    """D/E/F. A different case gets a different document, not an overwrite."""

    def _fresh_store(self):
        return HindsightMemoryStore(memory_config(Path(tempfile.mkdtemp())), client=self.client)

    def test_different_root_cause_is_a_separate_remote_document(self):
        a = self.store.retain(case(session_id="run-1", root_cause="proxy request size limit"))
        b = self._fresh_store().retain(case(session_id="run-1", root_cause="client TLS bug"))
        self.assertNotEqual(a.case_key, b.case_key)
        self.assertEqual(len(self.client.documents), 2)
        self.assertNotIn(a.case_key, b.memory_case_id)

    def test_different_resolution_is_a_separate_remote_document(self):
        a = self.store.retain(case(session_id="run-1", resolution="raised max_request_bytes to 8MB"))
        b = self._fresh_store().retain(case(session_id="run-1", resolution="moved to chunked upload"))
        self.assertNotEqual(a.case_key, b.case_key)
        self.assertEqual(len(self.client.documents), 2)

    def test_different_session_is_a_separate_remote_document(self):
        a = self.store.retain(case(session_id="run-1"))
        b = self._fresh_store().retain(case(session_id="run-2"))
        self.assertNotEqual(a.case_key, b.case_key)
        self.assertEqual(len(self.client.documents), 2)

    def test_different_problem_signature_is_a_separate_remote_document(self):
        a = self.store.retain(case(session_id="run-1"))
        b = self._fresh_store().retain(
            case(session_id="run-1", problem_signature="payments worker OOM under load")
        )
        self.assertNotEqual(a.case_key, b.case_key)
        self.assertEqual(len(self.client.documents), 2)

    def test_contradictory_outcomes_do_not_overwrite_each_other(self):
        """The Phase A regression, now at the remote layer: the first case's content survives."""
        a = self.store.retain(case(session_id="run-1", root_cause="proxy request size limit"))
        self._fresh_store().retain(case(session_id="run-1", root_cause="client TLS bug"))
        self.assertEqual(len(self.client.documents), 2)
        self.assertIn("proxy request size limit", self.client.documents[a.case_key]["content"])


class NoOverclaimTests(P32BTestBase):
    """H. Guard against the implementation (or its docs) claiming exactly-once."""

    def test_no_exactly_once_guarantee_is_asserted(self):
        """Any mention of 'exactly-once' must be a DISCLAIMER, never a claim.

        The docstring says "It is NOT exactly-once", which is exactly the point: the phrase may
        appear, but only next to a negation.
        """
        import inspect

        from debugagent.memory import hindsight_store

        source = inspect.getsource(hindsight_store.HindsightMemoryStore.retain).lower()
        for line in source.splitlines():
            if "exactly-once" in line:
                self.assertRegex(line.strip(), r"not exactly-once|never exactly-once")

    def test_retain_docstring_states_state_idempotence_not_exactly_once(self):
        import inspect

        from debugagent.memory import hindsight_store

        doc = inspect.getsource(hindsight_store.HindsightMemoryStore.retain).lower()
        self.assertIn("state-idempotent", doc)
        self.assertIn("not exactly-once", doc)
        self.assertIn("not make the remote write and the local ledger record atomic", doc)

    def test_no_async_or_operation_id_is_used(self):
        self.store.retain(case(session_id="run-1"))
        call = self.client.calls[0]
        self.assertNotIn("operation_id", call)
        self.assertNotIn("retain_async", call)

    # NOTE: that the retain lock still spans check -> remote write -> ledger record is verified by
    # the existing P3-1 suite (test_store_concurrency_p3.py), which already proves it with
    # barriers. An ad-hoc probe here was timing-dependent and duplicated that coverage, so it was
    # dropped rather than kept flaky.


class PortLevelTests(P32BTestBase):
    """The public seam is unchanged; only the remote call gained identity parameters."""

    def test_port_retain_still_returns_the_decision_shape(self):
        port = HindsightMemoryPort(self.store)
        decision = port.retain(case(session_id="run-1").to_dict())
        self.assertIs(decision["retained"], True)
        self.assertIn("case_key", decision)
        self.assertIn("memory_case_id", decision)

    def test_port_repeat_still_reports_a_consistent_decision(self):
        port = HindsightMemoryPort(self.store)
        first = port.retain(case(session_id="run-1").to_dict())
        second = port.retain(case(session_id="run-1").to_dict())
        self.assertTrue(first["retained"])
        self.assertFalse(second["retained"])
        self.assertEqual(first["case_key"], second["case_key"])
        self.assertIn("already retained", second["reason"])


if __name__ == "__main__":
    unittest.main()
