"""MK6: Evidence from engineer input only; T4 no auto-decide; memory never fills an evidence gap; resolution."""

from __future__ import annotations

import inspect
import unittest

import loop_support  # noqa: F401  (path wiring)
from loop_support import FakeLLM, FakeMemoryPort, candidate, hyp, view
from debugagent.pipeline.evidence import EvidenceError, add_facts, build_evidence
from debugagent.pipeline.hypothesize import generate_hypotheses
from debugagent.pipeline.normalize import normalize
from debugagent.pipeline.recall_match import recall
from debugagent.pipeline.types import DebugInput
from debugagent.pipeline.verify import EngineerDecision, VerificationError, build_resolution, evidence_gaps, verify

CASE = normalize(DebugInput("photo-api resets uploads over 2 MB\nservice=photo-api proxy=nginx-1.24\nsmall uploads succeed"))
REL = "3f9a1c07b2e4d815"
PAST_ENV = {"service": "media-uploader", "proxy": "nginx-1.25", "runtime": "node20"}
MEMORY = recall(FakeMemoryPort(view([candidate(REL, environment=PAST_ENV)])), CASE)
PROPOSAL = generate_hypotheses(CASE, MEMORY, FakeLLM({"hypotheses": [hyp(cites=[REL]), hyp(text="app-side limit")]}))
H1, H2 = PROPOSAL.hypotheses
ACCEPT = EngineerDecision("accept", "supported", True, "matches")


class EvidenceTests(unittest.TestCase):
    def test_built_from_case_only(self):
        self.assertEqual(list(inspect.signature(build_evidence).parameters)[:1], ["case"])
        evidence = build_evidence(CASE, captured_at="2026-09-28T09:00:00+00:00")
        self.assertEqual(evidence.known()["service"], "photo-api")
        self.assertEqual(evidence.unknown_fields, ["runtime", "region"])
        self.assertTrue(all(i.source == "engineer" for i in evidence.items))
        self.assertNotIn("media-uploader", str(evidence.to_dict()), "no recalled text may enter Evidence")

    def test_add_facts_fills_unknown_and_appends(self):
        evidence = add_facts(build_evidence(CASE), {"runtime": "python3.11", "observation": "413 in nginx log"})
        self.assertEqual(evidence.known()["runtime"], "python3.11")
        self.assertEqual(evidence.unknown_fields, ["region"])
        self.assertIn("413 in nginx log", [i.value for i in evidence.items if i.name == "observation"])

    def test_add_facts_conflict_fails_closed(self):
        with self.assertRaises(EvidenceError):
            add_facts(build_evidence(CASE), {"service": "other-api"})


class VerifyTests(unittest.TestCase):
    def full_evidence(self):
        return add_facts(build_evidence(CASE), {"runtime": "node20", "region": "eu-west-1"})

    def test_t4_missing_decision_blocks(self):
        with self.assertRaises(VerificationError):
            verify([H1, H2], self.full_evidence(), MEMORY, {"H1": ACCEPT})

    def test_memory_never_fills_a_gap(self):
        # H1 cites a strongly matching case, the engineer even claims 'supported', but the cited case's
        # runtime is unknown in current evidence -> insufficient_evidence, regardless of the match.
        results = verify([H1, H2], build_evidence(CASE), MEMORY, {"H1": ACCEPT, "H2": ACCEPT})
        self.assertEqual(results[0].status, "insufficient_evidence")
        mismatched, missing = evidence_gaps(H1, build_evidence(CASE), MEMORY)
        self.assertEqual(missing, ["runtime"])
        self.assertEqual(mismatched, ["proxy", "runtime", "service"])

    def test_claim_used_when_evidence_is_complete(self):
        results = verify([H1, H2], self.full_evidence(), MEMORY,
                         {"H1": ACCEPT, "H2": EngineerDecision("reject", "contradicted", False, "no app limit")})
        self.assertEqual([(r.status, r.engineer_decision) for r in results],
                         [("supported", "accept"), ("contradicted", "reject")])
        self.assertTrue(results[0].relevance_confirmed)
        self.assertFalse(results[1].relevance_confirmed, "a generic hypothesis cannot confirm memory relevance")
        self.assertEqual(results[0].mismatched_environment_fields, ["proxy", "service"])

    def test_no_evidence_at_all_is_insufficient(self):
        bare = normalize(DebugInput("something broke"))
        self.assertEqual(verify([H2], build_evidence(bare), MEMORY, {"H2": ACCEPT})[0].status, "insufficient_evidence")

    def test_claim_required_when_evidence_allows_a_verdict(self):
        with self.assertRaises(VerificationError):
            verify([H2], self.full_evidence(), MEMORY, {"H2": EngineerDecision("accept", None)})


class ResolutionTests(unittest.TestCase):
    def test_contradicted_accepted_hypothesis_becomes_failed_approach(self):
        results = verify([H1, H2], add_facts(build_evidence(CASE), {"runtime": "node20", "region": "eu"}), MEMORY,
                         {"H1": EngineerDecision("accept", "contradicted", True, "limit already 10m"),
                          "H2": EngineerDecision("reject", "contradicted")})
        resolution = build_resolution([H1, H2], results, action_taken="restarted upstream", observed_result="fixed",
                                      root_cause_confirmed="", outcome="workaround")
        self.assertIsNone(resolution.root_cause_confirmed)
        self.assertEqual([f.why_failed for f in resolution.failed_approaches_this_session], ["limit already 10m"])

    def test_evidence_refs_must_be_references(self):
        with self.assertRaises(VerificationError):
            build_resolution([], [], action_taken="a", observed_result="b", root_cause_confirmed=None,
                             outcome="resolved", evidence_refs=["pasted log line"])


if __name__ == "__main__":
    unittest.main()
