"""Seed loading: determinism, duplicate handling, and reporting."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support
from debugagent.memory.store import MemoryStore
from debugagent.schemas import MemoryCase, RetentionDecision
from debugagent.seeds.loader import load_seed_cases, load_seed_file


class RecordingStore:
    def __init__(self):
        self.calls: list[MemoryCase] = []

    def retain(self, case: MemoryCase) -> RetentionDecision:
        self.calls.append(case)
        if len(self.calls) > 1:
            return RetentionDecision(
                retained=False,
                reason="skipped: case already retained (idempotency ledger)",
                memory_case_id="mem-existing",
            )
        return RetentionDecision(
            retained=True, reason="retained", memory_case_id="mem-1", case_key="k"
        )

    def recall(self, **_kwargs):  # pragma: no cover - loader does not recall
        raise AssertionError("loader must not recall")

    def update(self, *_a, **_k):  # pragma: no cover
        raise AssertionError("loader must not update")

    def invalidate(self, *_a, **_k):  # pragma: no cover
        raise AssertionError("loader must not invalidate")


class SeedFileTests(unittest.TestCase):
    def test_seed_file_loads(self):
        cases = load_seed_file()
        self.assertGreaterEqual(len(cases), 4)
        self.assertTrue(all(isinstance(c, MemoryCase) for c in cases))

    def test_every_seed_case_validates_against_schema(self):
        for case in load_seed_file():
            MemoryCase.from_dict(case.to_dict())

    def test_seed_signatures_are_distinct(self):
        signatures = [c.problem_signature for c in load_seed_file()]
        self.assertEqual(len(signatures), len(set(signatures)))

    def test_seed_services_are_diverse(self):
        services = {c.environment.get("service") for c in load_seed_file()}
        self.assertGreaterEqual(len(services), 3)

    def test_every_seed_has_a_failed_approach_or_unconfirmed_root_cause(self):
        for case in load_seed_file():
            self.assertTrue(
                case.failed_approaches or not case.root_cause,
                f"{case.problem_signature} has neither a failed approach nor an unconfirmed root cause",
            )

    def test_seeds_are_marked_synthetic_and_carry_no_secrets(self):
        import json
        from debugagent.seeds.loader import DEFAULT_SEED_FILE

        payload = json.loads(DEFAULT_SEED_FILE.read_text(encoding="utf-8"))
        self.assertTrue(payload.get("synthetic"))
        text = DEFAULT_SEED_FILE.read_text(encoding="utf-8").lower()
        for forbidden in ("api_key", "password", "secret", "bearer", "token"):
            self.assertNotIn(forbidden, text)


class SeedLoaderTests(unittest.TestCase):
    def test_loader_reports_inserted_and_skipped(self):
        store = RecordingStore()
        report = load_seed_cases(store)
        self.assertEqual(len(report.inserted), 1)
        self.assertGreaterEqual(len(report.skipped), 3)
        self.assertEqual(report.rejected, [])
        self.assertEqual(report.to_dict()["inserted"], report.inserted)

    def test_loader_is_deterministic(self):
        first = [c.problem_signature for c in load_seed_file()]
        second = [c.problem_signature for c in load_seed_file()]
        self.assertEqual(first, second)

    def test_loader_never_overwrites(self):
        store = RecordingStore()
        load_seed_cases(store)
        for call in store.calls:
            self.assertTrue(call.case_id)

    def test_loader_rejects_invalid_seed_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(
                '{"cases": [{"problem_signature": "", "outcome": "nope"}]}', encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                load_seed_file(path)


if __name__ == "__main__":
    unittest.main()
