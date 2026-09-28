"""MK5: T3 citation integrity, T6 generic path, derived relevance_state, past/current separation in the prompt."""

from __future__ import annotations

import unittest

import loop_support  # noqa: F401  (path wiring)
from loop_support import ABSTAINED_VIEW, CONTRADICTORY_VIEW, PARTIAL_VIEW, RELEVANT_VIEW, FakeLLM, FakeMemoryPort, candidate, hyp, view
from debugagent.domain.errors import StructuredOutputError
from debugagent.domain.models import DebugInput
from debugagent.services.hypothesis_service import HypothesisService, build_prompt
from debugagent.services.normalization_service import NormalizationService
from debugagent.services.recall_service import RecallService

CASE = NormalizationService().normalize(DebugInput("photo-api resets uploads over 2 MB\nservice=photo-api proxy=nginx-1.24"))
REL = "3f9a1c07b2e4d815"


def memory(v):
    return RecallService(FakeMemoryPort(v)).recall(CASE)


def generate_hypotheses(case, mem, llm):
    return HypothesisService(llm).generate(case, mem)


class CitationTests(unittest.TestCase):
    def test_t3_invented_id_is_rejected_and_retried(self):
        llm = FakeLLM({"hypotheses": [hyp(cites=["seed-999"]), hyp()]},
                      {"hypotheses": [hyp(cites=[REL]), hyp(text="upstream timeout")]})
        proposal = generate_hypotheses(CASE, memory(RELEVANT_VIEW), llm)
        self.assertEqual(proposal.hypotheses[0].supporting_case_ids, [REL])
        self.assertEqual(proposal.attempts[0]["error_class"], "INVALID_OUTPUT")
        self.assertIn("seed-999", proposal.attempts[0]["detail"])

    def test_t3_excluded_case_cannot_be_cited(self):
        llm = FakeLLM({"hypotheses": [hyp(cites=["a7c2e9f04b1d6e38"]), hyp()]})
        with self.assertRaises(StructuredOutputError):
            generate_hypotheses(CASE, memory(RELEVANT_VIEW), llm)

    def test_t6_abstained_means_generic_and_no_citations(self):
        llm = FakeLLM({"hypotheses": [hyp(cites=[REL]), hyp()]}, {"hypotheses": [hyp(), hyp(text="dns")]})
        proposal = generate_hypotheses(CASE, memory(ABSTAINED_VIEW), llm)
        self.assertEqual([h.relevance_state for h in proposal.hypotheses], ["generic", "generic"])
        self.assertTrue(all(not h.supporting_case_ids for h in proposal.hypotheses))
        self.assertIn("Memory abstained", llm.prompts[0])

    def test_abstained_contradictory_cannot_be_cited(self):
        llm = FakeLLM({"hypotheses": [hyp(cites=["seed-001"]), hyp()]}, {"hypotheses": [hyp(), hyp()]})
        proposal = generate_hypotheses(CASE, memory(CONTRADICTORY_VIEW), llm)
        self.assertEqual(proposal.hypotheses[0].relevance_state, "generic")

    def test_schema_requires_two_or_three(self):
        with self.assertRaises(StructuredOutputError):
            generate_hypotheses(CASE, memory(RELEVANT_VIEW), FakeLLM({"hypotheses": [hyp()]}))


class RelevanceStateTests(unittest.TestCase):
    def test_derived_from_cited_classes(self):
        both = view([candidate(REL), candidate("seed-001", "partial"), candidate("seed-002", "contradictory")])
        llm = FakeLLM({"hypotheses": [hyp(cites=[REL, REL]), hyp(cites=["seed-001"]),
                                      hyp(cites=[REL, "seed-002"])]})
        states = [(h.ref, h.relevance_state, h.supporting_case_ids) for h in generate_hypotheses(CASE, memory(both), llm).hypotheses]
        self.assertEqual(states, [("H1", "supported", [REL]), ("H2", "weak-reference", ["seed-001"]),
                                  ("H3", "conditional", [REL, "seed-002"])])

    def test_uncited_is_generic(self):
        proposal = generate_hypotheses(CASE, memory(PARTIAL_VIEW), FakeLLM({"hypotheses": [hyp(), hyp()]}))
        self.assertEqual(proposal.hypotheses[0].relevance_state, "generic")

    def test_provider_metadata_carried(self):
        proposal = generate_hypotheses(CASE, memory(RELEVANT_VIEW), FakeLLM({"hypotheses": [hyp(), hyp()]}, provider="baseten"))
        self.assertEqual((proposal.provider, proposal.fallback_used), ("baseten", False))


class PromptTests(unittest.TestCase):
    def test_past_and_current_are_labelled_apart(self):
        prompt = build_prompt(CASE, memory(RELEVANT_VIEW))
        current, past = prompt.split("\nPAST CASES\n", 1)
        self.assertIn("environment now: service=photo-api, runtime=unknown, proxy=nginx-1.24, region=unknown", current)
        self.assertNotIn("media-uploader", current)
        self.assertIn(f"[case_id={REL}] relation: relevant; original environment: service=media-uploader", past)
        self.assertIn("service (then media-uploader, now photo-api)", past)
        self.assertIn("Never describe the current system with a past case's service name", prompt)

    def test_excluded_cases_never_reach_the_prompt(self):
        self.assertNotIn("a7c2e9f04b1d6e38", build_prompt(CASE, memory(RELEVANT_VIEW)))


if __name__ == "__main__":
    unittest.main()
