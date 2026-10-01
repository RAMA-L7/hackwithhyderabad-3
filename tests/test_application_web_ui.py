"""UI-1: the web surface. `WebEngineer`, the server, and the decision loop.

The load-bearing test in this file is `EngineerCompatibility.test_a_web_run_matches_a_terminal_run`.
It runs the SAME investigation twice - once through the repository's terminal `ScriptedEngineer`, once
through a `WebEngineer` answered by a background thread - and asserts the two sessions serialise
identically. That is the whole architectural claim of the milestone in one assertion: the UI is another
implementation of a protocol that already existed, so it changes nothing about what the flow produces. If
a future change makes the UI's answers reach further than a terminal's, that test fails.

The rest covers the trust boundary. A browser is untrusted input on the same footing as engineer
terminal input, and the tests here pin the three things it may do (facts, a decision, a resolution) and
the four it may not (write evidence, set a verification status, fabricate an answer, reach the memory
bank).
"""

from __future__ import annotations

import ast
import json
import sys
import threading
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path

import loop_support
from debugagent.application.view_model import build_view_model
from debugagent.application.web_engineer import (
    ENGINEER_DECISIONS,
    DecisionRequest,
    DecisionTimeout,
    WebEngineer,
)
from debugagent.application.web_server import (
    MAX_SESSIONS,
    SessionRegistry,
    _run_investigation,
    make_server,
    serve,
)
from debugagent.pipeline.evidence import build_evidence
from debugagent.pipeline.investigate import investigate
from debugagent.pipeline.types import DebugInput
from debugagent.pipeline.verify import EngineerDecision
from loop_support import RELEVANT_VIEW, RESOLVED, FakeLLM, FakeMemoryPort, ScriptedEngineer, candidate, \
    hyp, view

APP_DIR = Path(__file__).resolve().parents[1] / "src" / "debugagent" / "application"

SYMPTOM = "connection pool exhausted"
#: The answer set, so the terminal engineer and the web engineer are given identical input.
ACCEPT = {"decision": "accept", "claim": "supported", "confirmed": True, "note": "looks right"}
#: A case id `RELEVANT_VIEW` actually recalls. Cited rather than invented, because the proposal schema
#: only accepts a citation memory really surfaced - so a made-up id would fail the citation check
#: before any of the code under test ran.
CITED = "3f9a1c07b2e4d815"


def _raw() -> DebugInput:
    return DebugInput(f"checkout service fails: {SYMPTOM}",
                      [f"seen in {SYMPTOM} logs"],
                      {"service": "checkout", "region": "eu-west-1"})


def _llm() -> FakeLLM:
    """A schema-valid proposal. The schema sets its own minimum hypothesis count, so this matches it
    rather than the smallest list that would be convenient here."""
    return FakeLLM({"hypotheses": [
        hyp(text="pool size too small for checkout traffic", cites=(CITED,)),
        hyp(text="a retry storm exhausts the pool rather than pool size", cites=(CITED,),
            step="count retries per request in the log")]})


def _proposal() -> "Proposal":
    """A schema-valid proposal, built the way `generate_hypotheses` builds one."""
    from debugagent.pipeline.hypothesize import Proposal
    from debugagent.pipeline.types import Hypothesis

    return Proposal(
        hypotheses=[Hypothesis.from_dict({
            "ref": "H1", "hypothesis": "pool size too small for checkout traffic",
            "supporting_case_ids": [CITED], "relevance_state": "conditional",
            "refutation_conditions": ["pool is not saturated"], "recommended_next_step": "raise it"})],
        provider="fake", model="fake-model", fallback_used=False, attempts=[])


def _normalise(session_dict: dict) -> dict:
    """Strip the fields that differ by construction, so the comparison is about substance."""
    payload = json.loads(json.dumps(session_dict))
    payload.pop("session_id", None)
    if payload.get("memory"):
        payload["memory"].pop("recalled_at", None)
    return payload


def _answer_when_asked(engineer: WebEngineer, answers: list, *, timeout: float = 5.0) -> threading.Thread:
    """Answer each pending question in order, as a browser polling session would.

    Started as a thread because that is the point: the investigation is blocked while this runs.
    """

    def pump() -> None:
        for payload in answers:
            question = _await_question(engineer, timeout)
            if question is None:
                return
            try:
                if payload is None:
                    engineer.submit_resolution(None)
                elif "resolution" in payload:
                    engineer.submit_resolution(payload["resolution"])
                else:
                    engineer.submit_decision(payload)
            except ValueError:
                return

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    return thread


def _answer_all(engineer: WebEngineer, decision: dict, resolution: Any = None, *,
                timeout: float = 5.0) -> threading.Thread:
    """Give every decision question the same answer, then the resolution.

    Used where the point is "the engineer's answers", not "this particular hypothesis's answer" - the
    number of decision questions belongs to the proposal schema, not to the test.
    """

    def pump() -> None:
        while True:
            question = _await_question(engineer, timeout)
            if question is None:
                return
            try:
                if question.kind == "resolve":
                    engineer.submit_resolution(resolution)
                    return
                engineer.submit_decision(decision)
            except ValueError:
                return

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    return thread


def _await_question(engineer: WebEngineer, timeout: float):
    """Block until a question is published, or None if none arrives in time."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if engineer.awaiting:
            return engineer.pending
        time.sleep(0.01)
    return None


class NoNewDependencies(unittest.TestCase):
    """The UI adds no dependency. That was a decision, so it is a test rather than a claim."""

    def test_the_application_package_imports_only_stdlib_and_debugagent(self):
        for module in sorted(APP_DIR.glob("*.py")):
            tree = ast.parse(module.read_text(encoding="utf-8"))
            roots: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    roots.add(node.module.split(".")[0])
            for root in sorted(roots):
                with self.subTest(module=module.name, imported=root):
                    self.assertTrue(root in sys.stdlib_module_names or root == "debugagent",
                                    f"{module.name} imports third-party {root!r}")

    def test_the_server_uses_only_http_server(self):
        source = (APP_DIR / "web_server.py").read_text(encoding="utf-8")
        for framework in ("flask", "fastapi", "django", "uvicorn", "starlette", "aiohttp", "tornado"):
            self.assertNotIn(f"import {framework}", source.lower())

    def test_core_never_imports_the_application_layer(self):
        """Importing the flow must not drag in the UI, in either direction.

        Checked in a subprocess because `sys.modules` is interpreter-global: clearing it here would
        replace every `debugagent` module object for the rest of the run, and a test that quietly
        invalidates the tests after it is worse than no test at all. A fresh interpreter is also the
        honest place to ask what an import graph looks like from nothing.
        """
        import subprocess

        script = (
            "import sys; sys.path.insert(0, 'src');"
            "import debugagent.pipeline.investigate;"
            "loaded = [m for m in sys.modules if m.startswith('debugagent')];"
            "print([m for m in sorted(loaded) if 'application' in m])"
        )
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                                cwd=str(APP_DIR.parents[2]), timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "[]",
                         "importing the investigation flow pulled in the application layer")

    def test_the_application_layer_never_imports_a_core_stage(self):
        """The UI reads the session; it does not reach into the flow to drive it."""
        import subprocess

        script = (
            "import sys; sys.path.insert(0, 'src');"
            "import debugagent.application;"
            "print(sorted(m for m in sys.modules if m.startswith('debugagent.')))"
        )
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                                cwd=str(APP_DIR.parents[2]), timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        loaded = eval(result.stdout)  # a flat list of module names, printed by the child
        for forbidden in ("debugagent.agents", "debugagent.llm", "debugagent.memory",
                          "debugagent.composition", "debugagent.cli", "debugagent.domains"):
            self.assertNotIn(forbidden, loaded,
                             f"importing the application layer pulled in {forbidden}")


class EngineerCompatibility(unittest.TestCase):
    """The UI is another implementation of the protocol, not a special case in the flow."""

    def test_web_engineer_implements_every_protocol_method(self):
        for name in ("show", "current_facts", "decide", "resolve"):
            self.assertTrue(callable(getattr(WebEngineer, name, None)), f"missing {name}")

    def test_a_web_run_matches_a_terminal_run(self):
        """Same investigation, same answers, byte-identical session.

        This is the milestone's central claim. A UI that reached further than a terminal would change
        the result, and this fails the moment one does.
        """
        terminal_engineer = ScriptedEngineer(resolution=RESOLVED)
        terminal = investigate(_raw(), FakeMemoryPort(RELEVANT_VIEW), _llm(), terminal_engineer,
                               session_id="terminal")

        web = WebEngineer("web", timeout=5.0)
        _answer_all(web, ACCEPT, RESOLVED)
        served = investigate(_raw(), FakeMemoryPort(RELEVANT_VIEW), _llm(), web, session_id="web")

        self.assertEqual(_normalise(terminal.to_dict()), _normalise(served.to_dict()))
        self.assertEqual(terminal.verifications, served.verifications)
        self.assertEqual(terminal.resolution, served.resolution)

    def test_the_flow_showed_the_web_engineer_every_stage(self):
        web = WebEngineer("web", timeout=5.0)
        _answer_all(web, ACCEPT, RESOLVED)
        investigate(_raw(), FakeMemoryPort(), _llm(), web, session_id="web")
        self.assertGreaterEqual(len(web.transcript), 5, "the investigation barely said anything")

    def test_a_web_run_projects_into_the_view_model(self):
        web = WebEngineer("web", timeout=5.0)
        _answer_all(web, ACCEPT, RESOLVED)
        session = investigate(_raw(), FakeMemoryPort(), _llm(), web, session_id="web")
        model = build_view_model(session)
        for name in ("Memory", "Evidence", "Reasoning", "Validation", "Decision", "Trace"):
            self.assertIsNotNone(model.panel(name), f"a completed run has no {name} panel")
        self.assertIsNone(model.panel("Workers"), "no worker stage ran, so no Workers panel")

    def test_a_rejected_hypothesis_and_a_decline_are_carried_through(self):
        """The UI must be able to express every outcome, not only the happy one."""
        web = WebEngineer("web", timeout=5.0)
        _answer_all(web, {"decision": "reject", "note": "wrong layer"}, None)
        session = investigate(_raw(), FakeMemoryPort(), _llm(), web, session_id="web")
        self.assertEqual(session.verifications[0].engineer_decision, "reject")
        self.assertIsNone(session.resolution)
        self.assertEqual(web.outcome, "declined")


class DecisionLoop(unittest.TestCase):
    """The synchronous bridge: the investigation blocks, and the browser wakes it."""

    def test_a_decision_question_is_published_while_it_blocks(self):
        engineer = WebEngineer("s1", timeout=5.0)
        seen: list = []

        def answer():
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not engineer.awaiting:
                time.sleep(0.01)
            seen.append(engineer.pending)
            engineer.submit_decision("reject", note="no", confirmed=False)

        thread = threading.Thread(target=answer, daemon=True)
        thread.start()
        decision = engineer.decide(
            type("H", (), {"ref": "H1", "hypothesis": "the pool is too small"})(), [], ["region"])
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertIsNotNone(seen[-1], "the question was answered without ever being published")
        self.assertEqual(seen[-1].kind, "decide")
        self.assertEqual(seen[-1].hypothesis_ref, "H1")
        self.assertEqual(seen[-1].missing, ("region",))
        self.assertEqual(list(seen[-1].options), list(ENGINEER_DECISIONS))
        self.assertEqual(decision.decision, "reject")

    def test_the_question_carries_what_the_engineer_needs_to_answer(self):
        """Hiding the gaps would make the decision uninformed, which is worse than a slow one."""
        engineer = WebEngineer("s1", timeout=5.0)
        _answer_when_asked(engineer, [{"decision": "accept", "claim": "supported",
                                       "confirmed": True, "note": "n"}])
        decision = engineer.decide(
            type("H", (), {"ref": "H2", "hypothesis": "retries exhaust the pool"})(),
            ["proxy"], ["region"])
        self.assertEqual(decision.decision, "accept")
        self.assertEqual(decision.claim, "supported")
        self.assertTrue(decision.relevance_confirmed)
        self.assertEqual(decision.note, "n")

    def test_a_question_always_reports_its_gap_lists(self):
        """A client must be able to tell "no gaps" from "the server did not say".

        The gap lists are what make the decision informed, so they are unconditional - as `options` and
        `kind` already are.
        """
        for request in (DecisionRequest("decide", "s1"),
                        DecisionRequest("decide", "s1", "H1", "x", ("proxy",), ("region",)),
                        DecisionRequest("resolve", "s1")):
            with self.subTest(kind=request.kind, ref=request.hypothesis_ref):
                record = request.to_dict()
                self.assertEqual(record["mismatched"], list(request.mismatched))
                self.assertEqual(record["missing"], list(request.missing))
                self.assertIn("options", record)
                self.assertIn("kind", record)
                self.assertIn("session_id", record)

    def test_the_pending_question_clears_once_answered(self):
        engineer = WebEngineer("s1", timeout=5.0)
        _answer_when_asked(engineer, ["accept"])
        thread = threading.Thread(
            target=lambda: engineer.decide(type("H", (), {"ref": "H1", "hypothesis": "x"})(), [], []),
            daemon=True)
        thread.start()
        thread.join(timeout=2)
        self.assertIsNone(engineer.pending)
        self.assertFalse(engineer.awaiting)

    def test_facts_reach_the_evidence_stage(self):
        engineer = WebEngineer("s1", timeout=5.0)
        engineer.submit_facts({"Region": "eu-west-1", "service": "checkout"})
        facts = engineer.current_facts(None)
        self.assertEqual(facts, {"region": "eu-west-1", "service": "checkout"})

    def test_facts_cannot_overwrite_a_stated_fact(self):
        """The UI is offered the same refusal a terminal is: `add_facts` decides, not the caller."""
        from debugagent.pipeline.evidence import EvidenceError, add_facts
        from debugagent.pipeline.normalize import normalize

        engineer = WebEngineer("s1", timeout=5.0)
        engineer.submit_facts({"region": "us-east-1"})
        with self.assertRaises(EvidenceError) as caught:
            add_facts(build_evidence(normalize(_raw())), engineer.current_facts(None))
        self.assertIn("eu-west-1", str(caught.exception))

    def test_facts_may_fill_a_field_the_engineer_never_stated(self):
        from dataclasses import replace

        from debugagent.pipeline.evidence import add_facts
        from debugagent.pipeline.normalize import normalize

        engineer = WebEngineer("s1", timeout=5.0)
        engineer.submit_facts({"region": "us-east-1"})
        case = replace(normalize(_raw()), environment={"service": "checkout", "region": None})
        evidence = add_facts(build_evidence(case), engineer.current_facts(None))
        self.assertEqual({item.name: item.value for item in evidence.items}["region"], "us-east-1")

    def test_current_facts_ignores_the_evidence_it_is_handed(self):
        """Deriving the engineer's facts from the evidence would be inventing their answers."""
        engineer = WebEngineer("s1", timeout=5.0)
        evidence = type("E", (), {"items": [type("I", (), {"name": "region", "value": "us-east-1"})()]})()
        self.assertEqual(engineer.current_facts(evidence), {})


class TrustBoundary(unittest.TestCase):
    """What a browser may do, and what it may not."""

    def test_an_unknown_decision_is_refused_rather_than_guessed(self):
        engineer = WebEngineer("s1", timeout=5.0)
        with self.assertRaises(ValueError) as caught:
            engineer.submit_decision("probably_right")
        self.assertIn("probably_right", str(caught.exception))

    def test_an_illegal_claim_is_refused_at_the_boundary(self):
        """A claim is the engineer's reading of the evidence; only three readings exist.

        The refusal matters: an unreadable claim would otherwise become an `EngineerDecision` and fail
        later, inside `verify()`, as an error about a session the engineer never saw.
        """
        engineer = WebEngineer("s1", timeout=5.0)
        for claim in ("DEFINITELY", "supported-ish", "", 7):
            with self.subTest(claim=claim):
                with self.assertRaises(ValueError) as caught:
                    engineer.submit_decision({"decision": "accept", "claim": claim})
                self.assertIn("claim", str(caught.exception))

    def test_an_answer_arriving_with_nothing_pending_is_refused(self):
        engineer = WebEngineer("s1", timeout=5.0)
        with self.assertRaises(ValueError):
            engineer.submit_decision("accept")

    def test_a_double_submitted_answer_is_refused(self):
        """A double-clicked Accept button must not overwrite the answer already given.

        The window is real: the blocked `decide()` has not yet woken to clear the pending question, so
        a second POST still sees one. Overwriting here would let the engineer's second thoughts - or a
        different engineer's - silently replace their first answer, and the trace would not show it.
        """
        engineer = WebEngineer("s1", timeout=5.0)
        held: list = []

        def ask():
            held.append(engineer.decide(
                type("H", (), {"ref": "H1", "hypothesis": "x"})(), [], []))

        thread = threading.Thread(target=ask, daemon=True)
        thread.start()
        self.assertIsNotNone(_await_question(engineer, 5.0), "the question was never published")
        engineer.submit_decision("accept", note="first")
        with self.assertRaises(ValueError) as caught:
            engineer.submit_decision("reject", note="second")
        self.assertIn("already been answered", str(caught.exception))
        thread.join(timeout=3)
        self.assertEqual(held[0].decision, "accept", "the first answer was not the one that counted")
        self.assertEqual(held[0].note, "first")

    def test_an_answer_after_the_question_is_gone_is_refused(self):
        engineer = WebEngineer("s1", timeout=5.0)
        thread = threading.Thread(
            target=lambda: engineer.decide(type("H", (), {"ref": "H1", "hypothesis": "x"})(), [], []),
            daemon=True)
        thread.start()
        self.assertIsNotNone(_await_question(engineer, 5.0))
        engineer.submit_decision("accept")
        thread.join(timeout=3)
        with self.assertRaises(ValueError) as caught:
            engineer.submit_decision("reject")
        self.assertIn("no decision is pending", str(caught.exception))

    def test_a_stale_decision_cannot_settle_the_resolution(self):
        """The worst kind of stale answer: a decision landing on the resolution question.

        `resolve()` reads no `resolution` key from a decision payload, so without the guard it would
        read the engineer's answer as a refusal and end a resolved investigation as declined - a wrong
        conclusion, produced by an answer about a different question.
        """
        engineer = WebEngineer("s1", timeout=5.0)
        held: list = []

        def ask():
            held.append(engineer.resolve(None))

        thread = threading.Thread(target=ask, daemon=True)
        thread.start()
        self.assertIsNotNone(_await_question(engineer, 5.0))
        with self.assertRaises(ValueError) as caught:
            engineer.submit_decision("accept")
        self.assertIn("resolve", str(caught.exception))
        engineer.submit_resolution(RESOLVED)
        thread.join(timeout=3)
        self.assertEqual(held[0]["action_taken"], RESOLVED["action_taken"])

    def test_a_stale_resolution_cannot_settle_a_decision(self):
        engineer = WebEngineer("s1", timeout=5.0)
        held: list = []

        def ask():
            held.append(engineer.decide(
                type("H", (), {"ref": "H1", "hypothesis": "x"})(), [], []))

        thread = threading.Thread(target=ask, daemon=True)
        thread.start()
        self.assertIsNotNone(_await_question(engineer, 5.0))
        with self.assertRaises(ValueError) as caught:
            engineer.submit_resolution(RESOLVED)
        self.assertIn("does not answer it", str(caught.exception))
        engineer.submit_decision("reject")
        thread.join(timeout=3)
        self.assertEqual(held[0].decision, "reject")

    def test_a_timeout_is_a_refusal_not_an_answer(self):
        """A fabricated 'reject' is indistinguishable from a real one downstream, so none is made."""
        engineer = WebEngineer("s1", timeout=0.15)
        errors: list[BaseException] = []

        def ask():
            try:
                engineer.decide(type("H", (), {"ref": "H1", "hypothesis": "x"})(), [], [])
            except BaseException as exc:  # noqa: BLE001 - recording what a blocked caller sees
                errors.append(exc)

        thread = threading.Thread(target=ask, daemon=True)
        thread.start()
        thread.join(timeout=3)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], DecisionTimeout)
        self.assertIsNone(engineer.pending, "a timed-out question is still pending")

    def test_abandoning_does_not_produce_a_decision(self):
        engineer = WebEngineer("s1", timeout=5.0)
        errors: list[BaseException] = []

        def ask():
            try:
                engineer.decide(type("H", (), {"ref": "H1", "hypothesis": "x"})(), [], [])
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        thread = threading.Thread(target=ask, daemon=True)
        thread.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not engineer.awaiting:
            time.sleep(0.01)
        engineer.abandon()
        thread.join(timeout=3)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], DecisionTimeout)

    def test_a_finished_session_releases_a_blocked_caller(self):
        engineer = WebEngineer("s1", timeout=5.0)
        errors: list[BaseException] = []

        def ask():
            try:
                engineer.decide(type("H", (), {"ref": "H1", "hypothesis": "x"})(), [], [])
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        thread = threading.Thread(target=ask, daemon=True)
        thread.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not engineer.awaiting:
            time.sleep(0.01)
        engineer.finish("declined")
        thread.join(timeout=3)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], DecisionTimeout)

    def test_the_browser_cannot_name_a_verification_status(self):
        """A status is derived from evidence plus a validated decision; there is no setter to call."""
        for attribute in ("set_status", "status", "verify", "set_verification", "add_evidence",
                          "dispatch", "delegate", "retain", "session"):
            self.assertFalse(hasattr(WebEngineer("s1"), attribute),
                             f"WebEngineer exposes {attribute!r}, which reaches past the protocol")

    def test_an_illegal_claim_is_rejected_by_verify(self):
        """The browser cannot assert a status. It states a reading, and the gate decides the status."""
        from debugagent.pipeline.normalize import normalize
        from debugagent.pipeline.recall_match import recall
        from debugagent.pipeline.verify import VerificationError, verify

        case = normalize(_raw())
        # A recalled case whose environment is fully covered by current evidence, so the gate reaches
        # the claim itself. With a gap present it would short-circuit to insufficient_evidence and this
        # test would pass for the wrong reason.
        context = recall(FakeMemoryPort(view([candidate(CITED, environment={"service": "checkout"})])), case)
        proposal = _proposal()
        with self.assertRaises(VerificationError) as caught:
            verify(proposal.hypotheses, build_evidence(case), context,
                   {"H1": EngineerDecision("accept", "DEFINITELY", True, "note")})
        # The message names the hypothesis and what was needed, and does not echo the rejected value
        # back - error text is somewhere untrusted input reaches a log.
        self.assertIn("H1", str(caught.exception))
        self.assertNotIn("DEFINITELY", str(caught.exception))

    def test_a_claim_cannot_outrank_missing_evidence(self):
        """A browser claiming 'supported' still cannot promote a status when nothing is known."""
        from dataclasses import replace

        from debugagent.pipeline.normalize import normalize
        from debugagent.pipeline.recall_match import recall
        from debugagent.pipeline.verify import verify

        case = normalize(_raw())
        context = recall(FakeMemoryPort(), case)
        proposal = _proposal()
        # Keep only the fields the engineer never stated, so `known()` finds nothing.
        evidence = build_evidence(case)
        evidence = replace(evidence, items=[item for item in evidence.items if item.value is None])
        results = verify(proposal.hypotheses, evidence, context,
                         {"H1": EngineerDecision("accept", "supported", True, "note")})
        self.assertEqual(results[0].status, "insufficient_evidence")


class ServerHarness(unittest.TestCase):
    """A real server on a real loopback port, with a fake session factory."""

    def setUp(self):
        self.registry = SessionRegistry()
        self.counter = 0
        self.started: list[str] = []
        self.server = make_server(self.registry, self._build, host="127.0.0.1", port=0)
        self.host, self.port = self.server.server_address
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._shutdown)

    def _shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def _build(self, handler, description, options):
        """Stand in for the composition root: build the objects and start the thread."""
        self.counter += 1
        session_id = f"session-{self.counter}"
        self.started.append(description)
        engineer = WebEngineer(session_id, timeout=5.0)
        record = self.registry.create(session_id, engineer)
        raw = DebugInput(description, ["a symptom"], {"service": "checkout"})
        port = FakeMemoryPort()
        if options.get("fail") or options.get("silent"):
            # A run that dies at its first step asks the engineer nothing at all, which is how a test
            # reaches the "nothing is pending" state without racing the decision loop.
            kwargs = {"on_step": lambda session: (_ for _ in ()).throw(RuntimeError("boom"))}
        else:
            kwargs = {}
        thread = threading.Thread(
            target=_run_investigation, args=(record,),
            kwargs={"raw": raw, "port": port, "llm": _llm(), "kwargs": kwargs}, daemon=True)
        thread.start()
        return session_id, record, thread

    # -- HTTP helpers ------------------------------------------------------
    def get(self, path):
        conn = HTTPConnection(self.host, self.port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def post(self, path, payload):
        body = json.dumps(payload).encode("utf-8")
        conn = HTTPConnection(self.host, self.port, timeout=5)
        try:
            conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def post_raw(self, path, body: bytes):
        conn = HTTPConnection(self.host, self.port, timeout=5)
        try:
            conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def json_get(self, path):
        status, body = self.get(path)
        return status, json.loads(body)

    def json_post(self, path, payload):
        status, body = self.post(path, payload)
        return status, json.loads(body)


class Endpoints(ServerHarness):
    """The HTTP surface."""

    def test_the_application_document_is_served_inline(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn("Engineering Debugger", html)
        for panel in ("Memory", "Evidence", "Workers", "Reasoning", "Validation", "Decision", "Trace"):
            self.assertNotIn(f">{panel}<", html, "panels should be rendered from data, not hard-coded")

    def test_health_reports_the_offerable_decisions(self):
        status, payload = self.json_get("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["decisions"], list(ENGINEER_DECISIONS))
        self.assertEqual(payload["sessions"], [])

    def test_starting_a_session_returns_immediately_and_the_session_is_observable(self):
        """The run must not hold the request open; the engineer polls while it works."""
        status, payload = self.json_post("/api/sessions", {"description": SYMPTOM})
        self.assertEqual(status, 202)
        session_id = payload["session_id"]
        self.assertEqual(self.started, [SYMPTOM])

        status, snapshot = self.json_get(f"/api/sessions/{session_id}")
        self.assertEqual(status, 200)
        self.assertIn(snapshot["status"], ("running", "complete"))
        self.assertIn("pending", snapshot)
        self.assertIn("outcome", snapshot)

    def test_an_empty_description_is_refused(self):
        status, payload = self.json_post("/api/sessions", {"description": "   "})
        self.assertEqual(status, 400)
        self.assertIn("description", payload["error"])

    def test_a_running_investigation_publishes_what_it_has_so_far(self):
        """The console must show progress, not a blank page until the run ends.

        `investigate()` calls `on_step(session)` with the same Session it is mutating, so the server can
        project a partial investigation. Without it the browser can only ever show a decision question
        with no investigation behind it, because no session is returned until the run finishes.
        """
        _, payload = self.json_post("/api/sessions", {"description": SYMPTOM})
        session_id = payload["session_id"]
        seen = set()
        for _ in range(400):
            _, snapshot = self.json_get(f"/api/sessions/{session_id}")
            if snapshot["status"] != "running":
                break
            if snapshot["view"] is not None:
                seen.update(panel["name"] for panel in snapshot["view"]["panels"])
            if snapshot["pending"] is not None and snapshot["view"] is not None:
                break
            time.sleep(0.02)
        self.assertTrue(seen, "nothing was ever projected while the run was in flight")
        self.assertIn("Reasoning", seen,
                      "the hypotheses were not shown before the run finished")
        self.assertNotIn("Decision", seen, "a decision was recorded before the engineer gave one")

    def test_a_running_investigation_never_claims_a_verification_status(self):
        _, payload = self.json_post("/api/sessions", {"description": SYMPTOM})
        session_id = payload["session_id"]
        for _ in range(400):
            _, snapshot = self.json_get(f"/api/sessions/{session_id}")
            view = snapshot["view"]
            if view is not None and any(p["name"] == "Validation" for p in view["panels"]):
                break
            if snapshot["status"] != "running":
                break
            time.sleep(0.02)
        validation = next((p for p in (view or {"panels": []})["panels"]
                          if p["name"] == "Validation"), None)
        if validation is not None:
            for item in validation["items"]:
                self.assertIn(item["value"], ("accept", "modify", "reject", None),
                              "a status was published before the engineer decided")

    def test_an_unknown_session_is_a_404(self):
        status, payload = self.json_get("/api/sessions/nope")
        self.assertEqual(status, 404)
        self.assertIn("nope", payload["error"])

    def test_an_unknown_path_is_a_404(self):
        for path in ("/nope", "/api/nope", "/api/sessions/s1/nope"):
            with self.subTest(path=path):
                if path.endswith("/nope") and path.startswith("/api/sessions"):
                    status, _ = self.post(path, {})
                else:
                    status, _ = self.get(path)
                self.assertEqual(status, 404)

    def test_malformed_json_is_a_400_and_not_a_crash(self):
        status, body = self.post_raw("/api/sessions", b"{not json")
        self.assertEqual(status, 400)
        self.assertIn("JSON", json.loads(body)["error"])
        self.assertEqual(self.json_get("/api/health")[0], 200, "the server did not survive")

    def test_a_non_object_body_is_refused(self):
        status, body = self.post_raw("/api/sessions", b'"just a string"')
        self.assertEqual(status, 400)
        self.assertIn("object", json.loads(body)["error"])

    def test_an_oversized_body_is_refused(self):
        conn = HTTPConnection(self.host, self.port, timeout=5)
        try:
            conn.putrequest("POST", "/api/sessions")
            conn.putheader("Content-Length", str(5_000_000))
            conn.putheader("Content-Type", "application/json")
            conn.endheaders()
            response = conn.getresponse()
            self.assertEqual(response.status, 400)
            self.assertIn("too large", json.loads(response.read())["error"])
        finally:
            conn.close()

    def test_facts_are_accepted_and_reflected(self):
        _, payload = self.json_post("/api/sessions", {"description": SYMPTOM})
        session_id = payload["session_id"]
        status, payload = self.json_post(f"/api/sessions/{session_id}/facts",
                                         {"facts": {"Region": "eu-west-1"}})
        self.assertEqual(status, 200)
        self.assertEqual(payload["facts"], {"region": "eu-west-1"})

    def test_a_malformed_fact_payload_is_refused(self):
        _, payload = self.json_post("/api/sessions", {"description": SYMPTOM})
        session_id = payload["session_id"]
        for facts, why in (("not an object", "a string"), ({"region": 5}, "a non-string value"),
                           ({"": "x"}, "an empty key")):
            with self.subTest(facts=facts):
                status, body = self.json_post(f"/api/sessions/{session_id}/facts", {"facts": facts})
                self.assertEqual(status, 400, why)
                self.assertIn("facts", body["error"])

    def test_a_decision_with_nothing_pending_is_refused(self):
        """Deterministic: a run that dies at its first step never asks the engineer anything."""
        _, payload = self.json_post("/api/sessions", {"description": SYMPTOM, "options": {"silent": True}})
        session_id = payload["session_id"]
        self._await_status(session_id, "failed")
        status, body = self.json_post(f"/api/sessions/{session_id}/decision", {"decision": "accept"})
        self.assertEqual(status, 400)
        self.assertIn("no decision is pending", body["error"])

    def test_an_illegal_decision_is_refused(self):
        _, payload = self.json_post("/api/sessions", {"description": SYMPTOM, "options": {"silent": True}})
        session_id = payload["session_id"]
        self._await_status(session_id, "failed")
        status, body = self.json_post(f"/api/sessions/{session_id}/decision",
                                      {"decision": "ship_it"})
        self.assertEqual(status, 400)
        self.assertIn("ship_it", body["error"])

    def test_a_failing_run_is_reported_rather_than_swallowed(self):
        _, payload = self.json_post("/api/sessions", {"description": SYMPTOM, "options": {"fail": True}})
        snapshot = self._await_status(payload["session_id"], "failed")
        self.assertIn("boom", snapshot["error"])
        self.assertEqual(snapshot["view"], None, "a failed run has no session to project")
        self.assertEqual(snapshot["outcome"], "declined")

    def test_a_full_engineered_run_reaches_a_complete_snapshot(self):
        """End to end over HTTP: start, poll until a question appears, answer, resolve."""
        _, payload = self.json_post("/api/sessions", {"description": SYMPTOM})
        session_id = payload["session_id"]

        # Every hypothesis is asked about, and how many there are belongs to the proposal schema.
        asked = 0
        while True:
            pending = self._await_pending(session_id, "decide")
            self.assertEqual(pending["kind"], "decide")
            self.assertEqual(pending["options"], list(ENGINEER_DECISIONS))
            self.assertIn("hypothesis_ref", pending)
            self.assertTrue(pending["statement"], "the question does not show the hypothesis")
            status, _ = self.json_post(f"/api/sessions/{session_id}/decision", dict(ACCEPT))
            self.assertEqual(status, 200)
            asked += 1
            if asked >= 2 or self._resolve_is_next(session_id):
                break
        self.assertGreaterEqual(asked, 1)

        pending = self._await_pending(session_id, "resolve")
        self.assertEqual(pending["kind"], "resolve")
        status, _ = self.json_post(f"/api/sessions/{session_id}/decision", {"decision": {"resolution": RESOLVED}})
        self.assertEqual(status, 200)

        snapshot = self._await_complete(session_id)
        self.assertEqual(snapshot["status"], "complete")
        self.assertEqual(snapshot["outcome"], "resolved")
        self.assertIsNone(snapshot["pending"])
        names = [panel["name"] for panel in snapshot["view"]["panels"]]
        self.assertIn("Decision", names)
        self.assertIn("Trace", names)
        self.assertNotIn("Workers", names)

    def test_a_browser_can_decline_and_the_run_ends_unresolved(self):
        _, payload = self.json_post("/api/sessions", {"description": SYMPTOM})
        session_id = payload["session_id"]
        self._answer_every_decision(session_id, {"decision": "reject"})
        self._await_pending(session_id, "resolve")
        self.json_post(f"/api/sessions/{session_id}/decision", {"decision": {"resolution": None}})
        snapshot = self._await_complete(session_id)
        self.assertEqual(snapshot["status"], "complete")
        self.assertEqual(snapshot["outcome"], "declined")
        self.assertEqual(snapshot["view"]["status"], "complete",
                         "an unresolved run still completed; it simply concluded nothing")
        decisions = [item["label"] for panel in snapshot["view"]["panels"] if panel["name"] == "Decision"
                     for item in panel["items"] if item.get("source") == "engineer"]
        self.assertIn("reject", decisions)

    def test_abandoning_releases_the_run_without_answering(self):
        _, payload = self.json_post("/api/sessions", {"description": SYMPTOM})
        session_id = payload["session_id"]
        self._await_pending(session_id, "decide")
        status, _ = self.json_post(f"/api/sessions/{session_id}/abandon", {})
        self.assertEqual(status, 200)
        # Wait for the run to reach a terminal state before inspecting the engineer: abandoning releases
        # the blocked `decide()`, and the pending question is cleared by that thread waking up, not by
        # the request returning.
        snapshot = self._await_status(session_id, "failed")
        self.assertIn("abandoned", snapshot["error"])
        record = self.registry.get(session_id)
        self.assertIsNone(record.engineer.pending, "the abandoned question is still pending")
        self.assertFalse(record.engineer.awaiting)

    def test_the_session_list_reflects_what_was_started(self):
        self.assertEqual(self.json_get("/api/sessions")[1]["sessions"], [])
        _, payload = self.json_post("/api/sessions", {"description": SYMPTOM})
        self.assertEqual(self.json_get("/api/sessions")[1]["sessions"], [payload["session_id"]])
        self.assertEqual(self.json_get("/api/health")[1]["sessions"], [payload["session_id"]])

    # -- polling helpers ---------------------------------------------------
    def _answer_every_decision(self, session_id, payload, *, limit=10):
        """Answer decision questions until the run moves on to the resolution."""
        for _ in range(limit):
            self._await_pending(session_id, "decide")
            status, body = self.json_post(f"/api/sessions/{session_id}/decision", payload)
            self.assertEqual(status, 200, body)
            if self._resolve_is_next(session_id):
                return
        self.fail(f"the run was still asking for decisions after {limit} answers")

    def _resolve_is_next(self, session_id):
        _, snapshot = self.json_get(f"/api/sessions/{session_id}")
        pending = snapshot.get("pending")
        return bool(pending and pending["kind"] == "resolve")

    def _await_pending(self, session_id, kind, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _, snapshot = self.json_get(f"/api/sessions/{session_id}")
            pending = snapshot.get("pending")
            if pending and pending["kind"] == kind:
                return pending
            if snapshot["status"] == "failed":
                self.fail(f"the run failed while waiting for '{kind}': {snapshot.get('error')}")
            time.sleep(0.02)
        self.fail(f"no '{kind}' question appeared within {timeout}s")

    def _await_status(self, session_id, status, timeout=5.0):
        deadline = time.monotonic() + timeout
        snapshot = {}
        while time.monotonic() < deadline:
            _, snapshot = self.json_get(f"/api/sessions/{session_id}")
            if snapshot["status"] == status:
                return snapshot
            time.sleep(0.02)
        self.fail(f"the run never reached '{status}' within {timeout}s; it is '{snapshot.get('status')}'")

    def _await_complete(self, session_id, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _, snapshot = self.json_get(f"/api/sessions/{session_id}")
            if snapshot["status"] != "running":
                return snapshot
            time.sleep(0.02)
        self.fail(f"the run did not finish within {timeout}s")


class BoundedRegistry(unittest.TestCase):
    """Sessions live in memory, so the process needs a bound."""

    def test_the_registry_refuses_past_its_limit(self):
        registry = SessionRegistry(limit=2)
        for index in range(2):
            registry.create(f"s{index}", WebEngineer(f"s{index}"))
        with self.assertRaises(RuntimeError) as caught:
            registry.create("s2", WebEngineer("s2"))
        self.assertIn("session limit", str(caught.exception))

    def test_the_default_limit_is_bounded(self):
        self.assertGreater(MAX_SESSIONS, 0)
        self.assertLessEqual(MAX_SESSIONS, 1024)

    def test_serve_refuses_to_start_without_a_session_factory(self):
        """The server does not know how to compose a session; that is the composition root's job."""
        with self.assertRaises(ValueError) as caught:
            serve()
        self.assertIn("build_investigation", str(caught.exception))


class LocalOnly(unittest.TestCase):
    """Loopback is the security boundary; there is no authentication, so it has to hold."""

    def test_the_default_host_is_loopback(self):
        from debugagent.application import web_server
        from debugagent.application.console import CONSOLE_HTML

        self.assertEqual(web_server.DEFAULT_HOST, "127.0.0.1")
        self.assertNotIn("0.0.0.0", CONSOLE_HTML)


if __name__ == "__main__":
    unittest.main()
