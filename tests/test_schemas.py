"""Schema validation: valid cases accepted, malformed cases rejected (fail closed)."""

from __future__ import annotations

import unittest

import support  # noqa: F401  (path wiring)
from debugagent.schemas import MemoryCase, SchemaError

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


class ValidCaseTests(unittest.TestCase):
    def test_valid_case_parses(self):
        case = MemoryCase.from_dict(VALID)
        self.assertEqual(case.outcome, "resolved")
        self.assertEqual(len(case.failed_approaches), 1)
        self.assertEqual(case.environment["service"], "orders-api")

    def test_round_trip_is_stable(self):
        case = MemoryCase.from_dict(VALID)
        again = MemoryCase.from_dict(case.to_dict())
        self.assertEqual(case.to_dict(), again.to_dict())

    def test_null_root_cause_is_allowed(self):
        payload = dict(VALID, root_cause=None, resolution="workaround only")
        case = MemoryCase.from_dict(payload)
        self.assertIsNone(case.root_cause)

    def test_empty_failed_approaches_is_allowed_but_key_required(self):
        payload = dict(VALID, failed_approaches=[])
        self.assertEqual(len(MemoryCase.from_dict(payload).failed_approaches), 0)

    def test_optional_ids_are_preserved(self):
        case = MemoryCase.from_dict(dict(VALID, case_id="seed-001", session_id="seed-v1"))
        self.assertEqual(case.case_id, "seed-001")
        self.assertEqual(case.session_id, "seed-v1")


class InvalidCaseTests(unittest.TestCase):
    def assert_rejected(self, payload, expected_fragment: str):
        with self.assertRaises(SchemaError) as ctx:
            MemoryCase.from_dict(payload)
        joined = " | ".join(ctx.exception.errors)
        self.assertIn(expected_fragment, joined)

    def test_missing_signature_rejected(self):
        payload = {k: v for k, v in VALID.items() if k != "problem_signature"}
        self.assert_rejected(payload, "required field missing")

    def test_missing_verification_notes_rejected(self):
        payload = {k: v for k, v in VALID.items() if k != "verification_notes"}
        self.assert_rejected(payload, "required field missing")

    def test_bad_outcome_enum_rejected(self):
        self.assert_rejected(dict(VALID, outcome="fixed"), "is not one of")

    def test_blank_signature_rejected(self):
        self.assert_rejected(dict(VALID, problem_signature="   "), "must not be empty")

    def test_wrong_type_rejected(self):
        self.assert_rejected(dict(VALID, symptoms="not a list"), "expected array")

    def test_environment_wrong_type_rejected(self):
        self.assert_rejected(dict(VALID, environment=["service"]), "expected object")

    def test_failed_approach_missing_field_rejected(self):
        self.assert_rejected(
            dict(VALID, failed_approaches=[{"approach": "x"}]), "why_failed"
        )

    def test_root_cause_wrong_type_rejected(self):
        self.assert_rejected(dict(VALID, root_cause=42), "expected string or null")

    def test_multiple_errors_reported_together(self):
        payload = dict(VALID, outcome="nope", problem_signature="", symptoms="x")
        with self.assertRaises(SchemaError) as ctx:
            MemoryCase.from_dict(payload)
        self.assertGreaterEqual(len(ctx.exception.errors), 3)


if __name__ == "__main__":
    unittest.main()
