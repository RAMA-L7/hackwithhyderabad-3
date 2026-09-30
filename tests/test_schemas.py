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

    # --- session identity must survive a to_dict()/from_dict() round trip ---------
    # compute_case_key() is sha256(problem_signature|session_id)[:16], so a round trip
    # that drops session_id yields a DIFFERENT key for the same case. These are the
    # P3-2 Phase A regression tests for that loss.
    def test_to_dict_includes_identity_fields(self):
        case = MemoryCase.from_dict(dict(VALID, case_id="seed-001", session_id="seed-v1"))
        payload = case.to_dict()
        self.assertEqual(payload["case_id"], "seed-001")
        self.assertEqual(payload["session_id"], "seed-v1")

    def test_session_id_survives_round_trip(self):
        case = MemoryCase.from_dict(dict(VALID, session_id="run-A"))
        again = MemoryCase.from_dict(case.to_dict())
        self.assertEqual(again.session_id, "run-A")
        self.assertEqual(again.case_id, case.case_id)

    def test_case_id_survives_round_trip(self):
        case = MemoryCase.from_dict(dict(VALID, case_id="case-7"))
        again = MemoryCase.from_dict(case.to_dict())
        self.assertEqual(again.case_id, "case-7")

    def test_absent_identity_round_trips_as_none(self):
        case = MemoryCase.from_dict(VALID)
        self.assertIsNone(case.session_id)
        again = MemoryCase.from_dict(case.to_dict())
        self.assertIsNone(again.session_id)
        self.assertIsNone(again.case_id)

    def test_round_trip_does_not_change_case_key(self):
        from debugagent.memory.hindsight_store import compute_case_key

        case = MemoryCase.from_dict(dict(VALID, session_id="run-A"))
        before = compute_case_key(case, case.session_id or "seed")
        after = compute_case_key(MemoryCase.from_dict(case.to_dict()), "run-A")
        self.assertEqual(before, after)

    def test_case_key_would_differ_if_session_identity_were_lost(self):
        """Pins why the round trip matters: dropping session_id changes the key."""
        from debugagent.memory.hindsight_store import compute_case_key

        case = MemoryCase.from_dict(dict(VALID, session_id="run-A"))
        self.assertNotEqual(compute_case_key(case, "run-A"), compute_case_key(case, "seed"))

    # --- identity specificity contract (P3-2 Phase A) ---------------------------
    # case_key = sha256(problem_signature|session_id|outcome_digest)[:16], where
    # outcome_digest = sha256(root_cause|resolution)[:16]. The first two components keep their
    # established meaning ("a new session is a legitimately new case", change-log Stage 20); the
    # outcome digest was added so contradictory outcomes are distinct rather than deduplicated away.
    def test_new_session_is_a_new_case(self):
        """Documented contract: idempotency is keyed on signature AND session."""
        from debugagent.memory.hindsight_store import compute_case_key

        case = MemoryCase.from_dict(VALID)
        self.assertNotEqual(compute_case_key(case, "s1"), compute_case_key(case, "s2"))

    def test_different_problem_is_a_different_case(self):
        from debugagent.memory.hindsight_store import compute_case_key

        a = MemoryCase.from_dict(dict(VALID, problem_signature="orders api returns 502"))
        b = MemoryCase.from_dict(dict(VALID, problem_signature="payments worker oom"))
        self.assertNotEqual(compute_case_key(a, "s1"), compute_case_key(b, "s1"))

    def test_outcome_digest_is_part_of_case_identity(self):
        """Contradictory outcomes for one problem in one session are DISTINCT cases.

        Under the previous two-component formula these collided, so the second was silently
        dropped as a duplicate. Pinned here so that boundary cannot regress."""
        from debugagent.memory.hindsight_store import compute_case_key

        a = MemoryCase.from_dict(dict(VALID, root_cause="proxy limit", resolution="raise limit"))
        b = MemoryCase.from_dict(dict(VALID, root_cause="client bug", resolution="upgrade lib"))
        self.assertNotEqual(compute_case_key(a, "s1"), compute_case_key(b, "s1"))

    def test_different_resolution_is_a_different_case(self):
        from debugagent.memory.hindsight_store import compute_case_key

        a = MemoryCase.from_dict(dict(VALID, root_cause="same", resolution="fix one"))
        b = MemoryCase.from_dict(dict(VALID, root_cause="same", resolution="fix two"))
        self.assertNotEqual(compute_case_key(a, "s1"), compute_case_key(b, "s1"))

    def test_identical_outcome_stays_the_same_case(self):
        """The outcome digest must not over-discriminate: same outcome, same key."""
        from debugagent.memory.hindsight_store import compute_case_key

        a = MemoryCase.from_dict(dict(VALID, root_cause="rc", resolution="res"))
        b = MemoryCase.from_dict(dict(VALID, root_cause="rc", resolution="res",
                                       symptoms=["different symptom text"]))
        self.assertEqual(compute_case_key(a, "s1"), compute_case_key(b, "s1"))

    def test_demonstrated_failure_regression_contradictory_outcomes(self):
        """The exact case pair from the Phase A audit: these MUST NOT collide."""
        from debugagent.memory.hindsight_store import compute_case_key

        case_a = MemoryCase.from_dict(dict(VALID, problem_signature="orders api 502",
                                          session_id="run-1", root_cause="proxy limit",
                                          resolution="raise body size"))
        case_b = MemoryCase.from_dict(dict(VALID, problem_signature="orders api 502",
                                          session_id="run-1", root_cause="client bug",
                                          resolution="upgrade client"))
        self.assertNotEqual(compute_case_key(case_a, "run-1"), compute_case_key(case_b, "run-1"))

    def test_outcome_digest_is_deterministic(self):
        from debugagent.memory.hindsight_store import compute_outcome_digest

        case = MemoryCase.from_dict(dict(VALID, root_cause="rc", resolution="res"))
        self.assertEqual(compute_outcome_digest(case), compute_outcome_digest(case))
        self.assertEqual(len(compute_outcome_digest(case)), 16)

    def test_outcome_digest_handles_none_fields(self):
        """A case with no recorded outcome must hash, not raise."""
        from debugagent.memory.hindsight_store import compute_case_key, compute_outcome_digest

        case = MemoryCase.from_dict(dict(VALID, root_cause=None, resolution=None))
        self.assertEqual(len(compute_outcome_digest(case)), 16)
        self.assertEqual(len(compute_case_key(case, "s1")), 16)

    def test_case_key_is_deterministic_across_repeated_calls(self):
        from debugagent.memory.hindsight_store import compute_case_key

        case = MemoryCase.from_dict(dict(VALID, root_cause="rc", resolution="res"))
        keys = {compute_case_key(case, "s1") for _ in range(5)}
        self.assertEqual(len(keys), 1)

    def test_session_less_cases_share_the_seed_fallback_identity(self):
        """store.retain() substitutes the literal 'seed' when session_id is None, so two
        session-less cases with the same signature are one case. Observable consequence."""
        from debugagent.memory.hindsight_store import compute_case_key

        a = MemoryCase.from_dict(VALID)
        b = MemoryCase.from_dict(VALID)
        self.assertIsNone(a.session_id)
        self.assertEqual(compute_case_key(a, a.session_id or "seed"),
                         compute_case_key(b, b.session_id or "seed"))

    def test_retained_case_key_uses_session_identity_end_to_end(self):
        """The production retention path keys on the real session, not the 'seed' fallback."""
        import tempfile
        from pathlib import Path

        from debugagent.memory.hindsight_store import HindsightMemoryStore, compute_case_key
        from debugagent.pipeline.memory_adapter import HindsightMemoryPort
        from support import FakeHindsightClient, memory_config

        store = HindsightMemoryStore(
            memory_config(Path(tempfile.mkdtemp())), client=FakeHindsightClient()
        )
        decision = HindsightMemoryPort(store).retain(dict(VALID, session_id="run-A"))
        expected = compute_case_key(MemoryCase.from_dict(dict(VALID, session_id="run-A")), "run-A")
        self.assertEqual(decision["case_key"], expected)


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
