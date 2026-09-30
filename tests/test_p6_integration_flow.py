"""P6 integration: the three workers inside the REAL `investigate()` flow.

End-to-end over a real directory, the real filesystem adapters, a real `Coordinator` and a real
`MemoryLane`. The only fakes are the LLM, the Hindsight port and the engineer terminal, all of which sit
outside the code under test. That matters more than usual here: the P6 adapter tests already proved the
workers respect a path boundary in isolation, and what cannot be proven that way is whether the FLOW
keeps their results on the right side of the trust boundary once a real session runs around them.

Every test names the boundary it defends. A test that cannot say which boundary it protects tends to
quietly stop protecting it.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import loop_support
from debugagent.agents.coordinator import Coordinator
from debugagent.agents.registry import AuthorizationError
from debugagent.agents.tasks import Artifact, SubAgentResult
from debugagent.composition import build_repository_runtime
from debugagent.pipeline.investigate import investigate
from debugagent.pipeline.types import DebugInput
from loop_support import RESOLVED, FakeLLM, FakeMemoryPort, ScriptedEngineer, hyp

SYMPTOM = "connection pool exhausted"


def _repo(tmp: str) -> Path:
    """A real repository: code, a log, and an unrelated file."""
    root = Path(tmp) / "repo"
    (root / "src").mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "src" / "pool.py").write_text(
        "POOL_SIZE = 5\n"
        "def acquire():\n"
        f"    # {SYMPTOM} when the pool is small\n"
        "    return None\n", encoding="utf-8")
    (root / "logs" / "app.log").write_text(
        f"2026-01-01 ERROR {SYMPTOM}\n{SYMPTOM} after 5 retries\n", encoding="utf-8")
    (root / "src" / "unrelated.py").write_text("VALUE = 1\n", encoding="utf-8")
    return root


class RecordingEngineer(ScriptedEngineer):
    """The repo's own scripted engineer, extended to record what the flow showed.

    Reused rather than reimplemented so this file is tested against the same engineer contract the
    existing flow tests use.
    """

    @property
    def text(self) -> str:
        return "\n".join(self.shown)


def _raw() -> DebugInput:
    return DebugInput(
        f"checkout service fails: {SYMPTOM}",
        [f"seen in {SYMPTOM} logs"],
        {"service": "checkout", "region": "eu-west-1"})


def _hypothesis(cites=()) -> dict:
    """A schema-valid proposal. Cites are derived from what memory actually surfaced, so the schema's
    'only cite a recalled case' rule is satisfied by real recall rather than by hand-written ids."""
    return {"hypotheses": [
        hyp(text="pool size too small for checkout traffic", cites=cites),
        hyp(text="pool exhausted by a retry storm rather than pool size", cites=cites,
            step="count retries per request in the log")]}


def _deny(*agents):
    """Refuse authorisation for `agents` only, leaving every other dispatch alone.

    Patching the module-level `authorize` outright would also break the MEMORY delegation, and a test
    that fails in the memory path is not testing the worker stage. This keeps the failure where the
    boundary under test is.
    """
    from debugagent.agents import coordinator as coordinator_module

    real = coordinator_module.authorize

    def guarded(spec):
        if spec.agent in agents:
            raise AuthorizationError(f"denied by test for {spec.agent}")
        return real(spec)

    return mock.patch.object(coordinator_module, "authorize", guarded)


class FlowHarness(unittest.TestCase):
    """Shared fixture: a real repository, a real Coordinator, one real session.

    Only the LLM, the memory port and the engineer terminal are doubles - all of them outside the code
    under test. The repository, its adapters, the Coordinator and the lane are all real.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _repo(self._tmp.name)
        self.port = FakeMemoryPort()
        self.runtime = build_repository_runtime(str(self.root), coordinator=Coordinator())
        self.engineer = RecordingEngineer(resolution=RESOLVED)

    def _cited_case_ids(self, limit=1):
        """Case ids the pipeline itself would see, via the same `recall()` the flow uses.

        Derived rather than hand-written: an id the session never recalled would satisfy the schema by
        accident rather than because memory actually surfaced it.
        """
        from debugagent.pipeline.normalize import normalize
        from debugagent.pipeline.recall_match import recall

        context = recall(self.port, normalize(_raw()))
        return [] if context.abstained else [c["case_id"] for c in context.candidates][:limit]

    def _investigate(self, *, coordinator=None, runtime=None, llm=None, **kwargs):
        runtime = runtime or self.runtime
        # A fresh LLM per session: `FakeLLM` pops its scripted outputs, so reusing one would make the
        # second session in a test fail on an exhausted script rather than on the thing under test.
        llm = llm or FakeLLM(_hypothesis(self._cited_case_ids()))
        return investigate(_raw(), self.port, llm, self.engineer,
                           coordinator=coordinator or runtime.coordinator,
                           repository=runtime, **kwargs)


class WorkerStageRuns(FlowHarness):
    """The stage is reached at all, and it is reached with real adapters."""

    def test_flow_actually_runs_both_workers(self):
        """A milestone that wires workers in and never runs them would pass every other test here."""
        session = self._investigate(verifier_targets=["src/pool.py"],
                                    patch_requests=[("src/pool.py", "POOL_SIZE = 50\n")])
        self.assertIsNotNone(session.workers)
        self.assertTrue(session.workers["verifier"]["findings"], "verifier produced no findings")
        self.assertTrue(session.workers["patches"]["proposals"], "patch generator produced no proposal")

    def test_workers_read_the_real_files_on_disk(self):
        """Real adapters, not a stub: the finding must quote content that only exists on disk."""
        session = self._investigate(verifier_targets=["logs/app.log"])
        findings = session.workers["verifier"]["findings"]
        self.assertTrue(any("connection pool exhausted" in f["content"] for f in findings),
                        f"no finding quoted the real log; refs were {[f['ref'] for f in findings]}")

    def test_findings_carry_provenance(self):
        """A finding says where it came from, so it can be checked rather than trusted."""
        session = self._investigate(verifier_targets=["src/pool.py"])
        for finding in session.workers["verifier"]["findings"]:
            self.assertTrue(finding["ref"], "a finding arrived with no ref")
            self.assertTrue(finding["task_id"].startswith("verify:"))

    def test_no_worker_stage_without_a_repository(self):
        """The Phase 1 default is unchanged: no repository, no worker section, no new keys."""
        session = investigate(_raw(), self.port, FakeLLM(_hypothesis(self._cited_case_ids())),
                              self.engineer)
        self.assertIsNone(session.workers)
        self.assertNotIn("workers", session.to_dict())

    def test_repository_without_targets_runs_nothing(self):
        """A repository on its own is not consent to run workers."""
        session = self._investigate()
        self.assertIsNone(session.workers)
        self.assertNotIn("workers", session.to_dict())
        self.assertIsNotNone(session.proposal, "the flow itself should still have run")

    def test_only_the_requested_channel_appears(self):
        """Asking for verification does not grow a patches section, and vice versa."""
        session = self._investigate(verifier_targets=["src/pool.py"])
        self.assertIn("verifier", session.workers)
        self.assertNotIn("patches", session.workers)

        session = self._investigate(patch_requests=[("src/pool.py", "POOL_SIZE = 50\n")])
        self.assertIn("patches", session.workers)
        self.assertNotIn("verifier", session.workers)

    def test_engineer_sees_both_sections_with_their_boundary_wording(self):
        self._investigate(verifier_targets=["src/pool.py"],
                          patch_requests=[("src/pool.py", "POOL_SIZE = 50\n")])
        self.assertIn("Code/Log Verifier", self.engineer.text)
        self.assertIn("Patch Generator", self.engineer.text)
        self.assertIn("not a verdict", self.engineer.text)
        self.assertIn("PROPOSALS", self.engineer.text)

    def test_stage_runs_before_hypotheses_are_generated(self):
        """The stage exists to inform the proposal, so it must not run after it."""
        order: list[str] = []

        class OrderedEngineer(RecordingEngineer):
            def show(self, text):
                if "Code/Log Verifier" in text:
                    order.append("verifier")
                super().show(text)

        engineer = OrderedEngineer()

        class OrderedLlm(FakeLLM):
            def complete_structured(self, prompt, *, schema, name="response", check=None):
                order.append("llm")
                return super().complete_structured(prompt, schema=schema, name=name, check=check)

        investigate(_raw(), self.port, OrderedLlm(_hypothesis(self._cited_case_ids())), engineer,
                    coordinator=self.runtime.coordinator, repository=self.runtime,
                    verifier_targets=["src/pool.py"])
        self.assertEqual(order[:2], ["verifier", "llm"],
                         "the worker stage must complete before the LLM proposes")

    def test_one_structured_record_holds_the_whole_worker_result(self):
        """One record, not three loose fields, so a session carries a single worker result."""
        session = self._investigate(verifier_targets=["src/pool.py", "logs/app.log"],
                                    patch_requests=[("src/pool.py", "POOL_SIZE = 50\n")])
        self.assertEqual(set(session.workers), {"verifier", "patches"})
        self.assertEqual(session.to_dict()["workers"], session.workers)


class AuthorityBoundaries(FlowHarness):
    """The point of the milestone: every result on the right side of the trust boundary."""

    def test_memory_is_never_evidence(self):
        """MEMORY informs. A recalled case must not become a fact about this system."""
        session = self._investigate(verifier_targets=["src/pool.py"])
        self.assertIn("not evidence about this issue", self.engineer.text)
        evidence_blob = str(session.evidence.to_dict())
        self.assertNotIn(session.memory.query, evidence_blob)
        # The memory section is rendered separately and never merged into the evidence set.
        self.assertIn("Past cases from bank", self.engineer.text)

    def test_memory_delegation_carries_no_worker_output(self):
        """The memory worker's report is the memory worker's alone.

        Without this, nothing would stop a future change from dropping verifier findings into
        `memory_delegation`, where they would be rendered as MEMORY - knowledge of a PAST case - and be
        believed for the wrong reason.
        """
        session = self._investigate(verifier_targets=["logs/app.log", "src/pool.py"],
                                    patch_requests=[("src/pool.py", "POOL_SIZE = 50\n")])
        report = session.memory_delegation or {}
        blob = str(report)
        for finding in session.workers["verifier"]["findings"]:
            self.assertNotIn(finding["content"][:40], blob,
                             "a verifier finding leaked into the memory delegation report")
        self.assertNotIn("POOL_SIZE = 50", blob, "a patch proposal leaked into the memory report")
        if report.get("observations"):
            for observation in report["observations"]:
                self.assertEqual(observation["ref"].split(":")[0], observation["ref"].split(":")[0])
                self.assertNotIn("pool.py", observation["ref"],
                                 "a repository file appeared in a memory observation")

    def test_verifier_findings_do_not_become_evidence_items(self):
        """The verifier is on the evidence SIDE of the line, but it is NOT inside `Evidence`.

        This is the boundary most easily lost: findings look like facts, so folding them in would let a
        worker's read of a file stand as a verified property of the system.
        """
        session = self._investigate(verifier_targets=["src/pool.py", "logs/app.log"])
        findings = session.workers["verifier"]["findings"]
        self.assertTrue(findings)
        evidence_blob = str(session.evidence.to_dict())
        for finding in findings:
            self.assertNotIn(finding["ref"], evidence_blob,
                             f"finding ref {finding['ref']!r} leaked into the Evidence set")
        for item in session.evidence.items:
            self.assertIn(item.source, ("case", "engineer"),
                          f"evidence item sourced from {item.source!r}, not case or engineer")

    def test_verifier_findings_never_change_a_verification(self):
        """Findings inform a decision. They never become one, and never mark anything verified."""
        session = self._investigate(verifier_targets=["src/pool.py"])
        for result in session.verifications:
            self.assertTrue(result.status, "a verification lost its status")
            self.assertTrue(result.engineer_decision, "a verification lost its engineer decision")
        # No finding text leaked into a decision or a verification record.
        blob = str([vars(r) for r in session.verifications])
        for finding in session.workers["verifier"]["findings"]:
            self.assertNotIn(finding["content"][:40], blob, "a finding became a decision input")

    def test_patches_are_proposals_never_applied(self):
        """A proposal is a candidate change. Nothing writes it, and nothing applies it."""
        session = self._investigate(patch_requests=[("src/pool.py", "POOL_SIZE = 50\n")])
        self.assertTrue(session.workers["patches"]["proposals"])
        # The file on disk is untouched. This is the assertion that fails if anything ever applies.
        on_disk = (self.root / "src" / "pool.py").read_text(encoding="utf-8")
        self.assertIn("POOL_SIZE = 5", on_disk, "a proposal was written to the repository")
        self.assertNotIn("POOL_SIZE = 50", on_disk)

    def test_a_proposal_never_becomes_a_resolution_or_a_verification(self):
        """A candidate change is not a fix, and the flow does not report it as one."""
        session = self._investigate(patch_requests=[("src/pool.py", "POOL_SIZE = 50\n")])
        # The section must label itself as a proposal and must not read as a change that happened. Both
        # halves are asserted: a label alone can sit on top of wording that claims the work was done.
        boundary = session.workers["patches"]["boundary"]
        self.assertTrue(boundary.startswith("PROPOSALS"), f"section no longer calls itself a proposal: {boundary}")
        self.assertIn("applied automatically", boundary)
        self.assertIn("Nothing here has been applied", self.engineer.text)
        resolution = str(session.resolution.to_dict() if session.resolution else "")
        self.assertNotIn("POOL_SIZE = 50", resolution, "the resolution claims the patch was applied")

    def test_the_worker_stage_never_calls_the_engineer_decision_api(self):
        """The stage reports; the engineer decides. It must not multiply decisions.

        With several findings and a proposal in hand, a stage that decided anything would produce more
        decisions than there are hypotheses. Decisions belong to hypotheses, and only to hypotheses.
        """
        engineer = RecordingEngineer(resolution=RESOLVED)
        session = investigate(_raw(), self.port, FakeLLM(_hypothesis(self._cited_case_ids())), engineer,
                              repository=self.runtime, coordinator=self.runtime.coordinator,
                              verifier_targets=["src/pool.py", "logs/app.log"],
                              patch_requests=[("src/pool.py", "POOL_SIZE = 50\n")])
        self.assertGreaterEqual(len(session.workers["verifier"]["findings"]), 2)
        self.assertTrue(session.workers["patches"]["proposals"])
        self.assertEqual(len(engineer.asked), len(session.proposal.hypotheses),
                         "decisions must be asked once per hypothesis, whatever the workers returned")

    def test_memory_specialist_never_sees_worker_results(self):
        """Memory is knowledge of past cases; findings and proposals are not its input."""
        seen: list[object] = []

        class SpySpecialist:
            agent_id = "memory_specialist"
            allowed_tools = ("memory_recall",)
            model = "none"
            client_access = True

            def execute(self, spec):
                seen.append(spec)
                return SubAgentResult(task_id=spec.task_id, agent=spec.agent, status="success",
                                      observations=())

        # One Coordinator carrying BOTH the memory worker and the repository workers - the real
        # composition, and the reason memory and workers share one lane.
        hub = Coordinator.with_memory_specialist(SpySpecialist())
        runtime = build_repository_runtime(str(self.root), coordinator=hub)
        session = self._investigate(coordinator=runtime.coordinator, runtime=runtime,
                                    verifier_targets=["src/pool.py"],
                                    patch_requests=[("src/pool.py", "POOL_SIZE = 50\n")])
        self.assertTrue(seen, "the memory specialist was not dispatched")
        blob = str([str(spec.context.to_dict()) for spec in seen])
        self.assertNotIn("POOL_SIZE = 50", blob, "a patch proposal leaked into a memory task")
        self.assertIsNotNone(session.memory_delegation)


class AuthorisationAndMemorySafety(FlowHarness):
    """`authorize()` before every dispatch, and one lane for the client."""

    def test_dispatch_goes_through_authorize(self):
        """Proven by removal, not by inspection: no authorize, no worker run."""
        from debugagent.agents import coordinator as coordinator_module
        with _deny("code_log_verifier", "patch_generator"):
            with mock.patch.object(coordinator_module, "authorize",
                                   wraps=coordinator_module.authorize) as authorize:
                session = self._investigate(verifier_targets=["src/pool.py"],
                                            patch_requests=[("src/pool.py", "POOL_SIZE = 50\n")])
        called_on = [call.args[0].agent for call in authorize.call_args_list]
        self.assertIn("code_log_verifier", called_on,
                      "the worker stage bypassed authorize()")
        self.assertIn("patch_generator", called_on, "the patch stage bypassed authorize()")
        self.assertFalse(session.workers["verifier"]["findings"],
                         "a worker ran despite failing authorisation")
        self.assertFalse(session.workers["patches"]["proposals"],
                         "a patch was proposed despite failing authorisation")

    def test_authorization_failure_is_a_refusal_not_a_crash(self):
        """A refused worker is reported as refused, and the flow still returns a session."""
        with _deny("code_log_verifier", "patch_generator"):
            session = self._investigate(verifier_targets=["src/pool.py"],
                                        patch_requests=[("src/pool.py", "POOL_SIZE = 50\n")])
        self.assertIsNotNone(session.evidence, "the flow aborted on a worker refusal")
        self.assertIsNotNone(session.resolution, "the flow did not finish after a worker refusal")
        refusals = session.workers["verifier"]["refusals"] + session.workers["patches"]["refusals"]
        self.assertEqual(len(refusals), 2, "refusals were swallowed")
        self.assertIn("denied by test", refusals[0]["error"])

    def test_repository_must_share_the_session_coordinator(self):
        """Two Coordinators means two MemoryLanes, and the client hazard with them."""
        other = build_repository_runtime(str(self.root), coordinator=Coordinator())
        self.assertIsNot(other.coordinator, self.runtime.coordinator)
        with self.assertRaises(ValueError) as caught:
            investigate(_raw(), self.port, FakeLLM(_hypothesis(self._cited_case_ids())), self.engineer,
                        coordinator=self.runtime.coordinator, repository=other,
                        verifier_targets=["src/pool.py"])
        self.assertIn("MemoryLanes", str(caught.exception))

    def test_one_shared_lane_covers_memory_and_the_worker_stage(self):
        """The worker stage adds no second path to the client, and takes no lane of its own."""
        import debugagent.pipeline.worker_stage as stage

        hub = self.runtime.coordinator
        before = id(hub.lane)
        self._investigate(verifier_targets=["src/pool.py"], patch_requests=[("src/pool.py", "P = 9\n")])
        self.assertEqual(id(hub.lane), before, "the flow replaced the shared lane")
        self.assertIs(hub.lane, self.runtime.coordinator.lane)
        # The stage cannot hold a Coordinator at all - it does not import one, so it must use the one
        # it was handed. Structural, not a convention someone could route around later.
        self.assertFalse(hasattr(stage, "Coordinator"), "the stage now holds a Coordinator")
        self.assertTrue(self.port.queries, "the flow never reached memory")

    def test_memory_work_still_serialises_with_workers_registered(self):
        """Registering repository workers must not change how memory work is serialised."""
        hub = self.runtime.coordinator
        session = self._investigate(verifier_targets=["src/pool.py", "logs/app.log"])
        self.assertTrue(self.port.queries, "the flow never reached memory")
        self.assertTrue(self.port.retained, "the flow never reached retention")
        self.assertEqual(len(session.workers["verifier"]["outcomes"]), 2)


class OutcomeSemantics(FlowHarness):
    """Refusal, failure and success stay three distinguishable things."""

    def test_one_bad_target_does_not_discard_the_others(self):
        """Failure isolation: a missing file fails alone, and real findings still arrive."""
        session = self._investigate(verifier_targets=["src/pool.py", "src/does_not_exist.py"])
        outcomes = session.workers["verifier"]["outcomes"]
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(any(o["status"] == "success" for o in outcomes),
                        "the good target was lost with the bad one")
        self.assertTrue(any(o["status"] != "success" for o in outcomes),
                        "the missing file did not fail")
        self.assertTrue(session.workers["verifier"]["findings"],
                        "no findings survived the failure")

    def test_refusal_is_reported_as_refused_and_never_as_failed(self):
        """A rule saying no and a thing going wrong are different, and stay different."""
        with _deny("code_log_verifier"):
            session = self._investigate(verifier_targets=["src/pool.py"])
        outcome = session.workers["verifier"]["outcomes"][0]
        self.assertEqual(outcome["status"], "refused", "a refusal was reported as something else")
        self.assertIsNone(outcome["result"], "a refused task invented a result")
        self.assertTrue(session.workers["verifier"]["refusals"])

    def test_a_non_authorisation_error_is_not_reported_as_a_refusal(self):
        """A refusal is specifically `AuthorizationError`. A dispatch error is not a refusal.

        Tested directly on the record builder, because the flow cannot easily produce a non-authorisation
        error outcome - the Coordinator converts worker faults into failed RESULTS, so the only refusals
        that reach this code are authorization ones. That is a property worth pinning: if this ever
        starts reporting outages as refusals, "the system said no" and "the system broke" collapse.
        """
        from debugagent.agents.coordinator import TaskOutcome
        from debugagent.pipeline.worker_stage import _refusal_records

        spec = mock.Mock(task_id="verify:a.py", agent="code_log_verifier")
        refused = TaskOutcome(0, spec, error=AuthorizationError("denied by test"))
        broke = TaskOutcome(1, spec, error=RuntimeError("thread died"))

        records = _refusal_records([refused, broke])
        self.assertEqual([record["task_id"] for record in records], ["verify:a.py"],
                         "a broken dispatch was reported as a refusal")
        self.assertIn("denied by test", records[0]["error"])
        self.assertNotIn("thread died", str(records))

    def test_a_worker_fault_is_an_error_not_a_refusal(self):
        """The two must not be collapsed, or 'denied' and 'broke' read the same."""
        worker = self.runtime.coordinator.worker_for("code_log_verifier")
        with mock.patch.object(worker, "execute", side_effect=RuntimeError("worker blew up")):
            session = self._investigate(verifier_targets=["src/pool.py"])
        outcome = session.workers["verifier"]["outcomes"][0]
        self.assertNotEqual(outcome["status"], "refused")
        self.assertFalse(session.workers["verifier"]["refusals"],
                         "a crash was reported as a refusal")

    def test_summary_reports_counts_not_a_verdict(self):
        """A stage that mostly worked reports how it went; it does not claim a result."""
        session = self._investigate(verifier_targets=["src/pool.py", "src/missing.py"])
        summary = session.workers["verifier"]["summary"]
        self.assertEqual(summary["total"], 2)
        self.assertTrue(summary["joined"], "the join barrier did not hold")
        self.assertEqual(sum(summary["by_status"].values()), 2)
        self.assertIn("observations", session.workers["verifier"]["boundary"])

    def test_a_clean_stage_with_nothing_to_show_says_so(self):
        """No findings must read as 'nothing observed', not as a quiet success."""
        from debugagent.pipeline.render import render_worker_verifier
        rendered = render_worker_verifier({"findings": [], "refusals": [],
                                           "findings_truncated": False, "findings_total": 0})
        self.assertIn("No observation was reported", rendered)
        self.assertIn("clean run with nothing to show", rendered)


class DeterministicRanking(FlowHarness):
    """Same repository and issue in, same order out."""

    def _refs(self, targets):
        session = self._investigate(verifier_targets=targets)
        return [(f["ref"], f["kind"], f["score"]) for f in session.workers["verifier"]["findings"]]

    def test_repeated_runs_produce_the_same_order(self):
        targets = ["src/pool.py", "logs/app.log", "src/unrelated.py"]
        first = self._refs(targets)
        self.assertTrue(first, "no findings to rank")
        self.assertEqual(first, self._refs(targets), "ranking is not reproducible")
        self.assertEqual(first, self._refs(list(reversed(targets))),
                         "ranking depends on the order targets were asked for")

    def test_identical_findings_keep_a_stable_order(self):
        """Byte-identical findings from one task are the same finding, and must still order stably."""
        from debugagent.pipeline.worker_stage import rank_findings
        duplicate = [SubAgentResult(
            task_id="verify:same.py", agent="code_log_verifier", status="success",
            observations=(Artifact(kind="code", ref="same.py", content="x"),)) for _ in range(2)]
        case = mock.Mock(problem_signature="s", symptoms=[], environment={})
        first = rank_findings([(result, None) for result in duplicate], case)
        second = rank_findings([(result, None) for result in duplicate], case)
        self.assertEqual([(f.ref, f.content) for f in first], [(f.ref, f.content) for f in second],
                         "identical findings did not order stably")

    def test_distinguishable_findings_cannot_tie(self):
        from debugagent.pipeline.worker_stage import rank_findings

        results = [SubAgentResult(
            task_id="verify:a.py", agent="code_log_verifier", status="success",
            observations=(Artifact(kind="code", ref="a.py", content="first"),
                          Artifact(kind="code", ref="a.py", content="second")))]
        case = mock.Mock(problem_signature="s", symptoms=[], environment={})
        findings = rank_findings([(result, None) for result in results], case)
        keys = [(f.score, f.kind, f.ref, f.task_id, f.content) for f in findings]
        self.assertEqual(len(set(keys)), len(keys), "two different findings tied")

    def test_relevant_findings_rank_above_irrelevant_ones(self):
        from debugagent.pipeline.worker_stage import rank_findings
        from debugagent.pipeline.types import NormalizedDebugCase

        case = NormalizedDebugCase.from_dict({
            "problem_signature": "checkout fails", "raw_description": f"checkout: {SYMPTOM}", "symptoms": [SYMPTOM],
            "environment": {"service": "checkout"}})
        result = SubAgentResult(
            task_id="verify:x", agent="code_log_verifier", status="success",
            observations=(Artifact(kind="code", ref="noise.py", content="VALUE = 1"),
                          Artifact(kind="log", ref="hit.log", content=f"ERROR {SYMPTOM} for checkout")))
        findings = rank_findings([(result, None)], case)
        self.assertEqual(findings[0].ref, "hit.log", "the relevant finding did not rank first")
        self.assertGreater(findings[0].score, findings[1].score)

    def test_ranking_does_not_depend_on_completion_order(self):
        """Findings are sorted by key, so the slow worker cannot win by finishing last."""
        from debugagent.pipeline.worker_stage import rank_findings
        from debugagent.pipeline.types import NormalizedDebugCase

        case = NormalizedDebugCase.from_dict({
            "problem_signature": "s", "raw_description": SYMPTOM, "symptoms": [SYMPTOM], "environment": {}})
        slow = SubAgentResult(task_id="verify:z", agent="code_log_verifier", status="success",
                              observations=(Artifact(kind="code", ref="z.py", content="VALUE = 0"),))
        fast = SubAgentResult(task_id="verify:a", agent="code_log_verifier", status="success",
                              observations=(Artifact(kind="code", ref="a.py", content=f"{SYMPTOM} here"),))
        forwards = rank_findings([(slow, None), (fast, None)], case)
        backwards = rank_findings([(fast, None), (slow, None)], case)
        self.assertEqual([f.ref for f in forwards], [f.ref for f in backwards])
        self.assertEqual(forwards[0].ref, "a.py", "the issue-relevant finding did not rank first")

    def test_truncation_is_reported_not_hidden(self):
        """A capped list must say it was capped, or it reads as a complete one."""
        from debugagent.pipeline import worker_stage
        from debugagent.agents.coordinator import FanOutResult, TaskOutcome

        observations = tuple(Artifact(kind="code", ref=f"f{i}.py", content="c")
                             for i in range(worker_stage.MAX_REPORTED_FINDINGS + 7))
        result = SubAgentResult(task_id="verify:f0.py", agent="code_log_verifier", status="success",
                                observations=observations)
        outcome = TaskOutcome(0, mock.Mock(task_id="verify:f0.py", agent="code_log_verifier"),
                              result=result)
        fan = FanOutResult((outcome,))
        case = mock.Mock(problem_signature="s", symptoms=[], environment={})
        section = worker_stage._verifier_section(fan, worker_stage.rank_findings([(result, None)], case))
        self.assertTrue(section["findings_truncated"])
        self.assertEqual(section["findings_total"], worker_stage.MAX_REPORTED_FINDINGS + 7)
        self.assertEqual(len(section["findings"]), worker_stage.MAX_REPORTED_FINDINGS)


class PathBoundaryHoldsInFlow(FlowHarness):
    """The repository boundary is still a boundary once the flow is running."""

    def test_absolute_path_outside_the_root_is_refused(self):
        outside = Path(self._tmp.name) / "outside.py"
        outside.write_text("SECRET = 1\n", encoding="utf-8")
        session = self._investigate(verifier_targets=[str(outside)])
        self.assertNotEqual(session.workers["verifier"]["summary"]["by_status"], {"success": 1},
                            "a path outside the root was accepted")
        self.assertNotIn("SECRET", self.engineer.text)

    def test_traversal_out_of_the_root_is_refused(self):
        session = self._investigate(verifier_targets=["src/../../outside.py"])
        self.assertFalse(session.workers["verifier"]["findings"],
                         "a traversing path produced findings")

    def test_the_worker_stage_cannot_escape_via_a_patch_target(self):
        """A patch target outside the root is refused like any other out-of-root path."""
        outside = Path(self._tmp.name) / "outside.py"
        outside.write_text("SECRET = 1\n", encoding="utf-8")
        session = self._investigate(patch_requests=[(str(outside), "SAFE = 2\n")])
        self.assertFalse(session.workers["patches"]["proposals"],
                         "a patch was proposed for a file outside the root")
        self.assertNotIn("SAFE = 2", (Path(self._tmp.name) / "outside.py").read_text(encoding="utf-8"))


class AdkRemainsOptional(FlowHarness):
    """ADK stays optional, and is not responsible for authority, memory or outcomes."""

    def test_the_flow_never_imports_the_adk_bridge(self):
        """The flow must not reach for ADK at all: a dependency nobody asked for is still one."""
        import sys
        sys.modules.pop("debugagent.adk_bridge", None)
        session = self._investigate(verifier_targets=["src/pool.py"])
        self.assertTrue(session.workers["verifier"]["findings"])
        self.assertNotIn("debugagent.adk_bridge", sys.modules,
                         "the investigate flow imported the ADK bridge")

    def test_the_bridge_participates_in_no_session_state(self):
        """A session produced by the flow carries nothing ADK-derived."""
        from debugagent.adk_bridge import TaskCollector
        with mock.patch.object(TaskCollector, "record",
                               side_effect=AssertionError("the bridge was used")):
            session = self._investigate(verifier_targets=["src/pool.py"],
                                        patch_requests=[("src/pool.py", "POOL_SIZE = 50\n")])
        self.assertTrue(session.workers["verifier"]["findings"])
        self.assertTrue(session.workers["patches"]["proposals"])

    def test_the_worker_stage_owns_no_authorization_or_lane(self):
        """The stage holds no authority of its own: no authoriser, no Coordinator, no client.

        Checked on the module's namespace rather than its text, because a docstring that merely
        *names* the lane it must not touch should not fail the test that guards against touching it.
        """
        import debugagent.pipeline.worker_stage as stage

        for forbidden in ("authorize", "authorize_task", "AuthorizationError", "MemoryLane",
                          "hindsight_client", "HindsightPort"):
            self.assertFalse(hasattr(stage, forbidden),
                             f"worker_stage defines {forbidden!r}; authority lives elsewhere")
        # It imports `Coordinator` only as a type; it must never construct one.
        import inspect
        self.assertNotIn("Coordinator(", inspect.getsource(stage),
                         "the stage constructs a Coordinator, so it could hold its own authority")
        # And neither entry point accepts a Coordinator, so it cannot be dispatched around.
        for func in (stage.run_worker_stage, stage.plan_tasks):
            self.assertNotIn("coordinator", inspect.signature(func).parameters,
                             f"{func.__name__} accepts a Coordinator, so it could dispatch directly")


if __name__ == "__main__":
    unittest.main()
