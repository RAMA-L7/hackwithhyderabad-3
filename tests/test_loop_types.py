"""T1 for Mukul-side types + the memory-port boundary: valid instances round-trip, invalid ones fail closed."""

from __future__ import annotations

import unittest

import loop_support  # noqa: F401  (path wiring)
from loop_support import ABSTAINED_VIEW, CONTRADICTORY_VIEW, PARTIAL_VIEW, RELEVANT_VIEW, FakeMemoryPort
from debugagent.pipeline.memory_port import MemoryFailure, check_retention, check_view
from debugagent.pipeline.types import (
    DebugInput,
    Evidence,
    Hypothesis,
    NormalizedDebugCase,
    Resolution,
    SchemaError,
    VerificationResult,
)

NOW = "2026-09-28T06:00:00+00:00"

VALID = {
    DebugInput: {"description": "media-uploader resets uploads over 2 MB",
                 "measurements": ["20/20 uploads of 2.5MB fail"], "environment_hints": {"service": "media-uploader"}},
    NormalizedDebugCase: {"problem_signature": "media-uploader resets uploads over 2 MB",
                          "symptoms": ["20/20 uploads of 2.5MB fail"],
                          "environment": {"service": "media-uploader", "proxy": None},
                          "raw_description": "media-uploader resets uploads over 2 MB", "source_case_ids": []},
    Evidence: {"items": [{"name": "service", "value": "media-uploader", "source": "engineer",
                          "captured_at": NOW, "known": True},
                         {"name": "proxy", "value": None, "source": "engineer", "captured_at": NOW, "known": False}],
               "unknown_fields": ["proxy"]},
    Hypothesis: {"ref": "H1", "hypothesis": "reverse-proxy body limit rejects large uploads",
                 "supporting_case_ids": ["3f9a1c07b2e4d815"], "relevance_state": "supported",
                 "refutation_conditions": ["resets persist after raising the limit"],
                 "recommended_next_step": "check client_max_body_size"},
    VerificationResult: {"hypothesis_ref": "H1", "status": "insufficient_evidence", "engineer_decision": "accept",
                         "relevance_confirmed": True, "mismatched_environment_fields": ["proxy"],
                         "engineer_note": "proxy version unknown"},
    Resolution: {"action_taken": "raised client_max_body_size to 10m", "observed_result": "no resets above 2 MB",
                 "root_cause_confirmed": "reverse proxy body limit", "outcome": "resolved",
                 "failed_approaches_this_session": [{"approach": "raised client timeout",
                                                     "why_failed": "proxy closed first"}],
                 "evidence_refs": ["log://media-uploader/2026-09-28/nginx-error.log"]},
}


def rejects(test: unittest.TestCase, cls, payload, fragment: str):
    with test.assertRaises(SchemaError) as ctx:
        cls.from_dict(payload)
    test.assertTrue(any(fragment in e for e in ctx.exception.errors), ctx.exception.errors)


class RoundTripTests(unittest.TestCase):
    def test_every_valid_instance_round_trips(self):
        for cls, payload in VALID.items():
            with self.subTest(cls=cls.__name__):
                obj = cls.from_dict(payload)
                self.assertEqual(cls.from_dict(obj.to_dict()).to_dict(), obj.to_dict())

    def test_non_object_rejected(self):
        for cls in VALID:
            with self.subTest(cls=cls.__name__):
                rejects(self, cls, ["not", "an", "object"], "expected object")


class InvalidInstanceTests(unittest.TestCase):
    def test_blank_description(self):
        rejects(self, DebugInput, {"description": "   "}, "description: must not be empty")

    def test_normalized_env_value_must_be_string_or_null(self):
        rejects(self, NormalizedDebugCase, dict(VALID[NormalizedDebugCase], environment={"service": 3}),
                "environment")

    def test_evidence_known_without_value(self):
        bad = {"items": [{"name": "service", "value": None, "source": "engineer", "captured_at": NOW, "known": True}]}
        rejects(self, Evidence, bad, "known item must carry a value")

    def test_evidence_unknown_with_value(self):
        bad = {"items": [{"name": "service", "value": "x", "source": "engineer", "captured_at": NOW, "known": False}]}
        rejects(self, Evidence, bad, "unknown item must have value null")

    def test_generic_hypothesis_cannot_cite(self):
        rejects(self, Hypothesis, dict(VALID[Hypothesis], relevance_state="generic"), "generic hypothesis")

    def test_memory_backed_hypothesis_needs_citation(self):
        rejects(self, Hypothesis, dict(VALID[Hypothesis], supporting_case_ids=[]), "requires at least one")

    def test_generic_without_citations_is_valid(self):
        h = Hypothesis.from_dict(dict(VALID[Hypothesis], relevance_state="generic", supporting_case_ids=[]))
        self.assertEqual(h.relevance_state, "generic")

    def test_hypothesis_ref_format(self):
        rejects(self, Hypothesis, dict(VALID[Hypothesis], ref="hyp-1"), "must look like H1")

    def test_verification_requires_engineer_decision(self):  # T4 at the type level
        payload = {k: v for k, v in VALID[VerificationResult].items() if k != "engineer_decision"}
        rejects(self, VerificationResult, dict(payload, status="supported"), "engineer_decision: required")

    def test_verification_rejects_unknown_decision(self):
        rejects(self, VerificationResult, dict(VALID[VerificationResult], engineer_decision="auto"), "not one of")

    def test_resolution_requires_outcome_and_root_cause_key(self):
        payload = {k: v for k, v in VALID[Resolution].items() if k not in ("outcome", "root_cause_confirmed")}
        with self.assertRaises(SchemaError) as ctx:
            Resolution.from_dict(payload)
        joined = " ".join(ctx.exception.errors)
        self.assertIn("outcome: required", joined)
        self.assertIn("root_cause_confirmed: required", joined)

    def test_resolution_null_root_cause_allowed(self):
        self.assertIsNone(Resolution.from_dict(dict(VALID[Resolution], root_cause_confirmed=None)).root_cause_confirmed)

    def test_all_errors_reported_at_once(self):
        with self.assertRaises(SchemaError) as ctx:
            Hypothesis.from_dict({"ref": "x"})
        self.assertGreaterEqual(len(ctx.exception.errors), 5)


class MemoryPortBoundaryTests(unittest.TestCase):
    def test_sample_views_are_valid(self):
        for v in (RELEVANT_VIEW, PARTIAL_VIEW, CONTRADICTORY_VIEW, ABSTAINED_VIEW):
            self.assertIs(check_view(v), v)

    def test_malformed_view_is_schema_failure_not_no_memory(self):
        bad = dict(RELEVANT_VIEW, report={"candidates": [{"case_id": ""}], "excluded": [],
                                          "top_score": 1, "threshold_used": 0})
        with self.assertRaises(MemoryFailure) as ctx:
            check_view(bad)
        self.assertEqual(ctx.exception.kind, "schema")

    def test_missing_abstention_rejected(self):
        with self.assertRaises(MemoryFailure):
            check_view({k: v for k, v in RELEVANT_VIEW.items() if k != "abstention"})

    def test_retention_decision_checked(self):
        self.assertTrue(check_retention(FakeMemoryPort().retain({"x": 1}))["retained"])
        with self.assertRaises(MemoryFailure):
            check_retention({"retained": "yes", "reason": "", "validated": True})

    def test_fake_port_failure_propagates(self):
        port = FakeMemoryPort(fail=MemoryFailure("unavailable", "bank unreachable"))
        with self.assertRaises(MemoryFailure) as ctx:
            port.recall_and_classify("q")
        self.assertEqual(ctx.exception.kind, "unavailable")

    def test_unknown_failure_kind_rejected(self):
        with self.assertRaises(ValueError):
            MemoryFailure("timeout", "x")


if __name__ == "__main__":
    unittest.main()
