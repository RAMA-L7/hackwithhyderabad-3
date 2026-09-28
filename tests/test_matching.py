"""Relevance classification, contradiction surfacing, provenance and abstention."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import support  # noqa: F401  (path wiring)
from debugagent.memory.matching import (
    AbstentionPolicy,
    classify_candidates,
    normalize_query,
    same_cause,
    tokenize,
)
from debugagent.schemas import RecallResult, RecallSet

NOW = datetime(2026, 3, 1, tzinfo=timezone.utc)
POLICY = AbstentionPolicy(min_final_score=0.05, weak_reference_floor=0.6, stale_after_days=365)


def item(
    case_id: str,
    score: float,
    *,
    service: str = "orders-api",
    root_cause_key: str = "proxy-limit",
    text: str = "connection reset above 2MB on orders-api",
    days_old: int = 1,
) -> RecallResult:
    return RecallResult(
        case_id=case_id,
        text=text,
        score_final=score,
        score_semantic=score,
        score_keyword=score,
        environment={"service": service, "runtime": "python3.10"},
        outcome="resolved",
        root_cause_key=root_cause_key,
        mentioned_at=(NOW - timedelta(days=days_old)).isoformat().replace("+00:00", "Z"),
    )


def recall_set(*items: RecallResult) -> RecallSet:
    return RecallSet(items=list(items), recalled_at=NOW.isoformat(), bank_id="test-bank")


class NormalizationTests(unittest.TestCase):
    def test_query_whitespace_collapsed(self):
        self.assertEqual(normalize_query("  reset   on  orders-api \n"), "reset on orders-api")

    def test_tokenizer_drops_separators(self):
        self.assertEqual(tokenize("orders-api/2MB, reset"), {"orders", "api", "2mb", "reset"})


class RelevanceTests(unittest.TestCase):
    def test_strong_score_is_relevant_and_not_abstained(self):
        report, decision = classify_candidates(
            "connection reset above 2MB", recall_set(item("seed-001", 1.02)), POLICY, NOW
        )
        self.assertFalse(decision.abstained)
        self.assertEqual(decision.relevance_class, "relevant")
        self.assertEqual([c.relevance_class for c in report.candidates], ["relevant"])

    def test_vague_score_is_partial(self):
        _, decision = classify_candidates("latency", recall_set(item("seed-001", 0.40)), POLICY, NOW)
        self.assertFalse(decision.abstained)
        self.assertEqual(decision.relevance_class, "partial")

    def test_unrelated_score_is_excluded_and_abstains(self):
        report, decision = classify_candidates(
            "frontend CSS not loading", recall_set(item("seed-001", 0.004)), POLICY, NOW
        )
        self.assertTrue(decision.abstained)
        self.assertEqual(report.candidates, [])
        self.assertEqual([c.relevance_class for c in report.excluded], ["irrelevant"])

    def test_empty_recall_set_abstains(self):
        report, decision = classify_candidates("anything", recall_set(), POLICY, NOW)
        self.assertTrue(decision.abstained)
        self.assertEqual(report.top_score, 0.0)
        self.assertIn("no candidates", decision.reason)

    def test_score_exactly_at_floor_is_included(self):
        report, decision = classify_candidates("orders-api", recall_set(item("s", 0.05)), POLICY, NOW)
        self.assertEqual(len(report.candidates), 1)
        self.assertFalse(decision.abstained)

    def test_stale_case_is_excluded(self):
        report, _ = classify_candidates(
            "orders-api", recall_set(item("old", 1.0, days_old=800)), POLICY, NOW
        )
        self.assertEqual(report.candidates, [])
        self.assertEqual([c.relevance_class for c in report.excluded], ["stale"])


class ContradictionTests(unittest.TestCase):
    def test_same_service_different_root_cause_is_contradictory(self):
        report, decision = classify_candidates(
            "orders-api 502 on large payloads",
            recall_set(
                item("seed-001", 1.02, root_cause_key="proxy-limit"),
                item("seed-002", 0.99, root_cause_key="upstream-502"),
            ),
            POLICY,
            NOW,
        )
        classes = {c.case_id: c.relevance_class for c in report.candidates}
        self.assertEqual(classes["seed-001"], "contradictory")
        self.assertEqual(classes["seed-002"], "contradictory")
        self.assertTrue(any("different root cause" in c.reason for c in report.candidates))
        self.assertTrue(decision.abstained)
        self.assertEqual(decision.relevance_class, "contradictory")

    def test_contradiction_with_one_strong_case_is_not_abstained(self):
        _, decision = classify_candidates(
            "orders-api reset above 2MB",
            recall_set(
                item("seed-001", 1.05, root_cause_key="proxy-limit"),
                item("seed-002", 0.55, root_cause_key="upstream-502"),
            ),
            POLICY,
            NOW,
        )
        self.assertFalse(decision.abstained)

    def test_different_services_are_not_contradictory(self):
        report, _ = classify_candidates(
            "connection reset on uploads",
            recall_set(
                item("seed-001", 1.02, service="orders-api", root_cause_key="proxy-limit"),
                item("seed-003", 0.99, service="media-uploader", root_cause_key="proxy-limit"),
            ),
            POLICY,
            NOW,
        )
        self.assertEqual([c.relevance_class for c in report.candidates], ["relevant", "relevant"])

    def test_agreeing_root_causes_are_corroboration_not_conflict(self):
        """Regression: found live on 2026-09-27. Many same-service cases that all agreed
        on the root cause were flagged contradictory, which abstained on a real match."""
        report, decision = classify_candidates(
            "connection reset on orders-api",
            recall_set(
                item("case-a", 0.80, root_cause_key="proxy-limit"),
                item("case-b", 0.79, root_cause_key="proxy-limit"),
                item("case-c", 0.78, root_cause_key="proxy-limit"),
            ),
            POLICY,
            NOW,
        )
        self.assertFalse(decision.abstained)
        self.assertEqual(
            [c.relevance_class for c in report.candidates], ["relevant", "relevant", "relevant"]
        )
        self.assertEqual([c.conflicts_with for c in report.candidates], [[], [], []])

    def test_unconfirmed_root_cause_never_contradicts(self):
        report, _ = classify_candidates(
            "connection reset on orders-api",
            recall_set(
                item("case-a", 0.80, root_cause_key="unconfirmed"),
                item("case-b", 0.79, root_cause_key="proxy-limit"),
            ),
            POLICY,
            NOW,
        )
        self.assertEqual([c.relevance_class for c in report.candidates], ["relevant", "relevant"])

    def test_conflict_is_scoped_to_the_disagreeing_case(self):
        report, _ = classify_candidates(
            "connection reset on orders-api",
            recall_set(
                item("case-a", 0.80, root_cause_key="proxy-limit"),
                item("case-b", 0.79, root_cause_key="proxy-limit"),
                item("case-c", 0.78, root_cause_key="upstream-502"),
            ),
            POLICY,
            NOW,
        )
        by_id = {c.case_id: c for c in report.candidates}
        self.assertEqual(
            by_id["case-a"].conflicts_with,
            ["same service 'orders-api' with different root cause", "case-c"],
        )
        self.assertEqual(
            by_id["case-c"].conflicts_with,
            ["same service 'orders-api' with different root cause", "case-a", "case-b"],
        )
        self.assertNotIn("case-b", by_id["case-a"].conflicts_with)


class ProvenanceTests(unittest.TestCase):
    def test_original_environment_is_preserved(self):
        report, _ = classify_candidates(
            "uploads reset", recall_set(item("seed-003", 0.98, service="media-uploader")), POLICY, NOW
        )
        candidate = report.candidates[0]
        self.assertEqual(candidate.environment["service"], "media-uploader")
        self.assertEqual(candidate.case_id, "seed-003")

    def test_provenance_lists_only_candidates_not_excluded(self):
        report, _ = classify_candidates(
            "orders-api",
            recall_set(item("seed-001", 1.0), item("noise", 0.001)),
            POLICY,
            NOW,
        )
        self.assertEqual(report.provenance(), ["seed-001"])

    def test_excluded_candidates_are_kept_in_trace(self):
        report, _ = classify_candidates(
            "orders-api", recall_set(item("noise", 0.001)), POLICY, NOW
        )
        self.assertEqual([c.case_id for c in report.excluded], ["noise"])


class FinalScoreCollapseTests(unittest.TestCase):
    """Pins the 2026-09-27 live finding: `final` is query-relative and collapses
    when the bank holds many near-identical cases, so an absolute floor on `final`
    alone silently drops real matches. `semantic` stays informative in that regime."""

    def collapsed(self, case_id: str, final: float, semantic: float) -> RecallResult:
        return RecallResult(
            case_id=case_id,
            text="synthetic probe trace",
            score_final=final,
            score_semantic=semantic,
            score_keyword=0.4,
            environment={"service": "probe-service", "runtime": "python3.10"},
            outcome="resolved",
            root_cause_key="probe-cause",
            mentioned_at=NOW.isoformat().replace("+00:00", "Z"),
        )

    def test_collapsed_final_with_high_semantic_is_still_usable(self):
        result = recall_set(self.collapsed("probe-a", 0.0034, 0.788))
        report, decision = classify_candidates("live probe", result, POLICY, NOW)
        self.assertFalse(decision.abstained)
        self.assertEqual(report.candidates[0].relevance_class, "relevant")
        self.assertIn("semantic_fallback", report.candidates[0].reason)

    def test_collapsed_final_with_low_semantic_stays_irrelevant(self):
        result = recall_set(self.collapsed("probe-a", 0.003, 0.52))
        report, decision = classify_candidates("quarterly budget", result, POLICY, NOW)
        self.assertTrue(decision.abstained)
        self.assertEqual(report.candidates, [])
        self.assertIn("semantic floor", report.excluded[0].reason)

    def test_informative_final_is_preferred_over_semantic(self):
        result = recall_set(self.collapsed("probe-a", 0.95, 0.30))
        report, _ = classify_candidates("live probe", result, POLICY, NOW)
        self.assertTrue(report.candidates[0].reason.startswith("final score"))

    def test_top_score_reports_effective_signal(self):
        result = recall_set(self.collapsed("probe-a", 0.0034, 0.788))
        report, decision = classify_candidates("live probe", result, POLICY, NOW)
        self.assertAlmostEqual(decision.top_score, 0.788, places=3)
        self.assertAlmostEqual(report.top_score, 0.788, places=3)

    def test_missing_semantic_does_not_rescue_irrelevant_case(self):
        broken = RecallResult(
            case_id="probe-a",
            text="synthetic probe trace",
            score_final=0.002,
            score_semantic=None,
            score_keyword=0.0,
            environment={"service": "probe-service"},
            outcome="resolved",
        )
        _, decision = classify_candidates("live probe", recall_set(broken), POLICY, NOW)
        self.assertTrue(decision.abstained)


    def test_conflict_about_another_service_is_not_reported(self):
        """Regression: found live on 2026-09-28. A media-uploader question still recalled two
        orders-api cases and reported them as contradicting each other. A disagreement about a
        service you are not debugging is not actionable."""
        report, _ = classify_candidates(
            "media-uploader uploads over 2MB fail with connection reset",
            recall_set(
                item("mu-1", 0.95, service="media-uploader", root_cause_key="proxy-limit"),
                item("oa-1", 0.30, service="orders-api", root_cause_key="proxy-limit",
                     text="orders-api 2MB body limit"),
                item("oa-2", 0.28, service="orders-api", root_cause_key="upstream-502",
                     text="orders-api 502 on large body"),
            ),
            POLICY,
            NOW,
        )
        by_id = {c.case_id: c for c in report.candidates + report.excluded}
        self.assertEqual(by_id["oa-1"].conflicts_with, [])
        self.assertEqual(by_id["oa-2"].conflicts_with, [])
        self.assertNotEqual(by_id["mu-1"].relevance_class, "contradictory")

    def test_conflict_is_still_reported_for_the_service_being_debugged(self):
        report, _ = classify_candidates(
            "orders-api uploads over 2MB fail with connection reset",
            recall_set(
                item("oa-1", 0.95, service="orders-api", root_cause_key="proxy-limit"),
                item("oa-2", 0.28, service="orders-api", root_cause_key="upstream-502"),
                item("mu-1", 0.30, service="media-uploader", root_cause_key="proxy-limit"),
            ),
            POLICY,
            NOW,
        )
        by_id = {c.case_id: c for c in report.candidates + report.excluded}
        self.assertIn("oa-2", by_id["oa-1"].conflicts_with)

    def test_query_naming_no_service_keeps_the_unrestricted_behaviour(self):
        report, _ = classify_candidates(
            "large payloads fail on the proxy",
            recall_set(
                item("oa-1", 0.95, service="orders-api", root_cause_key="proxy-limit"),
                item("oa-2", 0.28, service="orders-api", root_cause_key="upstream-502"),
            ),
            POLICY,
            NOW,
        )
        by_id = {c.case_id: c for c in report.candidates + report.excluded}
        self.assertIn("oa-2", by_id["oa-1"].conflicts_with)


    def test_paraphrased_root_causes_are_not_a_contradiction(self):
        """Regression: found live on 2026-09-28. Re-investigating an already-resolved issue
        stored a paraphrase of the seed; the two were reported as contradicting each other and the
        layer abstained on a correct match."""
        report, decision = classify_candidates(
            "media-uploader uploads over 2MB fail with a connection reset",
            recall_set(
                item("seed-003", 0.95, service="media-uploader",
                     root_cause_key="reverse proxy request body limit rejected the upload",
                     text="media-uploader uploads over 2MB fail with connection reset"),
                item("new-001", 0.90, service="media-uploader",
                     root_cause_key="reverse proxy request-body limit rejected uploads above the limit",
                     text="media-uploader resets large uploads"),
            ),
            POLICY,
            NOW,
        )
        self.assertFalse(decision.abstained)
        for candidate in report.candidates:
            self.assertEqual(candidate.conflicts_with, [])
            self.assertNotEqual(candidate.relevance_class, "contradictory")

    def test_genuinely_different_root_causes_still_conflict(self):
        report, _ = classify_candidates(
            "orders-api returns 502 for payloads above 2MB",
            recall_set(
                item("seed-001", 0.95, service="orders-api",
                     root_cause_key="upstream proxy request-size limit truncated the body",
                     text="orders-api 502 on large payloads"),
                item("seed-002", 0.90, service="orders-api",
                     root_cause_key="upstream dependency returned 502 for oversized bodies",
                     text="orders-api 502 on large payloads"),
            ),
            POLICY,
            NOW,
        )
        by_id = {c.case_id: c for c in report.candidates}
        self.assertIn("seed-002", by_id["seed-001"].conflicts_with)

    def test_same_cause_helper(self):
        self.assertTrue(same_cause("proxy body limit hit", "reverse proxy request body limit was hit", 0.5))
        self.assertFalse(same_cause("proxy body limit hit", "database connection pool exhausted", 0.5))
        self.assertFalse(same_cause("", "anything", 0.5))


class MemoryIsNotEvidenceTests(unittest.TestCase):
    def test_match_report_carries_no_evidence_fields(self):
        report, _ = classify_candidates("orders-api", recall_set(item("seed-001", 1.0)), POLICY, NOW)
        for candidate in report.candidates:
            payload = candidate.to_dict()
            self.assertNotIn("evidence", payload)
            self.assertNotIn("verdict", payload)
            self.assertNotIn("decision", payload)


if __name__ == "__main__":
    unittest.main()
