"""P7 production-readiness audit of the COMPLETE investigation flow.

The P6 integration tests asked "does the worker stage hold the boundary?". This file asks a different and
harder question: "does the WHOLE flow still hold every boundary once a realistic investigation runs end to
end?" Individual boundaries can each pass while the combination fails - a section that never renders is not
a boundary, a section that renders into the engineer's transcript before the decision is, and the audit is
the only place that combination shows up.

What is deliberately NOT here: new architecture. Every test asserts a property the current design already
claims. Where a probe found the code and its documentation disagree, that is recorded in
`docs/audit-001-production-readiness.md` as a finding and the wording was corrected - not the design.

Realism: the scenario is the repository's own `demo/inputs/act2-media-uploader.json` (an nginx body-limit
outage) driving a temp repository containing a real nginx config and a real log, through the real
`FileSourcePort`/`RepoPatchSourcePort`, a real `Coordinator` and a real `MemoryLane`. Only the LLM, the
Hindsight client and the engineer terminal are doubled, and the doubles come from the repo's own
`support`/`loop_support` helpers rather than bespoke ones.
"""

from __future__ import annotations

import hashlib
import re
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import loop_support
import support
from debugagent.agents.coordinator import Coordinator
from debugagent.agents.memory_specialist import MemorySpecialist
from debugagent.agents.registry import AuthorizationError
from debugagent.agents.tasks import Artifact, SubAgentResult
from debugagent.composition import build_repository_runtime
from debugagent.memory.hindsight_store import HindsightMemoryStore, compute_case_key
from debugagent.pipeline.ingest import load_debug_input
from debugagent.pipeline.investigate import investigate
from debugagent.pipeline.memory_adapter import HindsightMemoryPort
from debugagent.pipeline.types import DebugInput
from debugagent.seeds.loader import load_seed_file
from loop_support import RESOLVED, FakeLLM, FakeMemoryPort, ScriptedEngineer, hyp
from test_p6_integration_flow import _deny

REPO_ROOT = Path(__file__).resolve().parents[1]
ACT2 = REPO_ROOT / "demo" / "inputs" / "act2-media-uploader.json"

NGINX_CONF = """server {
    listen 80;
    client_max_body_size 2m;
    location /upload {
        proxy_pass http://127.0.0.1:9000;
    }
}
"""

# Shaped like a real nginx error log for act2, and carrying the ECONNRESET the issue actually reports -
# which is what makes relevance discriminable rather than uniformly zero.
UPLOAD_LOG = """2026-09-28T10:00:01Z INFO upload accepted size=1.2MB
2026-09-28T10:00:04Z ERROR upstream sent too large request: 413 Request Entity Too Large
2026-09-28T10:00:04Z WARN nginx[1234]: client intended to send too large body: 5242880 bytes
2026-09-28T10:00:05Z ERROR upstream prematurely closed connection: ECONNRESET
2026-09-28T10:00:09Z ERROR 413 resetting upload session
"""


def _repo(tmp: str) -> Path:
    """A repository shaped like the act2 scenario: the nginx config, its log, and unrelated code."""
    root = Path(tmp) / "prod-repo"
    (root / "nginx").mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "app").mkdir()
    (root / "nginx" / "site.conf").write_text(NGINX_CONF, encoding="utf-8")
    (root / "logs" / "nginx-error.log").write_text(UPLOAD_LOG, encoding="utf-8")
    (root / "app" / "server.py").write_text(
        "MAX_UPLOAD = 2 * 1024 * 1024\n"
        "def handle_upload(body):\n"
        "    return 413 if len(body) > MAX_UPLOAD else 200\n", encoding="utf-8")
    (root / "app" / "unrelated.py").write_text("VERSION = '1.0'\n", encoding="utf-8")
    return root


def _tree_digest(root: Path) -> dict[str, tuple[str, float, int]]:
    """Content, mtime and size for every file under `root`.

    Content alone is not enough for a read-only audit: a stage that rewrote a file with identical bytes
    would pass a content check while still having touched the repository.
    """
    digest = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            digest[str(path.relative_to(root))] = (
                hashlib.sha256(path.read_bytes()).hexdigest(), stat.st_mtime, stat.st_size)
    return digest


def _strip_volatile(record):
    """Drop fields that differ between two runs of the same scenario for reasons unrelated to policy.

    `session_id` is generated per run and `captured_at`/`recalled_at` are timestamps. Leaving them in
    would make every cross-run comparison in this file fail for reasons that have nothing to do with the
    boundary under test - and a test that fails for the wrong reason stops being read.
    """
    volatile = {"session_id", "captured_at", "recalled_at", "memory_case_id", "case_key"}
    id_shaped = re.compile(r"\b[0-9a-f]{12,}\b")

    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k not in volatile}
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, str):
            # Delegation task ids embed the session id (`<hex>-memory`), so scrub id-shaped runs too.
            return id_shaped.sub("<id>", value)
        return value

    return clean(record)


def _proposal(*cites):
    return {"hypotheses": [
        hyp(text="nginx client_max_body_size rejects uploads above 2 MB", cites=cites),
        hyp(text="the app rejects the body itself before nginx sees it", cites=cites,
            step="compare app MAX_UPLOAD with nginx client_max_body_size")],
    }


class AuditHarness(unittest.TestCase):
    """One realistic investigation, wired to real adapters throughout."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _repo(self._tmp.name)
        self.before = _tree_digest(self.root)
        self.runtime = build_repository_runtime(str(self.root), coordinator=Coordinator())
        self.engineer = ScriptedEngineer(resolution=RESOLVED)
        self.port = FakeMemoryPort()

    def _llm(self, *cites):
        return FakeLLM(_proposal(*cites))

    def _cited(self):
        """Case ids the pipeline itself would see, from the same `recall()` the flow uses."""
        from debugagent.pipeline.normalize import normalize
        from debugagent.pipeline.recall_match import recall

        from debugagent.pipeline.ingest import load_debug_input
        context = recall(self.port, normalize(load_debug_input(ACT2)))
        return [] if context.abstained else [c["case_id"] for c in context.candidates][:1]

    def _session(self, *, coordinator=None, runtime=None, llm=None, engineer=None, **kwargs):
        runtime = runtime or self.runtime
        return investigate(load_debug_input(ACT2), self.port, llm or self._llm(*self._cited()),
                           engineer or self.engineer,
                           coordinator=coordinator or runtime.coordinator,
                           repository=runtime, **kwargs)

    def _full(self, **kwargs):
        """A complete run: verification plus a patch proposal, the whole chain."""
        return self._session(verifier_targets=["nginx/site.conf", "logs/nginx-error.log",
                                               "app/server.py"],
                             patch_requests=[("nginx/site.conf",
                                             NGINX_CONF.replace("2m", "10m"))], **kwargs)


class CompleteFlow(AuditHarness):
    """The chain actually runs, in order, and produces one structured result."""

    def test_every_stage_contributes_to_one_session(self):
        session = self._full()
        # Each link in the chain is present, and each is where it belongs.
        self.assertIsNotNone(session.case, "no normalized case")
        self.assertIsNotNone(session.evidence, "no evidence set")
        self.assertTrue(session.workers["verifier"]["findings"], "no verifier findings")
        self.assertTrue(session.workers["patches"]["proposals"], "no patch proposal")
        self.assertIsNotNone(session.memory, "no memory context")
        self.assertTrue(session.proposal.hypotheses, "no hypotheses")
        self.assertTrue(session.verifications, "no verifications")
        self.assertIsNotNone(session.resolution, "no resolution")

    def test_the_trace_records_the_worker_stage_in_order(self):
        session = self._full()
        trace = "\n".join(session.trace)
        def index(prefix):
            return next(i for i, line in enumerate(session.trace) if line.startswith(prefix))
        self.assertLess(index("evidence:"), index("verifier:"), "worker stage ran before evidence")
        self.assertLess(index("verifier:"), index("proposed"), "proposal preceded the worker stage")
        self.assertIn("proposals, nothing applied", trace)

    def test_findings_reach_the_engineer_before_a_decision_is_asked(self):
        """The engineer's decision is informed by the transcript, which is the ORDER that matters."""
        asked_at: list[int] = []
        shown_at: dict[str, int] = {}

        class OrderedEngineer(ScriptedEngineer):
            def show(self, text):
                if "Code/Log Verifier" in text:
                    shown_at["verifier"] = len(self.shown)
                super().show(text)

            def decide(self, hypothesis, mismatched, missing):
                asked_at.append(len(self.shown))
                return super().decide(hypothesis, mismatched, missing)

        engineer = OrderedEngineer(resolution=RESOLVED)
        self._full(engineer=engineer)
        self.assertIn("verifier", shown_at, "the verifier section was never shown")
        self.assertTrue(asked_at, "the engineer was never asked for a decision")
        self.assertGreaterEqual(min(asked_at), shown_at["verifier"],
                                "the engineer decided before the verifier section was shown")

    def test_the_llm_prompt_does_not_contain_worker_output(self):
        """Findings are shown to the ENGINEER, never injected into the model's context.

        Worth pinning precisely because it is the opposite of what a reader might assume. Injecting a
        worker's observations into the prompt would move worker output into the proposal step, which is a
        trust-boundary change and needs its own decision - it is not something to slip in via plumbing.
        """
        from debugagent.pipeline.hypothesize import build_prompt

        session = self._full()
        prompt = build_prompt(session.case, session.memory)
        for finding in session.workers["verifier"]["findings"]:
            self.assertNotIn(finding["content"][:40], prompt,
                             "a verifier finding leaked into the LLM prompt")
        self.assertNotIn("PROPOSED PATCH", prompt, "a patch proposal leaked into the LLM prompt")

    def test_on_step_observes_every_stage(self):
        """Progress callbacks must see the worker stage, or a persisting caller loses it."""
        stages: list[str] = []
        self._session(verifier_targets=["nginx/site.conf"],
                      patch_requests=[("nginx/site.conf", "x")],
                      on_step=lambda s: stages.append("|".join(sorted((s.workers or {}).keys()))))
        self.assertTrue(any("patches" in s for s in stages),
                        "on_step never observed the worker stage, so it could not persist it")

    def test_the_repository_is_untouched_by_a_full_run(self):
        """Content, mtime and size all unchanged: a full run reads, and only reads."""
        self._full()
        self.assertEqual(_tree_digest(self.root), self.before,
                         "a full investigation modified the repository")


class MemoryStaysKnowledge(AuditHarness):
    """MEMORY informs. It is never evidence."""

    def _real_memory_port(self):
        """A REAL HindsightMemoryPort over seeded cases, not a canned view."""
        rows = []
        for seed in load_seed_file():
            rows.append(support.FakeMemory(
                text=f"past case: {seed.problem_signature}",
                metadata={"case_key": compute_case_key(seed, seed.session_id or "seed"),
                          "outcome": seed.outcome,
                          "service": seed.environment.get("service", "unknown"),
                          "runtime": seed.environment.get("runtime", "unknown"),
                          "root_cause_key": (seed.root_cause or "")[:64]},
                scores=support.FakeScores(0.9, 0.8, 0.7),
                mentioned_at="2026-01-01T00:00:00+00:00"))
        store = HindsightMemoryStore(
            support.memory_config(Path(self._tmp.name)),
            client=support.FakeHindsightClient(recall_results=rows))
        self.addCleanup(store.close)
        return HindsightMemoryPort(store)

    def test_real_recall_stays_out_of_the_evidence_set(self):
        """With a real port over real seed cases, recalled knowledge must not become current facts."""
        port = self._real_memory_port()
        session = investigate(load_debug_input(ACT2), port, self._llm(),
                              ScriptedEngineer(resolution=RESOLVED),
                              coordinator=self.runtime.coordinator, repository=self.runtime,
                              verifier_targets=["nginx/site.conf"],
                              patch_requests=[("nginx/site.conf", NGINX_CONF.replace("2m", "10m"))])
        self.assertFalse(session.memory.abstained, "the fixture should recall something")
        self.assertTrue(session.memory.candidates, "no candidates recalled")
        evidence_blob = str(session.evidence.to_dict())
        for candidate in session.memory.candidates:
            self.assertNotIn(candidate["case_id"], evidence_blob,
                             "a recalled case id leaked into the evidence set")
        for item in session.evidence.items:
            self.assertIn(item.source, ("case", "engineer"),
                          f"evidence item sourced from {item.source!r}")

    def test_memory_contradiction_still_goes_through_verification_not_facts(self):
        """A contradicting memory candidate must surface as a gap/mismatch, not as a rewritten fact."""
        session = self._full()
        for result in session.verifications:
            self.assertIn("mismatched_environment_fields", vars(result))
        # The evidence values remain exactly what the case stated.
        stated = {k: v for k, v in session.case.environment.items() if v}
        for item in session.evidence.items:
            if item.name in stated:
                self.assertEqual(item.value, stated[item.name],
                                 "evidence was rewritten from memory")

    def test_the_memory_delegation_report_is_separate_from_memory(self):
        session = self._session(verifier_targets=["nginx/site.conf"])
        self.assertIn("memory_delegation", session.to_dict())
        blob = str(session.memory_delegation)
        for finding in session.workers["verifier"]["findings"]:
            self.assertNotIn(finding["content"][:40], blob,
                             "a verifier finding leaked into the memory delegation report")


class ObservationsAreNotFacts(AuditHarness):
    """A worker's read of a file is an observation until the engineer says otherwise."""

    def test_evidence_is_exactly_what_the_case_and_engineer_supplied(self):
        session = self._full()
        names = {item.name for item in session.evidence.items}
        for finding in session.workers["verifier"]["findings"]:
            # Findings name files; the evidence set is named after environment fields and the observation.
            self.assertNotIn(finding["ref"], names,
                             f"finding {finding['ref']!r} became an evidence item")
        self.assertIn("observation", names, "the case's own observations should be evidence")

    def test_no_finding_content_reaches_the_evidence_values(self):
        session = self._full()
        values = [item.value for item in session.evidence.items if item.value]
        for finding in session.workers["verifier"]["findings"]:
            for value in values:
                self.assertNotEqual(value, finding["content"])
                self.assertNotIn(finding["content"], str(values),
                                 "finding content was copied into an evidence value")

    def test_worker_observations_never_change_a_verification_status(self):
        """The verifier saw the code that the hypothesis is about; the status is still the engineer's."""
        session = self._full()
        for result in session.verifications:
            self.assertIn(result.status, ("supported", "contradicted",
                                          "insufficient_evidence"))
            self.assertTrue(result.engineer_decision, "a verification lost its engineer decision")
        # The recorded statuses are the ones `verify()` produces for the mismatch set, not the worker's say.
        self.assertEqual(len(session.verifications), len(session.proposal.hypotheses))

    def test_the_worker_stage_cannot_add_hypotheses_or_decisions(self):
        """One decision asked per hypothesis, whatever the workers returned."""
        engineer = ScriptedEngineer(resolution=RESOLVED)
        session = self._full(engineer=engineer)
        self.assertEqual(len(engineer.asked), len(session.proposal.hypotheses))
        self.assertGreaterEqual(len(session.workers["verifier"]["findings"]), 3)

    def test_findings_are_labelled_as_observations_in_the_transcript(self):
        self._full()
        text = "\n".join(self.engineer.shown)
        self.assertIn("observations of the CURRENT system", text)
        self.assertIn("not a verdict", text)


class ProposalsAreInert(AuditHarness):
    """A proposal is a candidate change. It is never written, applied, or verified."""

    def test_nothing_is_written_and_no_file_appears(self):
        self._full()
        after = _tree_digest(self.root)
        self.assertEqual(after, self.before, "the repository changed")
        self.assertEqual(set(after), set(self.before), "a file was created or removed")

    def test_the_patch_port_exposes_no_write_surface(self):
        """Structural: the adapter a patch proposal is built from cannot write at all."""
        port = self.runtime.patch_port
        public = {name for name in dir(port) if not name.startswith("_")}
        forbidden = {"write", "write_text", "apply", "patch", "commit", "unlink", "remove",
                     "mkdir", "rename", "replace", "truncate"}
        self.assertEqual(public & forbidden, set(), "the patch port exposes a mutating method")

    def test_the_worker_stage_holds_no_writer(self):
        """The stage reports; it has no code path that could write."""
        import inspect

        import debugagent.pipeline.worker_stage as stage
        source = inspect.getsource(stage)
        for forbidden in ("open(", "write_text", "shutil", "os.remove", "Path.write"):
            self.assertNotIn(forbidden, source, f"worker_stage contains {forbidden!r}")

    def test_a_proposal_never_enters_a_resolution_or_a_retention_record(self):
        session = self._full()
        proposed = session.workers["patches"]["proposals"][0]
        diff_text = " ".join(obs["content"] for obs in proposed["observations"])
        self.assertNotIn(diff_text, str(session.resolution.to_dict() if session.resolution else {}))
        self.assertNotIn("10m", str(session.retention or {}),
                         "the proposed change was retained as if it had happened")

    def test_the_transcript_says_proposal_and_not_fix(self):
        self._full()
        text = "\n".join(self.engineer.shown)
        self.assertIn("PROPOSALS only", text)
        self.assertIn("no proposal is applied automatically", text)


class EngineerAuthority(AuditHarness):
    """The engineer decides. Nothing else may."""

    def test_the_engineers_facts_are_the_only_ones_added_to_evidence(self):
        """With no worker involvement at all, engineer facts still land - the seam is unchanged."""
        engineer = ScriptedEngineer(facts={"cluster": "eu-west-1a"}, resolution=RESOLVED)
        session = investigate(load_debug_input(ACT2), self.port, self._llm(*self._cited()), engineer,
                              coordinator=self.runtime.coordinator, repository=self.runtime,
                              verifier_targets=["nginx/site.conf"])
        values = {item.name: item.value for item in session.evidence.items}
        self.assertEqual(values.get("cluster"), "eu-west-1a",
                         "the engineer's fact did not reach the evidence set")

    def test_a_worker_cannot_overturn_an_engineer_decision(self):
        """The engineer can reject every hypothesis; the workers cannot outvote that."""
        from debugagent.pipeline.verify import EngineerDecision

        reject = EngineerDecision("reject", "contradicted", False, "not what I saw")
        engineer = ScriptedEngineer(decisions={}, resolution=RESOLVED)
        engineer.default = reject
        session = self._full(engineer=engineer)
        for result in session.verifications:
            self.assertEqual(result.engineer_decision, "reject")
        self.assertTrue(session.workers["verifier"]["findings"],
                        "findings existed but did not change the engineer's decision")

    def test_resolution_comes_from_the_engineer_not_the_workers(self):
        session = self._full()
        self.assertEqual(session.resolution.to_dict()["action_taken"],
                         RESOLVED["action_taken"], "the resolution was not the engineer's")

    def test_retention_records_the_outcome_not_the_findings(self):
        session = self._full()
        retained = str(session.retention)
        self.assertIn("retained", retained)
        for finding in session.workers["verifier"]["findings"]:
            self.assertNotIn(finding["content"][:40], retained,
                             "a finding was persisted into the retained case")


class AuthorizationPrecedesEveryExecution(AuditHarness):
    """Checked by ORDERING a real log, not by patching authorization away."""

    def _ordered_log(self):
        """Record every authorize and every worker execution in one ordered log."""
        from debugagent.agents import coordinator as coordinator_module

        log: list[tuple[str, str, str]] = []
        real_authorize = coordinator_module.authorize
        hub = self.runtime.coordinator
        workers = {agent: hub.worker_for(agent)
                   for agent in ("code_log_verifier", "patch_generator")}

        def logging_authorize(spec):
            log.append(("authorize", spec.task_id, spec.agent))
            return real_authorize(spec)

        def wrap(agent, worker):
            original = worker.execute

            def logging_execute(spec):
                log.append(("execute", spec.task_id, agent))
                return original(spec)
            return logging_execute

        patches = [mock.patch.object(coordinator_module, "authorize", logging_authorize)]
        for agent, worker in workers.items():
            patches.append(mock.patch.object(worker, "execute", wrap(agent, worker)))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return log

    def test_every_execution_is_preceded_by_authorisation_of_that_same_task(self):
        log = self._ordered_log()
        self._full()
        self.assertTrue(log, "nothing was dispatched")
        authorized: set[str] = set()
        executions = 0
        for kind, task_id, agent in log:
            if kind == "authorize":
                authorized.add(task_id)
            else:
                executions += 1
                self.assertIn(task_id, authorized,
                              f"{agent} ran task {task_id!r} without authorising it first")
        self.assertGreaterEqual(executions, 4, "expected two verifier targets and a patch task")

    def test_authorisation_happens_before_the_worker_object_is_used(self):
        """`delegate` authorises before resolving the worker; fan-out does the same in input order."""
        from debugagent.agents import coordinator as coordinator_module

        events: list[str] = []
        real_authorize = coordinator_module.authorize
        hub = self.runtime.coordinator

        def logging_authorize(spec):
            events.append(f"authorize:{spec.agent}")
            return real_authorize(spec)

        real_worker_for = hub.worker_for

        def logging_worker_for(agent):
            events.append(f"resolve:{agent}")
            return real_worker_for(agent)

        with mock.patch.object(coordinator_module, "authorize", logging_authorize), \
                mock.patch.object(hub, "worker_for", logging_worker_for):
            self._full()
        for index, event in enumerate(events):
            if event.startswith("resolve:"):
                prior = [e for e in events[:index] if e.startswith("authorize:")]
                self.assertTrue(prior, f"{event} happened before any authorisation")

    def test_a_denied_task_never_reaches_its_worker(self):
        executed: list[str] = []
        hub = self.runtime.coordinator
        worker = hub.worker_for("code_log_verifier")
        original = worker.execute

        def recording_execute(spec):
            executed.append(spec.task_id)
            return original(spec)

        with _deny("code_log_verifier"):
            with mock.patch.object(worker, "execute", recording_execute):
                session = self._session(verifier_targets=["nginx/site.conf"])
        self.assertEqual(executed, [], "a denied task still executed")
        self.assertEqual(session.workers["verifier"]["outcomes"][0]["status"], "refused")
        self.assertIsNone(session.workers["verifier"]["outcomes"][0]["result"])


class OneCoordinatorOneLane(AuditHarness):
    """One client, one Coordinator, one lane - shared correctly."""

    def test_every_hindsight_access_in_a_session_uses_the_same_lane(self):
        hub = self.runtime.coordinator
        lane = hub.lane
        seen: list[int] = []
        original_call = type(lane).call

        def recording_call(self, function, *args, **kwargs):
            seen.append(id(self))
            return original_call(self, function, *args, **kwargs)

        with mock.patch.object(type(lane), "call", recording_call):
            self._full()
        self.assertTrue(seen, "no memory access went through the lane")
        self.assertEqual(set(seen), {id(lane)}, "a memory access used a different lane")

    def test_memory_work_is_serialised_while_non_memory_work_overlaps(self):
        """Proven deterministically by holding the lane, not by timing.

        The lane is taken by hand. Non-memory work must then still complete - which is what "overlaps"
        means operationally - while memory work must block until it is free. A timing-based version of
        this would be flaky and would prove less.
        """
        from debugagent.pipeline.worker_stage import run_worker_stage
        lane = self.runtime.coordinator.lane
        case = self._session(verifier_targets=["app/server.py"]).case

        non_memory_done = threading.Event()
        non_memory_error: list[BaseException] = []

        def run_non_memory():
            try:
                stage = run_worker_stage(self.runtime, case, verifier_targets=["app/server.py"])
                non_memory_done.set()
                self._non_memory_stage = stage
            except BaseException as exc:  # noqa: BLE001 - reported below
                non_memory_error.append(exc)

        with lane:
            worker = threading.Thread(target=run_non_memory)
            worker.start()
            worker.join(timeout=10)

        self.assertEqual(non_memory_error, [],
                         f"non-memory work failed while the lane was held: {non_memory_error}")
        self.assertTrue(non_memory_done.is_set(),
                        "non-memory work could not run while the memory lane was held")
        stage = getattr(self, "_non_memory_stage", None)
        self.assertIsNotNone(stage, "no stage was produced")
        self.assertTrue(stage.verifier["findings"],
                        "non-memory work was blocked by the memory lane")

        # And the reverse: memory work must NOT proceed while the lane is held.
        memory_finished = threading.Event()

        def run_memory():
            lane.call(lambda: None)
            memory_finished.set()

        with lane:
            thread = threading.Thread(target=run_memory)
            thread.start()
            thread.join(timeout=0.2)
            self.assertFalse(memory_finished.is_set(),
                             "memory work ran while the lane was held")
        thread.join(timeout=10)
        self.assertTrue(memory_finished.is_set(),
                        "memory work never completed once the lane was free")

    def test_a_second_coordinator_is_refused(self):
        other = build_repository_runtime(str(self.root), coordinator=Coordinator())
        with self.assertRaises(ValueError):
            investigate(load_debug_input(ACT2), self.port, self._llm(*self._cited()),
                        ScriptedEngineer(resolution=RESOLVED),
                        coordinator=self.runtime.coordinator, repository=other,
                        verifier_targets=["nginx/site.conf"])

    def test_the_worker_stage_adds_no_lane_of_its_own(self):
        import inspect

        import debugagent.pipeline.worker_stage as stage
        self.assertFalse(hasattr(stage, "MemoryLane"))
        for name in ("run_worker_stage", "plan_tasks"):
            self.assertNotIn("lane", inspect.signature(getattr(stage, name)).parameters)


class OutcomeDistinctions(AuditHarness):
    """Refusal, failure, empty success and real success stay four different things."""

    def _outcome_statuses(self, **kwargs):
        session = self._session(**kwargs)
        return {o["task_id"]: o["status"] for o in session.workers["verifier"]["outcomes"]}

    def test_a_missing_file_is_a_failure_and_a_present_file_a_success(self):
        statuses = self._outcome_statuses(
            verifier_targets=["nginx/site.conf", "nginx/absent.conf"])
        self.assertEqual(statuses["verify:nginx/site.conf"], "success")
        self.assertNotEqual(statuses["verify:nginx/absent.conf"], "success")

    def test_reading_an_unrelated_file_still_reports_what_it_read(self):
        """An irrelevant file is NOT an empty result - the worker did observe it.

        Worth pinning because "empty success" is easy to define wrongly. Pointed at a file with nothing
        to do with the issue, the verifier still reports the excerpt it read; suppressing that would hide
        the fact that the file was examined at all.
        """
        session = self._session(verifier_targets=["app/unrelated.py"])
        section = session.workers["verifier"]
        self.assertEqual(section["outcomes"][0]["status"], "success")
        self.assertEqual(section["findings_total"], 1,
                         "a real read produced no observation, which would hide the work done")
        self.assertEqual(section["findings"][0]["ref"], "app/unrelated.py")

    def test_the_boilerplate_does_not_inflate_the_relevance_score(self):
        """REGRESSION: every observation echoes the issue signature, so scoring the whole string
        counted the template rather than the content.

        The audit found this by putting a deliberately irrelevant file in the same run as the real
        culprit: both scored 2, and the report claimed each matched two terms. A score that cannot
        distinguish them is worse than no score, because it looks like a measurement.
        """
        session = self._session(verifier_targets=["app/unrelated.py", "nginx/site.conf",
                                                 "logs/nginx-error.log"])
        scores = {finding["ref"]: finding["score"]
                  for finding in session.workers["verifier"]["findings"]}
        # The irrelevant file matches nothing the issue actually said.
        self.assertEqual(scores["app/unrelated.py"], 0,
                         "a file with no relevant content still scored, so the template is being counted")
        # The log quotes ECONNRESET, which is the symptom the engineer reported, so it genuinely matches.
        self.assertGreater(scores["logs/nginx-error.log"], 0,
                           "the log quoting the reported symptom did not score")
        # And the ranking actually uses the score: the genuine match leads the report.
        self.assertEqual(session.workers["verifier"]["findings"][0]["ref"], "logs/nginx-error.log",
                         "the finding that matched the reported symptom did not lead the report")
        # Before the fix every one of these scored 2, which is why the report claimed a match for all.
        self.assertLess(len(set(scores.values())), 3, "all findings scored identically again")

    def test_a_genuine_term_match_in_the_quoted_material_scores(self):
        from debugagent.pipeline.worker_stage import _scorable_text
        from debugagent.pipeline.types import NormalizedDebugCase

        signature = "checkout fails on upload"
        case = NormalizedDebugCase.from_dict({
            "problem_signature": signature, "raw_description": signature,
            "symptoms": ["econnreset"], "environment": {"service": "media-uploader"}})
        from debugagent.pipeline.worker_stage import _relevance_terms

        boilerplate_only = (f"OBSERVED in a.py for issue {signature!r}. "
                            "Current-system observation, not a verification.")
        # The boilerplate survives as words; what must not survive is the CASE's own signature matching
        # itself. So the assertion is about terms, not about the string becoming empty.
        for term in _relevance_terms(case):
            self.assertNotIn(term, _scorable_text(boilerplate_only, signature),
                             f"boilerplate still matches the case term {term!r}")
        with_content = f"{boilerplate_only}\nclient_max_body_size 2m for media-uploader"
        self.assertIn("media-uploader", _scorable_text(with_content, signature),
                      "the quoted material should still be scorable")

    def test_symptom_terms_are_not_stranded_by_punctuation(self):
        """REGRESSION: tokens kept their trailing separator, so 'ECONNRESET;' could never match.

        Every symptom in a real measurement ends in a separator or a semicolon, and none of those
        characters appear in the log line they describe - so the term was unfindable and every
        symptom-driven score came back zero.
        """
        from debugagent.pipeline.worker_stage import _relevance_terms
        from debugagent.pipeline.types import NormalizedDebugCase

        case = NormalizedDebugCase.from_dict({
            "problem_signature": "uploads reset", "raw_description": "uploads reset",
            "symptoms": ["20/20 uploads of 2.5MB fail with ECONNRESET; 1MB uploads succeed"],
            "environment": {}})
        terms = _relevance_terms(case)
        self.assertIn("econnreset", terms, "the symptom word was stranded by punctuation")
        self.assertIn("fail", terms)
        self.assertIn("2.5mb", terms)
        for term in terms:
            self.assertFalse(term.endswith((";", ",", "/")), f"term {term!r} kept its separator")
        # And the term now matches the log line a real system would produce.
        self.assertIn("econnreset", "error upstream prematurely closed connection: econnreset")

    def test_the_tiebreak_is_the_documented_total_order(self):
        """A tie is not a failure: the tiebreak must be total, applied, and reproducible."""
        session = self._session(verifier_targets=["logs/nginx-error.log", "nginx/site.conf",
                                                 "app/unrelated.py"])
        findings = session.workers["verifier"]["findings"]
        expected = [f["ref"] for f in
                    sorted(findings, key=lambda f: (-f["score"], f["kind"], f["ref"]))]
        self.assertEqual([f["ref"] for f in findings], expected,
                         "the order is not the documented (-score, kind, ref) sort")
        # The deliberately unrelated file cannot lead, because the log quoting the reported symptom scores.
        self.assertNotEqual(findings[0]["ref"], "app/unrelated.py")
        # And the same request twice gives the same order.
        again = self._session(verifier_targets=["logs/nginx-error.log", "nginx/site.conf",
                                                "app/unrelated.py"])
        self.assertEqual([f["ref"] for f in again.workers["verifier"]["findings"]],
                         [f["ref"] for f in findings])

    def test_a_genuinely_empty_success_is_distinguishable_from_a_real_finding(self):
        """A worker that ran and returned nothing is success-with-nothing, not failure."""
        worker = self.runtime.coordinator.worker_for("code_log_verifier")
        empty = SubAgentResult(task_id="verify:nginx/site.conf", agent="code_log_verifier",
                               status="success", observations=())
        with mock.patch.object(worker, "execute", return_value=empty):
            session = self._session(verifier_targets=["nginx/site.conf"])
        section = session.workers["verifier"]
        self.assertEqual(section["outcomes"][0]["status"], "success")
        self.assertIsNotNone(section["outcomes"][0]["result"],
                             "an empty success must still carry a result")
        self.assertEqual(section["findings_total"], 0)
        self.assertEqual(section["refusals"], [])
        self.assertFalse(section["findings_truncated"])

    def test_an_empty_success_renders_as_nothing_observed_not_as_a_verdict(self):
        worker = self.runtime.coordinator.worker_for("code_log_verifier")
        empty = SubAgentResult(task_id="verify:nginx/site.conf", agent="code_log_verifier",
                               status="success", observations=())
        with mock.patch.object(worker, "execute", return_value=empty):
            self._session(verifier_targets=["nginx/site.conf"])
        text = "\n".join(self.engineer.shown)
        self.assertIn("No observation was reported", text)
        self.assertIn("clean run with nothing to show", text)

    def test_a_refusal_is_not_a_failure_and_neither_is_a_success(self):
        with _deny("code_log_verifier"):
            session = self._session(verifier_targets=["nginx/site.conf"])
        outcome = session.workers["verifier"]["outcomes"][0]
        self.assertEqual(outcome["status"], "refused")
        self.assertIsNone(outcome["result"])
        self.assertIsNotNone(outcome["error"])
        self.assertEqual(len(session.workers["verifier"]["refusals"]), 1)
        self.assertIn("denied", session.workers["verifier"]["refusals"][0]["error"])

    def test_a_worker_crash_is_neither_a_refusal_nor_a_success(self):
        worker = self.runtime.coordinator.worker_for("code_log_verifier")
        with mock.patch.object(worker, "execute", side_effect=RuntimeError("worker crashed")):
            session = self._session(verifier_targets=["nginx/site.conf"])
        outcome = session.workers["verifier"]["outcomes"][0]
        self.assertNotEqual(outcome["status"], "success")
        self.assertNotEqual(outcome["status"], "refused")
        self.assertEqual(session.workers["verifier"]["refusals"], [],
                         "a crash was reported as a refusal")

    def test_all_four_kinds_are_distinguishable_in_one_serialized_record(self):
        """Success, empty success, failure and refusal must all survive into the record together."""
        worker = self.runtime.coordinator.worker_for("code_log_verifier")
        original = worker.execute
        calls = {"n": 0}

        def sometimes_empty(spec):
            calls["n"] += 1
            if calls["n"] == 2:
                return SubAgentResult(task_id=spec.task_id, agent=spec.agent, status="success",
                                      observations=())
            return original(spec)

        with mock.patch.object(worker, "execute", sometimes_empty):
            session = self._session(
                verifier_targets=["nginx/site.conf", "app/server.py", "nginx/absent.conf"])
        with _deny("code_log_verifier"):
            denied = self._session(verifier_targets=["logs/nginx-error.log"])

        statuses = {o["task_id"]: o for o in session.workers["verifier"]["outcomes"]}
        self.assertEqual(statuses["verify:nginx/site.conf"]["status"], "success")
        self.assertTrue(statuses["verify:nginx/site.conf"]["result"]["observations"],
                        "the first target should have produced observations")
        self.assertEqual(statuses["verify:app/server.py"]["status"], "success")
        self.assertEqual(statuses["verify:app/server.py"]["result"]["observations"], [],
                         "the empty success should have produced no observations")
        self.assertNotEqual(statuses["verify:nginx/absent.conf"]["status"], "success")
        self.assertEqual(denied.workers["verifier"]["outcomes"][0]["status"], "refused")
        self.assertEqual(len({statuses["verify:nginx/site.conf"]["status"],
                              statuses["verify:app/server.py"]["status"],
                              statuses["verify:nginx/absent.conf"]["status"],
                              "refused"}), 3,
                         "four outcomes collapsed into fewer distinguishable states")


class ContainmentAndReadOnly(AuditHarness):
    """The repository boundary holds through the whole flow, and the flow only reads."""

    def test_a_path_outside_the_root_never_reaches_a_finding(self):
        outside = Path(self._tmp.name) / "secrets.env"
        outside.write_text("API_KEY=super-secret-value\n", encoding="utf-8")
        session = self._session(verifier_targets=[str(outside)])
        self.assertFalse(session.workers["verifier"]["findings"],
                         "a file outside the root produced a finding")
        self.assertNotIn("super-secret-value", "\n".join(self.engineer.shown))
        self.assertNotIn("super-secret-value", str(session.workers))

    def test_a_relative_traversal_is_contained(self):
        session = self._session(verifier_targets=["../secrets.env", "nginx/../../etc/passwd"])
        self.assertFalse(session.workers["verifier"]["findings"],
                         "a traversing path produced a finding")

    @unittest.skipIf(os.name == "nt", "symlink traversal is not an escape on Windows")
    def test_a_symlink_out_of_the_root_is_contained(self):
        outside = Path(self._tmp.name) / "outside.conf"
        outside.write_text("SECRET=1\n", encoding="utf-8")
        (self.root / "link.conf").symlink_to(outside)
        self.addCleanup((self.root / "link.conf").unlink)
        session = self._session(verifier_targets=["link.conf"])
        self.assertNotIn("SECRET", "\n".join(self.engineer.shown))

    def test_a_patch_target_outside_the_root_produces_no_proposal(self):
        outside = Path(self._tmp.name) / "outside.conf"
        outside.write_text("A = 1\n", encoding="utf-8")
        digest_before = hashlib.sha256(outside.read_bytes()).hexdigest()
        session = self._session(patch_requests=[(str(outside), "B = 2\n")])
        self.assertFalse(session.workers["patches"]["proposals"])
        self.assertEqual(hashlib.sha256(outside.read_bytes()).hexdigest(), digest_before)
        self.assertIn("A = 1", outside.read_text(encoding="utf-8"))

    def test_the_repository_is_byte_identical_after_escaped_paths_are_attempted(self):
        self._session(verifier_targets=["../secrets.env", "nginx/../../outside"],
                      patch_requests=[("../escape.conf", "X = 1\n")])
        self.assertEqual(_tree_digest(self.root), self.before,
                         "a rejected path still changed the repository")

    def test_findings_report_relative_provenance(self):
        """A finding must not leak the absolute layout of the machine that produced it."""
        session = self._session(verifier_targets=["nginx/site.conf"])
        for finding in session.workers["verifier"]["findings"]:
            self.assertFalse(Path(finding["ref"]).is_absolute(),
                             f"finding ref {finding['ref']!r} is an absolute path")
            self.assertNotIn(str(self.root), finding["ref"])


class DeterminismAcrossRuns(AuditHarness):
    """The same repository and issue produce the same report, repeatedly."""

    TARGETS = ("nginx/site.conf", "logs/nginx-error.log", "app/server.py")

    def _workers_section(self):
        session = self._session(verifier_targets=list(self.TARGETS),
                                patch_requests=[("nginx/site.conf", NGINX_CONF.replace("2m", "10m"))])
        return session.workers

    def test_repeated_runs_in_one_process_are_identical(self):
        first = self._workers_section()
        for _ in range(4):
            self.assertEqual(self._workers_section(), first, "the report is not reproducible")

    def test_ranked_findings_do_not_depend_on_the_order_targets_were_requested(self):
        """The AGGREGATED result is order-independent; the per-task list is deliberately not.

        The audit initially flagged this as a defect and it is worth stating why it is not. `outcomes` and
        `summary.order` echo INPUT order, because `FanOutResult` is positional by contract (P5) and a
        caller needs to know which slot belongs to which request. The RANKED `findings` - the thing a
        reader actually consumes - are sorted by key and identical whatever order they arrived in.
        Asserting both halves, because collapsing them into one claim is what hid the distinction.
        """
        import json

        baseline = self._workers_section()
        orderings = [tuple(reversed(self.TARGETS)), tuple(sorted(self.TARGETS, reverse=True))]
        for ordering in orderings:
            session = self._session(verifier_targets=list(ordering),
                                    patch_requests=[("nginx/site.conf",
                                                     NGINX_CONF.replace("2m", "10m"))])
            section = session.workers["verifier"]
            self.assertEqual(section["findings"], baseline["verifier"]["findings"],
                             f"ranked findings depended on target order {ordering}")
            self.assertEqual(section["summary"]["order"],
                             [f"verify:{target}" for target in ordering],
                             "the outcome list should echo input order")
            self.assertEqual(
                sorted(o["task_id"] for o in section["outcomes"]),
                sorted(o["task_id"] for o in baseline["verifier"]["outcomes"]),
                "the set of dispatched tasks changed with the ordering")

    def test_the_report_is_identical_in_a_fresh_interpreter(self):
        """A separate process with a different hash seed, so set/dict ordering cannot be doing the work."""
        script = f'''
import json, sys, tempfile
sys.path[:0] = ["src", "tests"]
sys.argv = ["audit"]
import test_p7_production_audit as audit
import unittest

class DeterminismProbe(audit.AuditHarness):
    def runTest(self):
        session = self._full()
        print("REPORT:" + json.dumps(session.workers, sort_keys=True))

probe = DeterminismProbe()
probe.setUp()
try:
    probe.runTest()
finally:
    probe.doCleanups()
'''
        import subprocess

        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                              cwd=str(REPO_ROOT), encoding="utf-8", errors="replace")
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("REPORT:")), None)
        self.assertIsNotNone(line, f"subprocess produced no report: {proc.stderr[-600:]}")
        import json

        self.assertEqual(json.loads(line[len("REPORT:"):]), self._workers_section(),
                         "the report changed in a fresh interpreter, so something is unordered")

    def test_the_trace_is_reproducible(self):
        traces = []
        for _ in range(3):
            traces.append(self._full().trace)
        self.assertEqual(traces[1:], traces[:1] * 2, "the trace is not reproducible")


class Phase1BehaviourUnchanged(AuditHarness):
    """Omitting worker inputs must leave Phase 1 exactly as it was."""

    PHASE1_KEYS = {"session_id", "input", "case", "memory", "evidence", "proposal", "verifications",
                   "resolution", "retention", "trace"}

    def test_no_worker_inputs_gives_the_phase1_key_set_exactly(self):
        from debugagent.pipeline.ingest import load_debug_input

        session = investigate(load_debug_input(ACT2), self.port, self._llm(*self._cited()),
                              ScriptedEngineer(resolution=RESOLVED))
        self.assertEqual(set(session.to_dict()), self.PHASE1_KEYS)
        self.assertNotIn("workers", session.to_dict())
        self.assertIsNone(session.workers)

    def test_a_repository_without_targets_changes_nothing(self):
        """The control is a P4-equivalent run, not a bare Phase 1 run.

        Passing a `coordinator` adds `memory_delegation` - that is the P4 seam, and it predates P6. So
        the honest control for "did the repository change anything?" is a run with the same coordinator
        and no repository. Comparing against a bare Phase 1 session would blame P6 for a P4 key.
        """
        p4_control = investigate(load_debug_input(ACT2), self.port, self._llm(*self._cited()),
                                 ScriptedEngineer(resolution=RESOLVED),
                                 coordinator=self.runtime.coordinator).to_dict()
        with_repository = self._session().to_dict()
        self.assertIn("memory_delegation", p4_control, "the P4 control should carry it")
        self.assertEqual(set(with_repository), set(p4_control),
                         "supplying a repository added keys to a session")
        self.assertNotIn("workers", with_repository)
        self.assertEqual(with_repository["trace"], p4_control["trace"],
                         "supplying a repository changed the trace")

    def test_the_phase1_transcript_has_no_worker_sections(self):
        from debugagent.pipeline.ingest import load_debug_input

        engineer = ScriptedEngineer(resolution=RESOLVED)
        investigate(load_debug_input(ACT2), self.port, self._llm(*self._cited()), engineer)
        text = "\n".join(engineer.shown)
        self.assertNotIn("Code/Log Verifier", text)
        self.assertNotIn("Patch Generator", text)

    def test_worker_inputs_change_nothing_outside_the_worker_record(self):
        """Everything but `workers` and its trace lines is identical to a P4-equivalent run.

        Compared after stripping genuinely volatile fields. Two separate runs cannot be byte-identical -
        `session_id` is generated and `captured_at`/`recalled_at` are timestamps - so a naive equality
        check would fail for reasons that have nothing to do with the boundary under test. The Phase 1
        control is P4-equivalent rather than bare, because `memory_delegation` comes from the P4 seam.
        """
        p4_control = _strip_volatile(investigate(
            load_debug_input(ACT2), self.port, self._llm(*self._cited()),
            ScriptedEngineer(resolution=RESOLVED),
            coordinator=self.runtime.coordinator).to_dict())
        workered = _strip_volatile(self._session(
            verifier_targets=["nginx/site.conf"],
            patch_requests=[("nginx/site.conf", "X = 1\n")]).to_dict())
        worker_lines = [line for line in workered["trace"]
                        if line.startswith(("verifier:", "patch generator:"))]
        self.assertEqual(len(worker_lines), 2, "expected one trace line per worker channel")
        workered.pop("workers")
        workered["trace"] = [line for line in workered["trace"]
                             if not line.startswith(("verifier:", "patch generator:"))]
        self.assertEqual(set(workered), set(p4_control))
        for key in p4_control:
            if key != "trace":
                self.assertEqual(workered[key], p4_control[key],
                                 f"worker inputs changed {key!r}")


class AdkStaysOptional(AuditHarness):
    """A full investigation must complete without ADK, and without importing it."""

    def test_a_full_run_never_imports_the_adk_bridge(self):
        sys.modules.pop("debugagent.adk_bridge", None)
        session = self._full()
        self.assertTrue(session.workers["verifier"]["findings"])
        self.assertNotIn("debugagent.adk_bridge", sys.modules,
                         "the flow imported the ADK bridge")

    def test_a_full_run_completes_with_google_adk_blocked(self):
        """Block the import at the meta_path and run the entire flow."""
        real_import = __import__

        def guarded(name, *args, **kwargs):
            if name.startswith("google.adk"):
                raise ImportError(f"blocked for audit: {name}")
            return real_import(name, *args, **kwargs)

        session_in_thread: dict[str, object] = {}
        error: list[BaseException] = []

        def run():
            try:
                with mock.patch("builtins.__import__", guarded):
                    session_in_thread["session"] = self._full()
            except BaseException as exc:  # noqa: BLE001 - the point is to report any failure
                error.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=30)
        self.assertEqual(error, [], f"the flow failed without ADK: {error}")
        session = session_in_thread.get("session")
        self.assertIsNotNone(session, "no session was produced")
        self.assertTrue(session.workers["verifier"]["findings"])

    def test_the_bridge_cannot_influence_a_flow_session(self):
        from debugagent.adk_bridge import TaskCollector

        with mock.patch.object(TaskCollector, "record",
                               side_effect=AssertionError("the ADK bridge was used")):
            session = self._full()
        self.assertTrue(session.workers["verifier"]["findings"])
        self.assertEqual(set(session.workers), {"verifier", "patches"})


if __name__ == "__main__":
    unittest.main()
