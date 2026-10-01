"""UI-1: the composition root for a browser session.

`host.py` is where a browser session meets the real objects - the memory port, the Coordinator, the
LLM. It is the only module that knows both an `Engineer` implementation and how to assemble an
investigation, so it is also the only place a mistake here would be invisible: the server tests inject
a fake factory and would all still pass.

These tests therefore use the REAL factory, with only the memory bank and the LLM doubled.
"""

from __future__ import annotations

import json
import sys
import unittest
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread

import loop_support
from debugagent.agents.memory_specialist import MemorySpecialist
from debugagent.application.host import make_session_factory, parse_issue
from debugagent.application.web_server import SessionRegistry, make_server
from debugagent.composition import build_runtime
from debugagent.pipeline.normalize import normalize
from debugagent.pipeline.types import SchemaError
from loop_support import FakeLLM, FakeMemoryPort, hyp

#: A case id the default `FakeMemoryPort` view actually recalls. Cited rather than invented, because
#: the proposal schema only accepts a citation memory really surfaced.
CITED_CANDIDATE = "3f9a1c07b2e4d815"


def _llm() -> FakeLLM:
    return FakeLLM({"hypotheses": [
        hyp(text="pool size too small for checkout traffic", cites=(CITED_CANDIDATE,)),
        hyp(text="a retry storm exhausts the pool", cites=(CITED_CANDIDATE,),
            step="count retries per request")]})


ISSUE = ("checkout service fails: connection pool exhausted\n"
         "service=checkout\n"
         "region=eu-west-1\n"
         "seen in the checkout logs after 5 retries")


class ParseIssue(unittest.TestCase):
    """One textarea, the terminal's existing convention. A second format would be a second thing to learn."""

    def test_the_issue_reaches_normalize_intact(self):
        case = normalize(parse_issue(ISSUE))
        self.assertEqual(case.problem_signature, "checkout service fails: connection pool exhausted")
        self.assertEqual(case.environment["service"], "checkout")
        self.assertEqual(case.environment["region"], "eu-west-1")

    def test_an_unstated_key_stays_unknown_rather_than_invented(self):
        case = normalize(parse_issue("checkout is slow\nservice=checkout"))
        self.assertIsNone(case.environment["proxy"])
        self.assertIsNone(case.environment["region"])

    def test_extra_lines_become_symptoms(self):
        case = normalize(parse_issue(ISSUE))
        self.assertIn("seen in the checkout logs after 5 retries", case.symptoms)

    def test_blank_lines_are_dropped(self):
        case = normalize(parse_issue("\n\ncheckout is slow\n\n\nservice=checkout\n\n"))
        self.assertEqual(case.problem_signature, "checkout is slow")

    def test_an_empty_issue_is_refused(self):
        for text in ("", "   ", "\n\n", None):
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as caught:
                    parse_issue(text)
                self.assertIn("empty", str(caught.exception))

    def test_the_parser_does_not_judge_whether_an_issue_is_usable(self):
        """`parse_issue` only checks that there IS text; whether it is usable is normalize's call.

        Splitting the two is deliberate: the parser must not grow a second opinion about what a valid
        issue is, or the terminal and the browser would come to disagree about it.
        """
        from debugagent.pipeline.normalize import NormalizationError

        with self.assertRaises(NormalizationError):
            normalize(parse_issue("checkout is slow\nservice=checkout\nservice=media-uploader"))


class RealCompositionRoot(unittest.TestCase):
    """The real factory, a real server, a real investigation. Only the bank and the LLM are doubled."""

    def setUp(self):
        self._tmp = None
        self.port = FakeMemoryPort()
        self.runtime = build_runtime(self.port, memory_specialist=MemorySpecialist(self.port))
        self.addCleanup(self.runtime.close)
        self.registry = SessionRegistry()
        self.saved: list = []
        self.factory = make_session_factory(
            registry=self.registry, port=self.port, llm=_llm(),
            coordinator=self.runtime.coordinator, timeout=10.0,
            on_complete=lambda session: self.saved.append(session))
        self.server = make_server(self.registry, self.factory, host="127.0.0.1", port=0)
        self.base = f"127.0.0.1:{self.server.server_address[1]}"
        thread = Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _get(self, path):
        conn = HTTPConnection(self.base, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def _post(self, path, payload):
        conn = HTTPConnection(self.base, timeout=5)
        try:
            conn.request("POST", path, body=json.dumps(payload).encode("utf-8"),
                         headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def _drive_to_completion(self, decision, resolution):
        status, started = self._post("/api/sessions", {"description": ISSUE})
        self.assertEqual(status, 202, started)
        session_id = started["session_id"]
        asked = 0
        for _ in range(20):
            _, snapshot = self._get(f"/api/sessions/{session_id}")
            pending = snapshot.get("pending")
            if pending is None:
                if snapshot["status"] != "running":
                    return session_id, snapshot
                continue
            payload = decision if pending["kind"] == "decide" else {"decision": {"resolution": resolution}}
            self.assertEqual(self._post(f"/api/sessions/{session_id}/decision", payload)[0], 200)
            asked += 1
        self.fail("the run never finished")

    def test_a_browser_run_produces_the_documented_panels(self):
        _, snapshot = self._drive_to_completion(
            {"decision": "accept", "claim": "supported", "confirmed": True, "note": "confirmed"},
            {"action_taken": "raised pool size to 20", "observed_result": "no exhaustion",
             "root_cause_confirmed": "pool too small for retry storms", "outcome": "resolved",
             "failed_approaches": [], "evidence_refs": ["log://checkout/2026-10-01/app.log"]})
        self.assertEqual(snapshot["status"], "complete", snapshot.get("error"))
        self.assertEqual(snapshot["outcome"], "resolved")
        view = snapshot["view"]
        self.assertEqual([panel["name"] for panel in view["panels"]],
                         ["Memory", "Evidence", "Reasoning", "Validation", "Decision", "Trace"])
        self.assertEqual({panel["trust"] for panel in view["panels"]},
                         {"KNOWLEDGE", "EVIDENCE", "PROPOSAL", "DECISION", "TRACE"})

    def test_the_typed_issue_reached_the_case(self):
        _, snapshot = self._drive_to_completion({"decision": "reject"}, None)
        evidence = next(panel for panel in snapshot["view"]["panels"] if panel["name"] == "Evidence")
        values = {item["label"]: item.get("value") for item in evidence["items"]}
        self.assertEqual(values["service"], "checkout")
        self.assertEqual(values["region"], "eu-west-1")

    def test_the_host_persists_the_session_it_was_given(self):
        """The host is the only reason `debugagent inspect` can read a browser session."""
        _, snapshot = self._drive_to_completion({"decision": "reject"}, None)
        self.assertEqual(snapshot["status"], "complete", snapshot.get("error"))
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(self.saved[0].session_id, snapshot["session_id"])
        self.assertIsInstance(json.dumps(self.saved[0].to_dict(), default=str), str)

    def test_a_failed_run_reports_nothing_rather_than_an_empty_session(self):
        """A crash must not be persisted as if it were a session.

        `on_complete` receives `None` for a run that raised, and the host's own guard is what keeps
        that from becoming an empty record - which is why the guard is part of the contract rather than
        a defensive flourish in one caller.
        """
        session_id, snapshot = self._drive_to_completion({"decision": "reject"}, {"nonsense": True})
        self.assertEqual(snapshot["status"], "failed")
        self.assertIn("build_resolution", snapshot["error"])
        self.assertIsNone(snapshot["view"], "a failed run has no session to project")
        self.assertEqual(self.saved, [None], "a failed run was persisted as a session")
        self.assertIn(session_id, self.registry.ids(), "the failed session should still be listed")

    def test_an_unusable_issue_is_refused_before_a_session_exists(self):
        """A contradictory issue is a bad request, not a run that fails a second later.

        The engineer typed something unusable; answering 202 and then showing a failed run would make
        them think the investigation went wrong when in fact they were told nothing until it had.
        """
        status, payload = self._post("/api/sessions", {"description": "slow\nservice=a\nservice=b"})
        self.assertEqual(status, 400, payload)
        self.assertIn("service", payload["error"])
        self.assertEqual(self.registry.ids(), [], "a rejected request left a session behind")
        self.assertEqual(self.saved, [], "a rejected request was persisted")

    def test_a_session_runs_through_the_shared_coordinator(self):
        """One client, one Coordinator, one lane - the browser path holds the same invariant."""
        from debugagent.composition import coordinator_for

        session_id, snapshot = self._drive_to_completion({"decision": "reject"}, None)
        self.assertEqual(snapshot["status"], "complete", snapshot.get("error"))
        self.assertIs(coordinator_for(self.port), self.runtime.coordinator)
        self.assertEqual(len(self.registry.ids()), 1)
        self.assertIn(session_id, self.registry.ids())

    def test_an_empty_issue_creates_no_session(self):
        status, payload = self._post("/api/sessions", {"description": "  \n  "})
        self.assertEqual(status, 400, payload)
        self.assertIn("description", payload["error"])
        self.assertEqual(self.registry.ids(), [])

    def test_the_registry_id_is_the_session_id(self):
        """The UI and `debugagent inspect` must be talking about the same investigation."""
        session_id, snapshot = self._drive_to_completion({"decision": "reject"}, None)
        self.assertEqual(snapshot["session_id"], session_id)
        self.assertEqual(snapshot["view"]["session_id"], session_id)
        self.assertEqual(self.saved[0].session_id, session_id)


if __name__ == "__main__":
    unittest.main()
