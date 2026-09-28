"""Semantic-fallback floor (2026-09-28): configurable, default 0.75, pinned with the scores that motivated it."""

from __future__ import annotations

import os
import unittest

import support  # noqa: F401  (path wiring)
from debugagent.config import DEFAULT_SEMANTIC_FLOOR, load_memory_config
from debugagent.memory.matching import AbstentionPolicy, classify_candidates
from debugagent.pipeline.memory_adapter import policy_from_config
from debugagent.schemas import RecallResult, RecallSet


def collapsed(case_id: str, service: str, semantic: float) -> RecallSet:
    """A recall row whose `final` collapsed below its floor, so only the semantic fallback can admit it."""
    item = RecallResult(case_id=case_id, text=f"{service} past case", score_final=0.003, score_semantic=semantic,
                        environment={"service": service, "runtime": "python3.10"}, outcome="resolved",
                        root_cause_key="some cause")
    return RecallSet(items=[item], recalled_at="2026-09-28T18:00:00+00:00", bank_id="test")


class SemanticFloorTests(unittest.TestCase):
    def test_default_is_the_evidence_based_value(self):
        self.assertEqual(DEFAULT_SEMANTIC_FLOOR, 0.75)
        self.assertEqual(AbstentionPolicy().semantic_floor, 0.75)

    def test_unrelated_cases_from_the_rehearsal_are_rejected(self):
        # fresh-bank rehearsal: batch-runner for a billing issue (0.7084), media-uploader for orders-api (0.7016)
        for case_id, service, semantic in (("batch", "batch-runner", 0.7084), ("upload", "media-uploader", 0.7016)):
            with self.subTest(service=service):
                report, decision = classify_candidates("billing-service duplicate invoices", collapsed(case_id, service, semantic),
                                                       AbstentionPolicy())
                self.assertTrue(decision.abstained)
                self.assertEqual(report.candidates, [])

    def test_genuine_collapsed_match_is_still_admitted(self):  # M1: final 0.003 while semantic held 0.78
        _, decision = classify_candidates("orders-api resets on large payloads", collapsed("real", "orders-api", 0.78),
                                          AbstentionPolicy())
        self.assertFalse(decision.abstained)

    def test_floor_is_configurable_from_the_environment(self):
        saved = os.environ.get("DEBUGAGENT_SEMANTIC_FLOOR")
        try:
            os.environ.pop("DEBUGAGENT_SEMANTIC_FLOOR", None)
            self.assertEqual(policy_from_config(load_memory_config()).semantic_floor, 0.75)
            os.environ["DEBUGAGENT_SEMANTIC_FLOOR"] = "0.8"
            self.assertEqual(policy_from_config(load_memory_config()).semantic_floor, 0.8)
        finally:
            if saved is None:
                os.environ.pop("DEBUGAGENT_SEMANTIC_FLOOR", None)
            else:
                os.environ["DEBUGAGENT_SEMANTIC_FLOOR"] = saved


if __name__ == "__main__":
    unittest.main()
