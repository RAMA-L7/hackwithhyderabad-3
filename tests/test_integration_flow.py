"""MK9 integration: the real HindsightMemoryPort behind Mukul's MemoryPort seam, and the full loop.

Two layers of test:
  * adapter tests prove the port produces exactly the MemoryView / RetentionDecision shape that
    pipeline/memory_port.py pins, that relevance and abstention come from Rama's classify_candidates,
    and that a backend failure becomes MemoryFailure rather than "no memory found".
  * flow tests drive investigate() through the real adapter, so the whole Phase 1 loop
    (normalize -> recall -> EVIDENCE -> PROPOSAL -> DECISION -> retain) crosses the integration seam
    rather than a canned view.

The trust boundary is asserted directly: a recalled case never reaches Evidence, an abstained memory
forces generic hypotheses, and retention never happens without a recorded engineer decision.

The fake backend hands back a fixed score set, exactly as a real one does: classification is decided
from the scores it is given, which is the seam's whole job. Each scenario therefore states the bank it
uses, rather than pretending the fake ranks by query.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401  (path wiring)
from debugagent.memory.hindsight_store import HindsightMemoryStore
from debugagent.memory.matching import AbstentionPolicy
from debugagent.pipeline.investigate import assemble_memory_case, investigate
from debugagent.pipeline.memory_adapter import HindsightMemoryPort, close_port, policy_from_config
from debugagent.pipeline.memory_port import MemoryFailure
from debugagent.pipeline.types import DebugInput
from debugagent.pipeline.verify import EngineerDecision
from loop_support import FakeLLM, hyp
from support import FakeHindsightClient, FakeMemory, FakeScores, memory_config

RELEVANT_QUERY = "orders-api 502 on large payloads"
UNRELATED_QUERY = "quarterly marketing budget spreadsheet"

CASE_A = FakeMemory(
    text="orders-api resets connections on payloads above 2MB; root cause: upstream proxy "
         "request-size limit truncating the body",
    scores=FakeScores(final=0.95, semantic=0.87, keyword=0.7),
    metadata={"case_key": "case-aaaa1111222233", "outcome": "resolved", "service": "orders-api",
              "runtime": "python3.10", "root_cause_key": "upstream proxy request-size limit"},
)
CASE_B = FakeMemory(
    text="orders-api returns 502 for oversized bodies; root cause: upstream dependency 502",
    scores=FakeScores(final=0.90, semantic=0.85, keyword=0.6),
    metadata={"case_key": "case-bbbb4444555566", "outcome": "workaround", "service": "orders-api",
              "runtime": "python3.10", "root_cause_key": "upstream dependency 502"},
)
UNRELATED = FakeMemory(
    text="frontend css is not loading on the marketing site",
    scores=FakeScores(final=0.004, semantic=0.52, keyword=0.0),
    metadata={"case_key": "case-cccc7777888899", "outcome": "resolved", "service": "marketing-site",
              "runtime": "node20", "root_cause_key": "css bundle missing"},
)

RELEVANT = "orders-api 502 on large payloads"


def orders_bank() -> FakeHindsightClient:
    """One orders-api case that matches, plus a case that does not."""
    return FakeHindsightClient([CASE_A, UNRELATED])


def conflicting_bank() -> FakeHindsightClient:
    """Two orders-api cases with the same symptoms and different root causes."""
    return FakeHindsightClient([CASE_A, CASE_B, UNRELATED])


def unrelated_bank() -> FakeHindsightClient:
    """Nothing in the bank resembles the query."""
    return FakeHindsightClient([UNRELATED])


class FailingClient(FakeHindsightClient):
    """Every call raises, the way an unreachable or rejected backend behaves."""

    def __init__(self, error: Exception):
        super().__init__([CASE_A])
        self.error = error

    def recall(self, **kwargs):
        raise self.error

    def retain(self, **kwargs):
        raise self.error


class Unauthorized(RuntimeError):
    status = 401


def port_with(client, policy: AbstentionPolicy | None = None) -> HindsightMemoryPort:
    config = memory_config(Path(tempfile.mkdtemp()))
    return HindsightMemoryPort(HindsightMemoryStore(config, client=client), policy=policy)


class AdapterRecallTests(unittest.TestCase):
    def test_view_has_the_pinned_shape(self):
        view = port_with(orders_bank()).recall_and_classify(RELEVANT_QUERY)
        self.assertEqual(set(view), {"bank_id", "recalled_at", "report", "abstention"})
        self.assertIsInstance(view["bank_id"], str)
        self.assertIsInstance(view["recalled_at"], str)
        self.assertEqual(set(view["report"]), {"candidates", "excluded", "top_score", "threshold_used"})
        self.assertEqual(set(view["abstention"]),
                         {"abstained", "relevance_class", "top_score", "threshold_used", "reason"})

    def test_candidates_carry_text_and_outcome_joined_from_the_case(self):
        """Q8: MatchCandidate carries neither; the CLI and the prompt both need them."""
        view = port_with(orders_bank()).recall_and_classify(RELEVANT_QUERY)
        candidate = next(c for c in view["report"]["candidates"] if c["case_id"] == "case-aaaa1111222233")
        self.assertIn("upstream proxy request-size limit", candidate["text"])
        self.assertEqual(candidate["outcome"], "resolved")

    def test_unrecorded_environment_fields_are_dropped_not_passed_as_values(self):
        no_runtime = FakeMemory(text=CASE_A.text, scores=CASE_A.scores,
                                metadata=dict(CASE_A.metadata, runtime="unknown"))
        view = port_with(FakeHindsightClient([no_runtime])).recall_and_classify(RELEVANT_QUERY)
        self.assertEqual(view["report"]["candidates"][0]["environment"], {"service": "orders-api"})

    def test_irrelevant_case_is_excluded_but_kept_in_the_trace(self):
        view = port_with(orders_bank()).recall_and_classify(RELEVANT_QUERY)
        excluded = {c["case_id"]: c for c in view["report"]["excluded"]}
        self.assertIn("case-cccc7777888899", excluded)
        self.assertEqual(excluded["case-cccc7777888899"]["relevance_class"], "irrelevant")

    def test_relevant_query_does_not_abstain(self):
        view = port_with(orders_bank()).recall_and_classify(RELEVANT_QUERY)
        self.assertFalse(view["abstention"]["abstained"])
        self.assertEqual(view["abstention"]["relevance_class"], "relevant")

    def test_nothing_matching_abstains_with_a_visible_reason(self):
        view = port_with(unrelated_bank()).recall_and_classify(UNRELATED_QUERY)
        self.assertTrue(view["abstention"]["abstained"])
        self.assertEqual(view["abstention"]["relevance_class"], "irrelevant")
        self.assertTrue(view["abstention"]["reason"].strip())

    def test_disagreeing_cases_are_both_surfaced_and_it_abstains(self):
        view = port_with(conflicting_bank()).recall_and_classify(f"{RELEVANT} root cause")
        classes = {c["case_id"]: c["relevance_class"] for c in view["report"]["candidates"]}
        self.assertEqual(classes["case-aaaa1111222233"], "contradictory")
        self.assertEqual(classes["case-bbbb4444555566"], "contradictory")
        listed = [x for c in view["report"]["candidates"] for x in c["conflicts_with"]]
        self.assertIn("case-bbbb4444555566", listed)
        self.assertTrue(view["abstention"]["abstained"])
        self.assertEqual(view["abstention"]["relevance_class"], "contradictory")

    def test_duplicate_recall_rows_are_deduplicated_by_case(self):
        client = FakeHindsightClient([CASE_A, UNRELATED, CASE_A, UNRELATED])
        view = port_with(client).recall_and_classify(RELEVANT_QUERY)
        ids = [c["case_id"] for c in view["report"]["candidates"] + view["report"]["excluded"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_blank_query_is_a_schema_failure_not_an_empty_view(self):
        with self.assertRaises(MemoryFailure) as caught:
            port_with(orders_bank()).recall_and_classify("   ")
        self.assertEqual(caught.exception.kind, "schema")

    def test_backend_failure_maps_to_unavailable(self):
        with self.assertRaises(MemoryFailure) as caught:
            port_with(FailingClient(RuntimeError("connection reset"))).recall_and_classify(RELEVANT_QUERY)
        self.assertEqual(caught.exception.kind, "unavailable")

    def test_auth_failure_maps_to_auth(self):
        with self.assertRaises(MemoryFailure) as caught:
            port_with(FailingClient(Unauthorized("no"))).recall_and_classify(RELEVANT_QUERY)
        self.assertEqual(caught.exception.kind, "auth")

    def test_policy_takes_the_configured_thresholds_and_keeps_the_documented_defaults(self):
        policy = policy_from_config(memory_config(Path(tempfile.mkdtemp())))
        defaults = AbstentionPolicy()
        self.assertEqual(policy.min_final_score, defaults.min_final_score)
        self.assertEqual(policy.weak_reference_floor, defaults.weak_reference_floor)
        # L5: semantic_floor and contradiction_margin are not on MemoryConfig, so they stay defaults.
        self.assertEqual(policy.semantic_floor, defaults.semantic_floor)
        self.assertEqual(policy.contradiction_margin, defaults.contradiction_margin)


VALID_CASE = {
    "problem_signature": "orders-api 502 on large payloads",
    "symptoms": ["502 for payloads above 2MB"],
    "environment": {"service": "orders-api", "runtime": "python3.10"},
    "observed_evidence": ["log://orders-api/2026-09-28/error.log"],
    "investigation_trace": ["checked upstream logs", "compared request sizes"],
    "failed_approaches": [{"approach": "raised client timeouts", "why_failed": "no effect on the 502"}],
    "root_cause": "upstream proxy request-size limit",
    "resolution": "raised the proxy request-size limit",
    "outcome": "resolved",
    "verification_notes": "reproduced with a 3MB body after the change",
    "session_id": "sess-1",
}


class AdapterRetainTests(unittest.TestCase):
    def test_retain_returns_the_pinned_decision_shape(self):
        decision = port_with(orders_bank()).retain(VALID_CASE)
        self.assertTrue(decision["retained"])
        self.assertTrue(decision["validated"])
        self.assertTrue(decision["memory_case_id"])
        self.assertIn("reason", decision)

    def test_retain_is_idempotent(self):
        port = port_with(orders_bank())
        first = port.retain(VALID_CASE)
        second = port.retain(VALID_CASE)
        self.assertTrue(first["retained"])
        self.assertFalse(second["retained"])
        self.assertIn("already retained", second["reason"])

    def test_invalid_case_is_a_schema_failure_and_writes_nothing(self):
        client = orders_bank()
        with self.assertRaises(MemoryFailure) as caught:
            port_with(client).retain(dict(VALID_CASE, outcome="not-an-outcome"))
        self.assertEqual(caught.exception.kind, "schema")
        self.assertEqual(client.retained, [])


class ScriptedEngineer:
    """A deterministic engineer. Answers are fixed, in the order investigate() asks for them."""

    def __init__(self, facts: dict | None = None, decisions: dict | None = None,
                 resolution: dict | None = None):
        self.facts = facts or {}
        self.decisions = decisions or {}
        self.resolution = resolution
        self.sections: list[str] = []

    def show(self, text: str) -> None:
        self.sections.append(text)

    def current_facts(self, evidence):
        return dict(self.facts)

    def decide(self, hypothesis, mismatched, missing):
        # None when the engineer recorded nothing: verify() must then refuse to proceed.
        return self.decisions.get(hypothesis.ref)

    def resolve(self, session):
        return None if self.resolution is None else dict(self.resolution)

    def titles(self) -> list[str]:
        # each section is: rule line, then "TITLE  · subtitle"
        return [section.splitlines()[1].split("  ·")[0]
                for section in self.sections if len(section.splitlines()) > 1]


def run_loop(client, *, outputs, decisions=None, resolution=None, facts=None, description=None):
    """Drive investigate() through the real adapter and return (session, engineer)."""
    port = port_with(client)
    engineer = ScriptedEngineer(facts, decisions, resolution)
    raw = DebugInput.from_dict({
        "description": description or "orders-api 502 on large payloads\nservice=orders-api",
        "measurements": ["502 for payloads above 2MB"],
    })
    session = investigate(raw, port, FakeLLM(*outputs), engineer)
    return session, engineer


RESOLUTION = {
    "action_taken": "raised the upstream proxy request-size limit",
    "observed_result": "a 3MB body now succeeds",
    "root_cause_confirmed": "upstream proxy request-size limit",
    "outcome": "resolved",
    "failed_approaches": [{"approach": "raised client timeouts", "why_failed": "no effect on the 502"}],
    "evidence_refs": ["log://orders-api/2026-09-28/error.log"],
}


def citing(*case_ids) -> dict:
    """Two hypotheses, the first citing past cases. The response schema requires 2-3."""
    return {"hypotheses": [hyp(cites=list(case_ids)), hyp(text="an unrelated alternative cause")]}


def uncited() -> dict:
    return {"hypotheses": [hyp(), hyp(text="an unrelated alternative cause")]}


def accept_h1() -> dict:
    return {"H1": EngineerDecision("accept", "supported", True, "matches the logs"),
            "H2": EngineerDecision("reject", "contradicted", False, "ruled out by the same logs")}


def reject_both() -> dict:
    return {"H1": EngineerDecision("reject", "contradicted", False, "not this time"),
            "H2": EngineerDecision("reject", "contradicted", False, "ruled out")}


class FullLoopTests(unittest.TestCase):
    def test_complete_flow_renders_the_four_trust_sections(self):
        session, engineer = run_loop(
            orders_bank(),
            outputs=[citing("case-aaaa1111222233")],
            decisions=accept_h1(),
            facts={"runtime": "python3.10"},
            resolution=RESOLUTION,
        )
        for expected in ("MEMORY", "EVIDENCE", "PROPOSAL", "DECISION"):
            self.assertIn(expected, engineer.titles())
        self.assertEqual(session.verifications[0].status, "supported")
        self.assertTrue(session.retention["retained"])

    def test_recalled_environment_never_enters_evidence(self):
        """The cited past case states a runtime the engineer never mentioned.

        The runtime placeholder must still appear (so the gap is visible and drives
        insufficient_evidence), but it must stay unknown: memory may not fill it in.
        """
        session, _ = run_loop(
            orders_bank(),
            outputs=[citing("case-aaaa1111222233")],
            decisions=accept_h1(),
            resolution=RESOLUTION,
            description="orders-api 502 on large payloads\nservice=orders-api",  # runtime NOT stated
        )
        items = {item.name: item for item in session.evidence.items}
        self.assertTrue(items["service"].known, "the current input supplies this one")
        self.assertIn("runtime", items, "the gap must be visible, not silently absent")
        self.assertFalse(items["runtime"].known, "memory must not supply a value for it")
        self.assertIsNone(items["runtime"].value)
        self.assertNotIn("runtime", session.evidence.known())
        self.assertIn("runtime", session.evidence.unknown_fields)

    def test_missing_evidence_yields_insufficient_evidence_however_strong_the_match(self):
        """A perfect memory match cannot upgrade a verdict on its own."""
        session, _ = run_loop(
            orders_bank(),
            outputs=[citing("case-aaaa1111222233")],
            decisions=accept_h1(),
            resolution=None,
            description="orders-api 502 on large payloads\nservice=orders-api",  # runtime NOT stated
        )
        self.assertFalse(session.memory.abstained)
        self.assertEqual(session.verifications[0].status, "insufficient_evidence")
        self.assertIn("runtime", session.verifications[0].mismatched_environment_fields)

    def test_abstained_memory_forces_generic_hypotheses(self):
        session, _ = run_loop(
            unrelated_bank(),
            outputs=[citing("case-aaaa1111222233"), uncited()],
            decisions=accept_h1(),
            resolution=None,
            description="quarterly marketing budget\nproxy=nginx",
        )
        self.assertTrue(session.memory.abstained)
        for hypothesis in session.proposal.hypotheses:
            self.assertEqual(hypothesis.supporting_case_ids, [],
                             "an abstained memory must leave every hypothesis uncited")
            self.assertEqual(hypothesis.relevance_state, "generic")

    def test_contradictory_memory_surfaces_both_sides_and_stays_generic(self):
        session, _ = run_loop(
            conflicting_bank(),
            outputs=[citing("case-aaaa1111222233"), uncited()],
            decisions=accept_h1(),
            facts={"runtime": "python3.10"},
            resolution=None,
            description=f"{RELEVANT}\nservice=orders-api\nruntime=python3.10",
        )
        self.assertTrue(session.memory.abstained)
        self.assertEqual(session.memory.abstention["relevance_class"], "contradictory")
        shown = {c["case_id"] for c in session.memory.candidates}
        self.assertEqual(shown, {"case-aaaa1111222233", "case-bbbb4444555566"},
                         "both sides of a disagreement must be shown")
        self.assertEqual(session.proposal.hypotheses[0].supporting_case_ids, [])

    def test_no_retention_without_an_engineer_decision(self):
        with self.assertRaises(Exception) as caught:
            run_loop(
                orders_bank(),
                outputs=[citing("case-aaaa1111222233")],
                decisions={},
                resolution=RESOLUTION,
            )
        self.assertIn("decision", str(caught.exception))

    def test_no_retention_when_the_engineer_does_not_resolve(self):
        session, _ = run_loop(
            orders_bank(),
            outputs=[citing("case-aaaa1111222233")],
            decisions=reject_both(),
            resolution=None,
        )
        self.assertIsNone(session.resolution)
        self.assertIsNone(session.retention)
        self.assertIn("not resolved", " ".join(session.trace))

    def test_retention_happens_only_after_the_decision_and_stores_the_engineers_report(self):
        session, _ = run_loop(
            orders_bank(),
            outputs=[citing("case-aaaa1111222233")],
            decisions=accept_h1(),
            facts={"runtime": "python3.10"},
            resolution=RESOLUTION,
        )
        self.assertTrue(session.retention["retained"])
        self.assertEqual(session.verifications[0].engineer_decision, "accept")
        stored = assemble_memory_case(session)
        self.assertEqual(stored["session_id"], session.session_id)
        self.assertEqual(stored["outcome"], "resolved")
        self.assertEqual(len(stored["failed_approaches"]), 1)
        self.assertEqual(stored["observed_evidence"], ["log://orders-api/2026-09-28/error.log"])

    def test_hypothesis_may_only_cite_recalled_cases(self):
        with self.assertRaises(Exception):
            run_loop(
                orders_bank(),
                outputs=[citing("case-not-in-memory")],
                decisions=accept_h1(),
                resolution=None,
            )


class ResourceCleanupTests(unittest.TestCase):
    """The Hindsight client opens an aiohttp session on first use. It must be released explicitly at
    the application lifecycle boundary, not left to the garbage collector at interpreter exit."""

    def test_store_close_releases_the_backend_client(self):
        client = orders_bank()
        store = HindsightMemoryStore(memory_config(Path(tempfile.mkdtemp())), client=client)
        self.assertFalse(client.closed)
        store.close()
        self.assertTrue(client.closed, "close() must reach the backend client")

    def test_store_close_is_idempotent(self):
        client = orders_bank()
        store = HindsightMemoryStore(memory_config(Path(tempfile.mkdtemp())), client=client)
        store.close()
        store.close()  # must not raise, and must not double-close the transport
        self.assertTrue(client.closed)

    def test_store_close_tolerates_a_client_without_close(self):
        class Bare:
            def get_bank_config(self, bank_id):
                return {"bank_id": bank_id}

        store = HindsightMemoryStore(memory_config(Path(tempfile.mkdtemp())), client=Bare())
        store.close()  # no close() on the client: nothing to release, must not raise

    def test_port_close_delegates_to_the_store(self):
        client = orders_bank()
        port = port_with(client)
        port.close()
        self.assertTrue(client.closed)

    def test_close_port_is_a_no_op_for_a_port_that_owns_no_client(self):
        class Ownerless:
            pass

        close_port(Ownerless())  # no close(): must not raise

    @staticmethod
    def _abort(prompt=""):
        """End the session at the first prompt. TerminalEngineer turns EOFError into AbortSession,
        so this exercises the early-exit path without driving a whole interactive session."""
        raise EOFError

    def test_cli_leaves_an_injected_port_to_its_caller(self):
        """main() closes only a port it created; an injected port belongs to whoever passed it in."""
        import io as _io

        from debugagent import cli

        closed: list[str] = []

        class Port:
            def recall_and_classify(self, query):  # pragma: no cover - never reached
                raise AssertionError("retain/recall must not be reached")

            def retain(self, case):  # pragma: no cover - never reached
                raise AssertionError("retain must not be reached")

            def close(self):
                closed.append("closed")

        code = cli.main(["debug", "--memory", "hindsight", "--env", ".env"], ask=self._abort,
                        out=_io.StringIO(), err=_io.StringIO(),
                        llm=FakeLLM({"hypotheses": [hyp(), hyp()]}), port=Port(),
                        state_dir=tempfile.mkdtemp())
        self.assertEqual(code, 1)
        self.assertEqual(closed, [], "an injected port belongs to the caller and must not be closed")

    def test_cli_closes_the_port_it_built_on_the_abort_path(self):
        """A session that ends early must still release the backend HTTP session."""
        import io as _io

        from debugagent import cli

        closed: list[str] = []

        class Port:
            def recall_and_classify(self, query):  # pragma: no cover - never reached
                raise AssertionError("recall must not be reached")

            def retain(self, case):  # pragma: no cover - never reached
                raise AssertionError("retain must not be reached")

            def close(self):
                closed.append("closed")

        original = cli.make_port
        cli.make_port = lambda spec, state: Port()
        try:
            code = cli.main(["debug", "--memory", "hindsight", "--env", ".env"], ask=self._abort,
                            out=_io.StringIO(), err=_io.StringIO(),
                            llm=FakeLLM({"hypotheses": [hyp(), hyp()]}),
                            state_dir=tempfile.mkdtemp())
        finally:
            cli.make_port = original
        self.assertEqual(code, 1, "an aborted session exits non-zero")
        self.assertEqual(closed, ["closed"], "the port must be released even when the session ends early")

    def test_cli_closes_the_port_it_built_when_recall_fails(self):
        """A memory failure is an error, not 'no memory found' - and still releases the session."""
        import io as _io

        from debugagent import cli
        from debugagent.pipeline.memory_port import MemoryFailure

        closed: list[str] = []

        class Port:
            def recall_and_classify(self, query):
                raise MemoryFailure("unavailable", "bank unreachable")

            def retain(self, case):  # pragma: no cover - never reached
                raise AssertionError("retain must not be reached")

            def close(self):
                closed.append("closed")

        original = cli.make_port
        cli.make_port = lambda spec, state: Port()
        # description lines, blank, then measurements lines, blank. recall() is the next step and it
        # fails, so the script never has to satisfy a later prompt.
        script = ["media-uploader resets connections on uploads over 2MB", "service=media-uploader", "", ""]
        try:
            code = cli.main(["debug", "--memory", "hindsight", "--env", ".env"],
                            ask=lambda prompt="": script.pop(0) if script else "",
                            out=_io.StringIO(), err=_io.StringIO(),
                            llm=FakeLLM({"hypotheses": [hyp(), hyp()]}),
                            state_dir=tempfile.mkdtemp())
        finally:
            cli.make_port = original
        self.assertEqual(code, 1, "an unreachable bank is an error, not 'no memory found'")
        self.assertEqual(closed, ["closed"], "the port must be released even when the session fails")


if __name__ == "__main__":
    unittest.main()
