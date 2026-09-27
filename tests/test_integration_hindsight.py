"""Live Hindsight Cloud integration. Skipped unless HINDSIGHT_URL is configured."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import support
from debugagent.config import load_memory_config
from debugagent.memory.hindsight_store import HindsightMemoryStore
from debugagent.memory.matching import AbstentionPolicy, classify_candidates
from debugagent.schemas import MemoryCase
from support import memory_config

POLICY = AbstentionPolicy()


def configured() -> bool:
    return bool(os.environ.get("HINDSIGHT_URL", "").strip())


def unique_suffix() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")


def live_case(signature: str, session: str) -> MemoryCase:
    return MemoryCase.from_dict(
        {
            "problem_signature": signature,
            "symptoms": ["synthetic integration probe symptom"],
            "environment": {"service": "probe-service", "runtime": "python3.10"},
            "observed_evidence": ["probe://evidence/1"],
            "investigation_trace": ["synthetic probe trace"],
            "failed_approaches": [
                {"approach": "synthetic failed approach", "why_failed": "synthetic reason"}
            ],
            "root_cause": "synthetic root cause",
            "resolution": "synthetic resolution",
            "outcome": "resolved",
            "verification_notes": "synthetic integration case",
            "case_id": f"probe-{unique_suffix()}",
            "session_id": session,
        }
    )


@unittest.skipUnless(configured(), "HINDSIGHT_URL not configured")
class LiveStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.session = f"itest-{unique_suffix()}"
        self.config = memory_config(
            self.tmp_path,
            hindsight_url=os.environ["HINDSIGHT_URL"],
            hindsight_api_key=os.environ.get("HINDSIGHT_API_KEY", ""),
            bank_id=os.environ.get("HINDSIGHT_BANK_ID", "debugagent-itest"),
        )
        self.store = HindsightMemoryStore(self.config)
        self.addCleanup(self._close_store)

    def _close_store(self):
        client = getattr(self.store, "_client", None)
        if client is None:
            return
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass

    def test_01_bank_is_reachable_and_provisioned(self):
        self.assertEqual(self.store._config.bank_id, self.config.bank_id)

    def test_02_retain_then_duplicate_retain_is_skipped(self):
        case = live_case("live probe duplicate handling", self.session)
        first = self.store.retain(case)
        second = self.store.retain(case)
        self.assertTrue(first.retained)
        self.assertFalse(second.retained)
        self.assertIn("already retained", second.reason)

    def test_03_recall_returns_provenance_for_retained_case(self):
        signature = f"live probe recall provenance {unique_suffix()}"
        self.store.retain(live_case(signature, self.session))
        result = self.store.recall(query=signature, max_tokens=2048)
        self.assertGreater(len(result.items), 0)
        for entry in result.items:
            self.assertTrue(entry.case_id)
            self.assertIsInstance(entry.score_final, float)
        self.assertIn(result.bank_id, (self.config.bank_id,))

    def test_04_irrelevant_query_classifies_as_irrelevant_and_abstains(self):
        signature = f"live probe irrelevant {unique_suffix()}"
        self.store.retain(live_case(signature, self.session))
        result = self.store.recall(
            query="quarterly marketing budget spreadsheet colour palette", max_tokens=2048
        )
        report, decision = classify_candidates(
            "quarterly marketing budget spreadsheet colour palette", result, POLICY
        )
        self.assertTrue(decision.abstained)
        self.assertEqual(report.candidates, [])

    def test_05_relevant_query_classifies_as_relevant(self):
        signature = f"live probe relevant {unique_suffix()}"
        self.store.retain(live_case(signature, self.session))
        result = self.store.recall(query=signature, max_tokens=2048)
        report, decision = classify_candidates(signature, result, POLICY)
        self.assertFalse(decision.abstained)
        self.assertGreaterEqual(len(report.candidates), 1)

    def test_06_blank_query_raises_before_network(self):
        from debugagent.memory.store import MemorySchemaError

        with self.assertRaises(MemorySchemaError):
            self.store.recall(query="  ")


class ConfigSmokeTests(unittest.TestCase):
    def test_config_never_contains_literal_secret_defaults(self):
        config = load_memory_config(data_dir="data")
        self.assertNotIn("sk-", config.hindsight_api_key)
        self.assertIn(config.min_final_score, (0.05,))


if __name__ == "__main__":
    unittest.main()
