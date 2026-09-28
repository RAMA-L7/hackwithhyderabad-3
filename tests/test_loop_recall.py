"""MK2: recall through the port, citable set, environment comparison, MEMORY rendering."""

from __future__ import annotations

import unittest

import loop_support  # noqa: F401  (path wiring)
from loop_support import ABSTAINED_VIEW, CONTRADICTORY_VIEW, PARTIAL_VIEW, RELEVANT_VIEW, FakeMemoryPort
from debugagent.pipeline.memory_port import MemoryFailure
from debugagent.pipeline.normalize import normalize
from debugagent.pipeline.recall_match import compare_environment, recall, recall_query
from debugagent.pipeline.render import render_memory
from debugagent.pipeline.types import DebugInput

CASE = normalize(DebugInput("photo-api resets uploads over 2 MB\nservice=photo-api proxy=nginx-1.24\nsmall uploads succeed"))


class RecallTests(unittest.TestCase):
    def test_query_is_signature_plus_symptoms(self):
        port = FakeMemoryPort()
        recall(port, CASE)
        self.assertEqual(port.queries, ["photo-api resets uploads over 2 MB; small uploads succeed"])
        self.assertEqual(recall_query(CASE), port.queries[0])

    def test_relevant_candidates_are_citable_excluded_are_not(self):
        memory = recall(FakeMemoryPort(RELEVANT_VIEW), CASE)
        self.assertEqual(list(memory.citable()), ["3f9a1c07b2e4d815"])
        self.assertEqual([c["case_id"] for c in memory.excluded], ["a7c2e9f04b1d6e38"])

    def test_partial_is_citable_not_abstention(self):
        memory = recall(FakeMemoryPort(PARTIAL_VIEW), CASE)
        self.assertFalse(memory.abstained)
        self.assertEqual(list(memory.citable()), ["seed-001"])

    def test_abstained_contradictory_shows_candidates_but_cites_nothing(self):
        memory = recall(FakeMemoryPort(CONTRADICTORY_VIEW), CASE)
        self.assertEqual(len(memory.candidates), 2)
        self.assertEqual(memory.citable(), {})

    def test_memory_failure_propagates(self):
        with self.assertRaises(MemoryFailure) as ctx:
            recall(FakeMemoryPort(fail=MemoryFailure("auth", "bad key")), CASE)
        self.assertEqual(ctx.exception.kind, "auth")

    def test_malformed_view_is_schema_failure(self):
        with self.assertRaises(MemoryFailure) as ctx:
            recall(FakeMemoryPort({"bank_id": "b"}), CASE)
        self.assertEqual(ctx.exception.kind, "schema")

    def test_compare_environment(self):
        diffs = compare_environment({"service": "media-uploader", "proxy": "nginx-1.25", "region": "eu"},
                                    {"service": "photo-api", "proxy": "nginx-1.25", "region": None})
        self.assertEqual(diffs, [("region", "eu", None), ("service", "media-uploader", "photo-api")])


class RenderMemoryTests(unittest.TestCase):
    def test_relevant_rendering(self):
        text = render_memory(recall(FakeMemoryPort(RELEVANT_VIEW), CASE), CASE.environment)
        self.assertIn("MEMORY", text)
        self.assertIn("[relevant] case 3f9a1c07b2e4d815", text)
        self.assertIn("original environment: service=media-uploader, proxy=nginx-1.25", text)
        self.assertIn("service (then media-uploader, now photo-api)", text)
        self.assertIn("proxy (then nginx-1.25, now nginx-1.24)", text)
        self.assertIn("not evidence", text)
        self.assertIn("a7c2e9f04b1d6e38 [irrelevant]", text)
        self.assertNotIn("0.9", text, "scores are never shown")

    def test_partial_labelled_weak(self):
        self.assertIn("[partial · weak reference]", render_memory(recall(FakeMemoryPort(PARTIAL_VIEW), CASE), {}))

    def test_contradictory_shows_both_sides_and_conflict(self):
        text = render_memory(recall(FakeMemoryPort(CONTRADICTORY_VIEW), CASE), {})
        self.assertIn("seed-001", text)
        self.assertIn("seed-002", text)
        self.assertIn("conflicts with", text)
        self.assertIn("The proposal will be generic", text)

    def test_abstained_shows_reason(self):
        text = render_memory(recall(FakeMemoryPort(ABSTAINED_VIEW), CASE), {})
        self.assertIn("No usable memory: memory returned no candidates", text)


if __name__ == "__main__":
    unittest.main()
