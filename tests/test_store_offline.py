"""MemoryStore behaviour against an offline fake backend: retain, recall, idempotency, curation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support
from debugagent.memory.hindsight_store import (
    HindsightMemoryStore,
    Ledger,
    case_tags,
    compute_case_key,
    dedupe_by_case,
    render_content,
)
from debugagent.memory.store import MemorySchemaError
from debugagent.schemas import MemoryCase, RecallResult
from support import FakeHindsightClient, FakeMemory, FakeScores, memory_config

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


def case(**overrides) -> MemoryCase:
    return MemoryCase.from_dict(dict(VALID, **overrides))


class StoreTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)


class RetainTests(StoreTestBase):
    def make_store(self, client=None):
        return HindsightMemoryStore(memory_config(self.tmp_path), client=client or FakeHindsightClient())

    def test_retain_writes_and_returns_memory_id(self):
        client = FakeHindsightClient()
        store = self.make_store(client)
        decision = store.retain(case(case_id="seed-001", session_id="seed-v1"))
        self.assertTrue(decision.retained)
        self.assertTrue(decision.memory_case_id)
        self.assertEqual(len(client.retained), 1)

    def test_retain_sends_structured_metadata_and_tags(self):
        client = FakeHindsightClient()
        self.make_store(client).retain(case(case_id="seed-001", session_id="seed-v1"))
        call = client.retained[0]
        self.assertIn("case_key", call["metadata"])
        self.assertEqual(call["metadata"]["service"], "orders-api")
        self.assertEqual(call["metadata"]["outcome"], "resolved")
        self.assertIn("service:orders-api", call["tags"])
        self.assertIn("debugagent", call["tags"])

    def test_second_identical_retain_is_skipped(self):
        client = FakeHindsightClient()
        store = self.make_store(client)
        first = store.retain(case(case_id="seed-001", session_id="seed-v1"))
        second = store.retain(case(case_id="seed-001", session_id="seed-v1"))
        self.assertTrue(first.retained)
        self.assertFalse(second.retained)
        self.assertIn("already retained", second.reason)
        self.assertEqual(len(client.retained), 1)

    def test_different_session_is_a_different_key(self):
        client = FakeHindsightClient()
        store = self.make_store(client)
        store.retain(case(session_id="run-a"))
        decision = store.retain(case(session_id="run-b"))
        self.assertTrue(decision.retained)
        self.assertEqual(len(client.retained), 2)

    def test_ledger_persists_across_store_instances(self):
        client = FakeHindsightClient()
        config = memory_config(self.tmp_path)
        first = HindsightMemoryStore(config, client=client).retain(case(session_id="seed-v1"))
        second = HindsightMemoryStore(memory_config(self.tmp_path), client=client).retain(
            case(session_id="seed-v1")
        )
        self.assertTrue(first.retained)
        self.assertFalse(second.retained)

    def test_invalid_case_is_rejected_without_writing(self):
        client = FakeHindsightClient()
        store = self.make_store(client)
        broken = MemoryCase(
            problem_signature="",
            symptoms=[],
            environment={},
            observed_evidence=[],
            investigation_trace=[],
            failed_approaches=[],
            root_cause=None,
            resolution=None,
            outcome="resolved",
            verification_notes="",
        )
        with self.assertRaises(MemorySchemaError):
            store.retain(broken)
        self.assertEqual(client.retained, [])


class RecallTests(StoreTestBase):
    def test_recall_maps_provenance_and_scores(self):
        client = FakeHindsightClient(
            recall_results=[
                FakeMemory(
                    text="proxy request size limit truncated the stream",
                    metadata={
                        "case_key": "abc123",
                        "service": "orders-api",
                        "runtime": "python3.10",
                        "outcome": "resolved",
                        "root_cause_key": "proxy-limit",
                    },
                    scores=FakeScores(1.02, 0.87, 0.7),
                    mentioned_at="2026-02-11T10:00:00Z",
                )
            ]
        )
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        result = store.recall(query="connection reset above 2MB")
        self.assertEqual(len(result.items), 1)
        first = result.items[0]
        self.assertEqual(first.case_id, "abc123")
        self.assertAlmostEqual(first.score_final, 1.02)
        self.assertEqual(first.environment["service"], "orders-api")
        self.assertEqual(result.provenance(), ["abc123"])
        self.assertEqual(result.bank_id, "test-bank")

    def test_recall_passes_types_and_budget(self):
        client = FakeHindsightClient(recall_results=[])
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        store.recall(query="anything", types=["world"], max_tokens=256)
        call = client.recall_calls[0]
        self.assertEqual(call["types"], ["world"])
        self.assertEqual(call["max_tokens"], 256)

    def test_blank_query_rejected(self):
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=FakeHindsightClient())
        with self.assertRaises(MemorySchemaError):
            store.recall(query="   ")

    def test_missing_score_defaults_to_zero_rather_than_raising(self):
        client = FakeHindsightClient(
            recall_results=[FakeMemory("t", {"case_key": "k"}, FakeScores(0.0))]
        )
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        self.assertEqual(store.recall(query="q").items[0].score_final, 0.0)


class CurationTests(StoreTestBase):
    def test_update_sends_text_and_reason(self):
        client = FakeHindsightClient()
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        store.update("abc123", text="corrected fact", reason="misextracted subject")
        payload = client.updates[-1]
        self.assertEqual(payload["update_memory_request"]["text"], "corrected fact")
        self.assertEqual(payload["memory_id"], "abc123")

    def test_invalidate_sets_invalidated_state(self):
        client = FakeHindsightClient()
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        store.invalidate("abc123", reason="superseded by a later case")
        self.assertEqual(
            client.updates[-1]["update_memory_request"]["state"], "invalidated"
        )

    def test_update_requires_reason(self):
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=FakeHindsightClient())
        with self.assertRaises(MemorySchemaError):
            store.update("abc123", text="x", reason="  ")

    def test_invalidate_requires_case_id(self):
        store = HindsightMemoryStore(memory_config(self.tmp_path), client=FakeHindsightClient())
        with self.assertRaises(MemorySchemaError):
            store.invalidate("", reason="stale")


class BankProvisioningTests(StoreTestBase):
    def test_bank_is_created_when_absent(self):
        client = FakeHindsightClient()
        HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        self.assertIn("test-bank", client.created_banks)

    def test_existing_bank_is_not_recreated(self):
        client = FakeHindsightClient()
        client.banks.add("test-bank")
        HindsightMemoryStore(memory_config(self.tmp_path), client=client)
        self.assertEqual(client.created_banks, [])


class HelperTests(StoreTestBase):
    def test_case_key_is_deterministic_and_signature_sensitive(self):
        a = compute_case_key(case(), "seed-v1")
        b = compute_case_key(case(), "seed-v1")
        c = compute_case_key(case(problem_signature="different"), "seed-v1")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_content_render_includes_failed_approaches(self):
        rendered = render_content(case())
        self.assertIn("raised client timeout", rendered)
        self.assertIn("proxy request size limit", rendered)

    def test_tags_include_outcome(self):
        self.assertIn("outcome:resolved", case_tags(case()))

    def test_ledger_ignores_corrupt_file(self):
        path = self.tmp_path / "ledger.json"
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(Ledger(path).has("any"), False)


class DedupeTests(unittest.TestCase):
    @staticmethod
    def make_result(
        case_id: str, score: float, text: str, semantic: float | None = None
    ) -> RecallResult:
        return RecallResult(
            case_id=case_id,
            text=text,
            score_final=score,
            score_semantic=score if semantic is None else semantic,
            score_keyword=score,
            environment={"service": "orders-api"},
            outcome="resolved",
        )

    def test_duplicate_case_rows_collapse_to_best_row(self):
        items = [
            self.make_result("case-a", 0.10, "chunk one"),
            self.make_result("case-a", 0.80, "chunk two"),
            self.make_result("case-b", 0.50, "chunk three"),
        ]
        out = dedupe_by_case(items)
        self.assertEqual([i.case_id for i in out], ["case-a", "case-b"])
        self.assertEqual(out[0].score_final, 0.80)

    def test_semantic_breaks_ties_on_final(self):
        low = self.make_result("case-a", 0.50, "low semantic", semantic=0.10)
        high = self.make_result("case-a", 0.50, "high semantic", semantic=0.90)
        out = dedupe_by_case([low, high])
        self.assertEqual(out[0].text, "high semantic")

    def test_dedupe_is_stable_for_identical_rows(self):
        first = self.make_result("case-a", 0.50, "first")
        second = self.make_result("case-a", 0.50, "second")
        out = dedupe_by_case([first, second])
        self.assertEqual(out[0].text, "first")

    def test_empty_input(self):
        self.assertEqual(dedupe_by_case([]), [])


if __name__ == "__main__":
    unittest.main()
