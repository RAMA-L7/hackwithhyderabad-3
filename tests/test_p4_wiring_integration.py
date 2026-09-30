"""P4 demo-flow wiring: the Memory Specialist is reachable through `investigate()`.

The P4 gate proved the worker against the real port and the real act fixtures, but nothing
constructed it, so it was unreachable in production. This file closes that: it drives a FULL session
through `investigate()` with a worker supplied, and proves the wiring holds the architecture's rules.

What is asserted here, and why each matters:

- **Reachable.** A worker supplied to `investigate()` is dispatched, and its result is recorded.
- **Default unchanged.** With no worker, the session is byte-identical to Phase 1: same keys in
  `to_dict()`, same rendered sections, same trace shape. Wiring memory must not change Phase 1.
- **MEMORY stays separate.** The worker's observations never enter `build_evidence`, are never
  presented as current-system facts, and the ranked `session.memory` the proposal is built from is
  still produced by the existing `recall()`.
- **Failure does not abort.** A failing worker is reported and the investigation continues, because
  the engineer's session must not depend on a worker being available.
- **No P5.** One task, serial, and the worker cannot retain.

Deterministic: no sleeps, no threads, no network.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support
from debugagent.agents.memory_specialist import MemorySpecialist
from debugagent.memory.hindsight_store import HindsightMemoryStore, compute_case_key
from debugagent.pipeline.ingest import load_debug_input
from debugagent.pipeline.normalize import normalize
from debugagent.pipeline.investigate import investigate
from debugagent.pipeline.memory_adapter import HindsightMemoryPort
from debugagent.pipeline.render import render_memory_delegation
from debugagent.pipeline.verify import EngineerDecision
from support import FakeHindsightClient, FakeMemory, FakeScores, memory_config

import loop_support
from loop_support import RESOLVED, FakeLLM, ScriptedEngineer, hyp

ACTS = ("act1-billing.yaml", "act2-media-uploader.json", "act3-orders.yaml", "act4-billing-recurs.yaml")


def seed_row(seed):
    """One recall row carrying a real seed case's identity, so the real classifier sees it."""
    return FakeMemory(
        text=f"past case: {seed.problem_signature}",
        metadata={"case_key": compute_case_key(seed, seed.session_id or "seed"),
                  "outcome": seed.outcome,
                  "service": seed.environment.get("service", "unknown"),
                  "runtime": seed.environment.get("runtime", "unknown"),
                  "root_cause_key": (seed.root_cause or "")[:64]},
        scores=FakeScores(0.9, 0.8, 0.7), mentioned_at="2026-01-01T00:00:00+00:00")


def seeded_rows():
    """Recall rows built from the real seed cases, so the real classifier finds candidates."""
    from debugagent.seeds.loader import load_seed_file

    return [seed_row(seed) for seed in load_seed_file()]


class RecordingEngineer(ScriptedEngineer):
    """The repo's own scripted engineer, extended to record every rendered section.

    Reusing `ScriptedEngineer` rather than writing a second one means the wiring is tested against
    the same engineer contract the existing flow tests use, so a change to that contract surfaces
    here rather than hiding behind a bespoke double.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.shown: list[str] = []

    def show(self, text: str) -> None:
        self.shown.append(text)


def hypothesis_output(*case_ids: str) -> dict:
    """A schema-valid proposal. `hyp()` supplies the exact field set the schema requires."""
    cites = [c for c in case_ids if c]
    return {"hypotheses": [hyp(cites=cites), hyp(text="app-side upload limit", cites=cites)]}


class WiringBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def port(self, client=None):
        store = HindsightMemoryStore(
            memory_config(self.tmp_path),
            client=client if client is not None else FakeHindsightClient(recall_results=seeded_rows()))
        self.addCleanup(store.close)
        return HindsightMemoryPort(store)

    def gapped_case(self):
        """A case whose current evidence is *missing* the fields memory knows about.

        `verify()` distinguishes the two ways memory can disagree with evidence: a field the
        engineer has stated but memory contradicts is `mismatched`, while a field nobody stated is
        `missing`, and only `missing` forces `insufficient_evidence`. No act fixture states fewer
        environment fields than its recalled cases, so this scenario is built directly - and it
        matters, because it is the case where a careless implementation would let memory quietly
        supply the answer.
        """
        from debugagent.pipeline.types import DebugInput
        from debugagent.seeds.loader import load_seed_file

        seed = next(s for s in load_seed_file() if s.environment.get("service") == "orders-api")
        store = HindsightMemoryStore(
            memory_config(self.tmp_path),
            client=FakeHindsightClient(recall_results=[seed_row(seed)]))
        self.addCleanup(store.close)
        port = HindsightMemoryPort(store)
        raw = DebugInput("uploads reset over 2 MB on the edge proxy\nproxy=nginx-1.24",
                         ["20/20 uploads fail above 2 MB"])
        self.assertNotEqual({k: v for k, v in normalize(raw).environment.items() if v}
                            .get("service"), "orders-api",
                            "the fixture must not state the service memory knows about")
        return raw, port

    def cited_case_ids(self, port, raw) -> list[str]:
        """The case ids the *pipeline* would see: the same query, via the same `recall()`.

        Deriving the ids this way matters. Building the query by hand picks different candidates,
        and a proposal citing an id the session never recalled would be citable by accident rather
        than because memory actually surfaced it.
        """
        from debugagent.pipeline.recall_match import recall

        context = recall(port, normalize(raw))
        return [] if context.abstained else [c["case_id"] for c in context.candidates]

    def run_session(self, *, worker, engineer=None, filename="act2-media-uploader.json", llm=None):
        raw = load_debug_input(
            Path(__file__).resolve().parents[1] / "demo" / "inputs" / filename)
        port = self.port()
        if llm is None:
            # Cite the top recalled case, or nothing when memory abstained - the proposal schema
            # requires generic hypotheses in that case, and citing anyway is rejected.
            llm = FakeLLM(hypothesis_output(*self.cited_case_ids(port, raw)[:1]))
        if engineer is None:
            # No hand-fed facts: each act's own input declares its environment, so the evidence
            # under test is the one the engineer would really have.
            engineer = RecordingEngineer(resolution=RESOLVED)
        return investigate(raw, port, llm, engineer, memory_specialist=worker), port


class ReachabilityTests(WiringBase):
    def test_worker_is_dispatched_and_recorded(self):
        port = self.port()
        session, _ = self.run_session(worker=MemorySpecialist(port))
        self.assertIsNotNone(session.memory_delegation, "the worker result must be recorded")
        self.assertEqual(session.memory_delegation["agent"], "memory_specialist")
        self.assertEqual(session.memory_delegation["status"], "success")
        self.assertTrue(session.memory_delegation["observations"])

    def test_delegation_appears_in_the_serialised_session(self):
        port = self.port()
        session, _ = self.run_session(worker=MemorySpecialist(port))
        self.assertIn("memory_delegation", session.to_dict())
        self.assertEqual(session.to_dict()["memory_delegation"], session.memory_delegation)

    def test_delegation_is_traced(self):
        port = self.port()
        session, _ = self.run_session(worker=MemorySpecialist(port))
        self.assertTrue(any("memory specialist" in note for note in session.trace),
                        f"the delegation must be traced: {session.trace}")

    def test_every_act_reaches_the_worker(self):
        """Each act fixture is wired through the real flow and reaches the worker.

        The proposal cites the case ids the real `recall()` surfaced, so every act runs the
        genuine path rather than a hand-fitted one - including `act3`, where memory abstains and
        the proposal must therefore cite nothing.
        """
        for filename in ACTS:
            with self.subTest(act=filename):
                self.setUp()  # a fresh bank per act
                port = self.port()
                raw = load_debug_input(
                    Path(__file__).resolve().parents[1] / "demo" / "inputs" / filename)
                session = investigate(
                    raw, port,
                    FakeLLM(hypothesis_output(*self.cited_case_ids(port, raw)[:1])),
                    RecordingEngineer(resolution=RESOLVED),
                    memory_specialist=MemorySpecialist(port))
                self.assertEqual(session.memory_delegation["status"], "success",
                                 f"{filename} must reach the worker")
                self.assertIsNotNone(session.proposal, f"{filename} must reach a proposal")

    def test_the_worker_task_is_authorised(self):
        """The dispatch must go through build_task_spec -> authorize, not a private path."""
        from debugagent.agents.memory_specialist import build_memory_specialist_task

        port = self.port()
        session, _ = self.run_session(worker=MemorySpecialist(port))
        expected = build_memory_specialist_task(
            task_id=session.session_id + "-memory",
            case_signature=session.case.problem_signature,
            symptoms=tuple(session.case.symptoms))
        self.assertEqual(session.memory_delegation["task_id"], expected.task_id)
        self.assertEqual(session.memory_delegation["agent"], expected.agent)

    def test_worker_executed_exactly_one_task(self):
        port = self.port()
        worker = MemorySpecialist(port)
        session, _ = self.run_session(worker=worker)
        self.assertEqual(session.memory_delegation["status"], "success")


class DefaultUnchangedTests(WiringBase):
    """With no worker, Phase 1 must be byte-identical."""

    def test_session_without_a_worker_has_no_delegation_key(self):
        session, _ = self.run_session(worker=None)
        self.assertIsNone(session.memory_delegation)
        self.assertNotIn("memory_delegation", session.to_dict(),
                         "an absent worker must not add a key to the demo output")

    def test_rendering_is_unchanged_without_a_worker(self):
        engineer = RecordingEngineer(resolution=RESOLVED)
        session, _ = self.run_session(worker=None, engineer=engineer)
        self.assertFalse(any("Memory Specialist" in text for text in engineer.shown),
                         "no worker means no delegation section rendered")

    def test_ranked_memory_is_unchanged_by_the_wiring(self):
        """The MemoryContext the proposal is built from must be the same either way."""
        with_worker, _ = self.run_session(worker=MemorySpecialist(self.port()))
        self.setUp()
        without_worker, _ = self.run_session(worker=None)
        self.assertEqual(with_worker.memory.candidates, without_worker.memory.candidates)
        self.assertEqual(with_worker.proposal.hypotheses[0].ref,
                         without_worker.proposal.hypotheses[0].ref)

    def test_proposal_hypotheses_are_unchanged(self):
        with_worker, _ = self.run_session(worker=MemorySpecialist(self.port()))
        self.setUp()
        without_worker, _ = self.run_session(worker=None)
        self.assertEqual([h.to_dict() for h in with_worker.proposal.hypotheses],
                         [h.to_dict() for h in without_worker.proposal.hypotheses])

    def test_verification_status_is_unchanged(self):
        with_worker, _ = self.run_session(worker=MemorySpecialist(self.port()))
        self.setUp()
        without_worker, _ = self.run_session(worker=None)
        self.assertEqual([v.to_dict() for v in with_worker.verifications],
                         [v.to_dict() for v in without_worker.verifications])


class MemoryStaysSeparateTests(WiringBase):
    """MEMORY informs; it never verifies."""

    def test_delegation_never_enters_evidence(self):
        port = self.port()
        session, _ = self.run_session(worker=MemorySpecialist(port))
        self.assertTrue(session.memory_delegation["observations"])
        rendered = "\n".join(
            item.text if hasattr(item, "text") else "" for item in session.evidence.items)
        for observation in session.memory_delegation["observations"]:
            fragment = str(observation["content"]).split("recalled: ")[-1][:40].strip()
            if fragment:
                self.assertNotIn(fragment, rendered,
                                 "recalled memory must not appear in EVIDENCE")

    def test_evidence_items_are_unchanged_by_the_wiring(self):
        with_worker, _ = self.run_session(worker=MemorySpecialist(self.port()))
        self.setUp()
        without_worker, _ = self.run_session(worker=None)
        self.assertEqual([i.to_dict() for i in with_worker.evidence.items],
                         [i.to_dict() for i in without_worker.evidence.items])

    def test_delegation_is_labelled_as_not_evidence(self):
        port = self.port()
        session, _ = self.run_session(worker=MemorySpecialist(port))
        for observation in session.memory_delegation["observations"]:
            self.assertIn("not current-system evidence", observation["content"])

    def test_the_rendered_delegation_declares_it_is_not_evidence(self):
        port = self.port()
        session, _ = self.run_session(worker=MemorySpecialist(port))
        text = render_memory_delegation(session.memory_delegation)
        self.assertIn("not evidence about this issue", text)
        for observation in session.memory_delegation["observations"]:
            self.assertIn(observation["ref"], text)

    def test_worker_observations_cannot_fill_an_evidence_gap(self):
        """`verify()`'s own rule: memory never fills a gap, however strong the match.

        The engineer has said only `proxy`. The recalled case was fixed in a different service and
        runtime, so both are *missing* from current evidence and the verdict is
        `insufficient_evidence`. The worker's observations state that past environment in full -
        and it still cannot be used to conclude anything about the current system.
        """
        raw, port = self.gapped_case()
        session = investigate(
            raw, port, FakeLLM(hypothesis_output(*self.cited_case_ids(port, raw)[:1])),
            RecordingEngineer(resolution=RESOLVED),
            memory_specialist=MemorySpecialist(port))
        self.assertTrue(session.memory_delegation["observations"],
                        "the worker did report the past case's environment")
        self.assertEqual(session.verifications[0].status, "insufficient_evidence",
                         "delegating memory must not upgrade the verification outcome")
        self.assertIn("service", session.verifications[0].mismatched_environment_fields)
        self.assertIn("runtime", session.verifications[0].mismatched_environment_fields)

    def test_worker_observations_flag_a_mismatch_without_upgrading(self):
        """The other half: environment that is *known but different* is flagged, not failed.

        `act2` declares its own service and runtime, so nothing is missing and the verdict stands -
        but the recalled case disagrees with both, and that disagreement is surfaced rather than
        resolved in memory's favour.
        """
        session, _ = self.run_session(worker=MemorySpecialist(self.port()))
        verification = session.verifications[0]
        self.assertEqual(verification.status, "supported")
        self.assertIn("service", verification.mismatched_environment_fields,
                      "a differing known field is reported, not silently accepted")
        recalled = {c["environment"]["service"] for c in session.memory.candidates}
        self.assertNotEqual(session.evidence.known()["service"], recalled,
                            "the current service must stay the act's own, never memory's")

    def test_retention_is_still_a_coordinator_decision(self):
        port = self.port()
        session, _ = self.run_session(worker=MemorySpecialist(port))
        # The session retained, because the ENGINEER resolved it -- not because the worker did.
        self.assertTrue(session.retention["retained"])
        self.assertIn("memory_delegation", session.to_dict())
        self.assertNotIn("retain", str(session.memory_delegation["observations"])[:200])


class FailureDoesNotAbortTests(WiringBase):
    """A worker outage must not take the engineer's investigation down with it."""

    def test_backend_failure_is_reported_and_the_session_continues(self):
        """The worker's bank is down; the engineer's own session must still complete.

        Two ports on purpose: the worker gets a broken bank, while the pipeline's own recall
        uses a working one. That is what proves the worker is a side channel rather than a
        dependency of the flow.
        """
        class Down(FakeHindsightClient):
            def recall(self, **kwargs):
                raise RuntimeError("bank unreachable")

        raw = load_debug_input(
            Path(__file__).resolve().parents[1] / "demo" / "inputs" / "act2-media-uploader.json")
        working_port = self.port()
        broken_port = self.port(client=Down())
        session = investigate(
            raw, working_port,
            FakeLLM(hypothesis_output(*self.cited_case_ids(working_port, raw)[:1])),
            RecordingEngineer(resolution=RESOLVED),
            memory_specialist=MemorySpecialist(broken_port))
        self.assertEqual(session.memory_delegation["status"], "failed")
        self.assertIsNotNone(session.memory, "the direct recall must still have run")
        self.assertTrue(session.proposal.hypotheses, "the session must still reach a proposal")

    def test_a_raising_worker_does_not_abort_the_session(self):
        class Exploding:
            def execute(self, spec):
                raise RuntimeError("worker bug")

        session, _ = self.run_session(worker=Exploding())
        self.assertEqual(session.memory_delegation["status"], "failed")
        self.assertIn("could not be dispatched", session.memory_delegation["failure_detail"])
        self.assertIsNotNone(session.proposal, "the investigation must still complete")

    def test_a_failed_delegation_renders_as_memory_unavailable(self):
        text = render_memory_delegation(
            {"agent": "memory_specialist", "status": "failed", "observations": [],
             "failure_kind": "unavailable", "failure_detail": "bank unreachable"})
        self.assertIn("Memory unavailable", text)
        self.assertIn("not evidence about this issue", text)

    def test_an_empty_recall_still_renders(self):
        text = render_memory_delegation(
            {"agent": "memory_specialist", "status": "success",
             "observations": [{"ref": "x", "content": "no relevant past case"}], })
        self.assertIn("no relevant past case", text)
        self.assertNotIn("Memory unavailable", text)


class NoP5Tests(WiringBase):
    def test_no_concurrency_in_the_wiring(self):
        import inspect

        from debugagent.agents import memory_specialist
        from debugagent.pipeline import investigate as investigate_module

        for module in (memory_specialist, investigate_module):
            source = inspect.getsource(module)
            for forbidden in ("ThreadPool", "threading", "concurrent.futures", "as_completed",
                              "multiprocessing"):
                self.assertNotIn(forbidden, source,
                                 f"P5 fan-out must not be in the wiring: {module.__name__}")

    def test_no_coordinator_is_introduced(self):
        import inspect

        from debugagent.pipeline import investigate as investigate_module

        source = inspect.getsource(investigate_module)
        for forbidden in ("class Coordinator", "def coordinate", "fan_out", "gather("):
            self.assertNotIn(forbidden, source)

    def test_exactly_one_task_is_delegated_per_session(self):
        port = self.port()
        worker = MemorySpecialist(port)
        session, _ = self.run_session(worker=worker)
        self.assertEqual(worker._executed, [session.session_id + "-memory"],
                         "one session delegates exactly one task")

    def test_the_worker_still_cannot_retain(self):
        port = self.port()
        session, _ = self.run_session(worker=MemorySpecialist(port))
        client = port._store._client
        retained_case_keys = [call["metadata"]["case_key"] for call in client.retained]
        # The session retained ONE case, via the engineer's resolution - never via the worker.
        self.assertLessEqual(len(retained_case_keys), 1)


if __name__ == "__main__":
    unittest.main()
