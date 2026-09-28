"""MK7: full loop with fakes; T5 retention gate; MemoryCase assembly; failures never retain."""

from __future__ import annotations

import unittest

import loop_support  # noqa: F401  (path wiring)
from loop_support import ABSTAINED_VIEW, RELEVANT_VIEW, RESOLVED, FakeLLM, FakeMemoryPort, ScriptedEngineer, hyp
from debugagent.app import build_investigation
from debugagent.domain.errors import MemoryFailure, StructuredOutputError, VerificationError
from debugagent.domain.models import DebugInput


def investigate(raw, port, llm, engineer, **kwargs):
    return build_investigation(port, llm).run(raw, engineer, **kwargs)


RAW = DebugInput("photo-api resets uploads over 2 MB\nservice=photo-api proxy=nginx-1.24", ["20/20 2.5MB uploads fail"])
REL = "3f9a1c07b2e4d815"


def llm():
    return FakeLLM({"hypotheses": [hyp(cites=[REL]), hyp(text="app-side upload limit")]})


class LoopTests(unittest.TestCase):
    def test_full_loop_retains_a_valid_case(self):
        port = FakeMemoryPort(RELEVANT_VIEW)
        engineer = ScriptedEngineer(facts={"runtime": "python3.11", "region": "eu-west-1"}, resolution=RESOLVED)
        steps = []
        session = investigate(RAW, port, llm(), engineer, session_id="s1", on_step=lambda s: steps.append(len(s.trace)))
        self.assertEqual(len(port.retained), 1)
        case = port.retained[0]
        self.assertEqual(set(case), {"problem_signature", "symptoms", "environment", "observed_evidence",
                                     "investigation_trace", "failed_approaches", "root_cause", "resolution",
                                     "outcome", "verification_notes", "session_id"})
        self.assertEqual(case["environment"], {"service": "photo-api", "runtime": "python3.11",
                                               "proxy": "nginx-1.24", "region": "eu-west-1"})
        self.assertEqual(case["session_id"], "s1")
        self.assertEqual(case["observed_evidence"], ["log://photo-api/2026-09-28/nginx-error.log"])
        self.assertEqual(case["failed_approaches"], [{"approach": "raised client timeout", "why_failed": "proxy closed first"}])
        self.assertTrue(case["investigation_trace"])
        self.assertEqual(session.retention["memory_case_id"], "fake-fact-1")
        self.assertEqual(engineer.stages, ["memory", "evidence", "proposal", "decision", "retention"])
        self.assertEqual(steps, sorted(steps))

    def test_unknown_env_values_never_stored(self):
        port = FakeMemoryPort(ABSTAINED_VIEW)
        investigate(RAW, port, FakeLLM({"hypotheses": [hyp(), hyp()]}), ScriptedEngineer(resolution=RESOLVED))
        self.assertEqual(port.retained[0]["environment"], {"service": "photo-api", "proxy": "nginx-1.24"})

    def test_t5_unresolved_retains_nothing(self):
        port = FakeMemoryPort(RELEVANT_VIEW)
        session = investigate(RAW, port, llm(), ScriptedEngineer(resolution=None))
        self.assertEqual(port.retained, [])
        self.assertIsNone(session.retention)
        self.assertIn("nothing retained", session.trace[-1])

    def test_t5_missing_decision_retains_nothing(self):
        class NoDecision(ScriptedEngineer):
            def decide(self, hypothesis, mismatched, missing):
                return None
        port = FakeMemoryPort(RELEVANT_VIEW)
        with self.assertRaises(VerificationError):
            investigate(RAW, port, llm(), NoDecision(resolution=RESOLVED))
        self.assertEqual(port.retained, [])

    def test_memory_failure_is_not_no_memory(self):
        port = FakeMemoryPort(fail=MemoryFailure("unavailable", "bank unreachable"))
        with self.assertRaises(MemoryFailure):
            investigate(RAW, port, llm(), ScriptedEngineer(resolution=RESOLVED))

    def test_llm_failure_retains_nothing(self):
        port = FakeMemoryPort(RELEVANT_VIEW)
        with self.assertRaises(StructuredOutputError):
            investigate(RAW, port, FakeLLM({"hypotheses": []}), ScriptedEngineer(resolution=RESOLVED))
        self.assertEqual(port.retained, [])



class RetentionGuardTests(unittest.TestCase):
    def test_retention_service_refuses_an_undecided_session(self):
        from debugagent.domain.investigation import Session
        from debugagent.services.retention_service import RetentionService

        port = FakeMemoryPort()
        with self.assertRaises(ValueError):
            RetentionService(port).retain(Session("s", RAW))
        self.assertEqual(port.retained, [])


if __name__ == "__main__":
    unittest.main()
