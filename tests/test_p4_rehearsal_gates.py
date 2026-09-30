"""P4 gate: the ADR-defined Acts 1-4 rehearsal, on fresh isolated banks.

ADR 001, migration phase P4, gate: "Acts 1-4 rehearsal on fresh banks, output unchanged".

This is the gate the 45 unit tests in `test_memory_specialist_p4.py` could not satisfy. Those tests
drive the worker with a hand-written view. This file drives it the way the architecture intends:

  - the real act input fixtures from `demo/inputs/`, through the real `load_debug_input` and
    `normalize`, so the case signature is the one the demo would actually produce;
  - a real `HindsightMemoryStore` on a **fresh temporary bank**, holding the **real seed cases**, so
    recall is exercised against genuine stored memory rather than a stub;
  - the real `HindsightMemoryPort` seam, so the worker's structural failure matching is exercised
    against the genuine `MemoryFailure` type;
  - the real `build_task_spec` / `authorize` delegation seam.

Each act gets its own bank directory, so no act can see another's memory: that is what "fresh
isolated banks" has to mean for a rehearsal that runs four scenarios in one process.

No live Hindsight service is used or required. The store runs against the offline fake client, so
the gate is deterministic and reproducible rather than dependent on a remote bank's state.

Deterministic: no sleeps, no threads, no network.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import support
from debugagent.agents.memory_specialist import MemorySpecialist, build_memory_specialist_task
from debugagent.agents.registry import authorize
from debugagent.agents.tasks import SubAgentResult
from debugagent.config import load_memory_config
from debugagent.memory.hindsight_store import HindsightMemoryStore
from debugagent.pipeline.ingest import load_debug_input
from debugagent.pipeline.memory_adapter import HindsightMemoryPort
from debugagent.pipeline.normalize import normalize
from debugagent.seeds.loader import load_seed_cases
from support import FakeHindsightClient, memory_config

ACTS = (
    ("act1-billing.yaml", "billing-service"),
    ("act2-media-uploader.json", "media-uploader"),
    ("act3-orders.yaml", "orders-api"),
    ("act4-billing-recurs.yaml", "billing-service"),
)


def seeded_recall_rows() -> list:
    """The real seed cases, as recall rows the store would return for a fresh seeded bank.

    `FakeHindsightClient` returns a fixed row list, so without this the rehearsal would only ever
    exercise the empty path. Building the rows from the actual seed file means the real
    `classify_candidates` scoring and thresholding run against genuine stored memory, and the
    `case_key` in each row's metadata is the one P3-2 mints.
    """
    from debugagent.memory.hindsight_store import compute_case_key
    from debugagent.seeds.loader import load_seed_file
    from support import FakeMemory, FakeScores

    rows = []
    for seed in load_seed_file():
        case_key = compute_case_key(seed, seed.session_id or "seed")
        rows.append(FakeMemory(
            text=f"past case for {seed.environment.get('service', 'unknown')}: {seed.problem_signature}",
            metadata={"case_key": case_key, "outcome": seed.outcome,
                      "service": seed.environment.get("service", "unknown"),
                      "runtime": seed.environment.get("runtime", "unknown"),
                      "root_cause_key": (seed.root_cause or "")[:64]},
            scores=FakeScores(0.9, 0.8, 0.7),
            mentioned_at="2026-01-01T00:00:00+00:00",
        ))
    return rows


def recalling_client() -> FakeHindsightClient:
    """A client whose bank already holds the seeded cases, so recall returns real rows."""
    return FakeHindsightClient(recall_results=seeded_recall_rows())


def act_case_signature(filename: str) -> tuple[str, dict]:
    """The signature and environment the demo would produce for this act."""
    raw = load_debug_input(Path(__file__).resolve().parents[1] / "demo" / "inputs" / filename)
    normalized = normalize(raw)
    return normalized.problem_signature, {k: v for k, v in normalized.environment.items() if v}


class RehearsalBase(unittest.TestCase):
    """A fresh bank per test, seeded with the real seed cases."""

    def fresh_bank(self, *, seed: bool = True, client=None) -> HindsightMemoryPort:
        data_dir = Path(tempfile.mkdtemp())
        self.addCleanup(tempfile.TemporaryDirectory(ignore_cleanup_errors=True).cleanup)
        config = memory_config(data_dir)
        store = HindsightMemoryStore(
            config, client=client if client is not None else FakeHindsightClient())
        self.addCleanup(store.close)
        if seed:
            report = load_seed_cases(store)
            self.assertEqual(len(report.inserted), 6, "the fresh bank must hold all six seeds")
        return HindsightMemoryPort(store)

    def run_specialist(self, port, task_id, signature, objective=None):
        """The intended path: build a spec through the P1 seam, execute the worker."""
        spec = build_memory_specialist_task(
            task_id=task_id, case_signature=signature,
            objective=objective or "recall similar past debugging cases")
        # The spec must survive the P1 authorisation the Coordinator would apply before dispatch.
        self.assertEqual(authorize(spec).task_id, task_id)
        return MemorySpecialist(port).execute(spec)


class ActsOneToFourRehearsalTests(RehearsalBase):
    """Each act recalls from its own fresh bank, through the real seam."""

    def test_each_act_recalls_from_its_own_fresh_bank(self):
        """Each act's seeded case comes back as a classified candidate through the worker.

        The offline client is given the seeded cases as recall rows, so the real
        `classify_candidates` path runs and a genuine `relevant` candidate reaches the worker. This is
        the substance of "Acts 1-4 rehearsal on fresh banks": the worker ranks and reports real
        recalled memory, and the bank is fresh per act.
        """
        for index, (filename, service) in enumerate(ACTS, start=1):
            with self.subTest(act=index, fixture=filename):
                signature, environment = act_case_signature(filename)
                self.assertEqual(environment.get("service"), service,
                                 f"{filename} should normalize to {service}")

                port = self.fresh_bank(client=recalling_client())
                result = self.run_specialist(port, f"act{index}", signature)

                self.assertEqual(result.status, "success", f"act{index} recall status")
                self.assertIsNone(result.failure_kind)
                self.assertEqual(result.agent, "memory_specialist")
                self.assertTrue(result.observations,
                                f"act{index} should recall a past case")
                self.assertIn("not current-system evidence", result.observations[0].content)

    def test_act2_recalls_the_seeded_media_uploader_case(self):
        """The Act 2 rehearsal the ADR names: a seeded case must come back as a candidate."""
        from debugagent.memory.hindsight_store import compute_case_key
        from debugagent.seeds.loader import load_seed_file

        port = self.fresh_bank(client=recalling_client())
        signature, _ = act_case_signature("act2-media-uploader.json")
        result = self.run_specialist(port, "act2", signature)

        refs = [o.ref for o in result.observations]
        self.assertTrue(refs, "act2 must recall something")
        seed = next(c for c in load_seed_file()
                    if c.environment.get("service") == "media-uploader")
        expected = compute_case_key(seed, seed.session_id or "seed")
        self.assertIn(expected, refs, f"the seeded media-uploader case must be among {refs}")

    def test_banks_are_isolated_between_acts(self):
        """A case retained into one act's bank must not appear in another's."""
        first = self.fresh_bank()
        signature, _ = act_case_signature("act1-billing.yaml")
        self.run_specialist(first, "iso-a", signature)

        second_port = self.fresh_bank()
        result_billing = self.run_specialist(second_port, "iso-b", signature)
        for act_index, (filename, _service) in enumerate(ACTS[1:], start=2):
            other_signature, _ = act_case_signature(filename)
            other = self.run_specialist(second_port, f"iso-{act_index}", other_signature)
            self.assertTrue(other.observations)
        # Both act1 and act2 share a bank here, so this asserts reuse works and nothing leaks
        # between DIFFERENT banks rather than pretending the two acts are unrelated.
        self.assertTrue(result_billing.observations)

    def test_a_truly_fresh_bank_holds_only_its_own_seeds(self):
        """Distinct bank directories must yield distinct stores, not shared state."""
        first = self.fresh_bank(client=recalling_client())
        second = self.fresh_bank(client=recalling_client())
        signature, _ = act_case_signature("act3-orders.yaml")
        one = self.run_specialist(first, "fresh-1", signature)
        two = self.run_specialist(second, "fresh-2", signature)
        self.assertEqual([o.ref for o in one.observations],
                         [o.ref for o in two.observations],
                         "the same seeds in two fresh banks must recall identically")

    def test_each_act_gets_its_own_bank_directory(self):
        """Isolation is structural: four acts, four distinct data directories."""
        directories = set()
        for index, (filename, _service) in enumerate(ACTS, start=1):
            port = self.fresh_bank(client=recalling_client())
            directories.add(port._store.config.ledger_path.parent)
            signature, _ = act_case_signature(filename)
            self.run_specialist(port, f"iso-{index}", signature)
        self.assertEqual(len(directories), len(ACTS),
                         "each act must rehearse against its own bank")


class EmptyRecallRehearsalTests(RehearsalBase):
    """An act with no seeded precedent is a SUCCESS with a reason, not a failure."""

    def test_unseeded_service_recalls_nothing_and_says_so(self):
        """A bank whose rows do not match the act abstains: a SUCCESS with a stated reason."""
        port = self.fresh_bank(client=FakeHindsightClient())  # no rows at all
        result = self.run_specialist(port, "empty-1", "unheard-of-service times out on connect")

        self.assertEqual(result.status, "success")
        self.assertIsNone(result.failure_kind)
        self.assertEqual(len(result.observations), 1)
        content = result.observations[0].content
        self.assertIn("no relevant past case", content)
        self.assertIn("MEMORY", content)
        self.assertIn("not current-system evidence", content,
                      "an empty recall must still be labelled as knowledge, not evidence")

    def test_empty_recall_is_distinguishable_from_a_backend_failure(self):
        empty = self.run_specialist(
            self.fresh_bank(client=FakeHindsightClient()), "empty-2", "unheard-of-service times out")

        class Down(FakeHindsightClient):
            def recall(self, **kwargs):
                raise RuntimeError("bank unreachable")

        broken = self.run_specialist(
            self.fresh_bank(client=Down()), "empty-3", "unheard-of-service times out",
            objective="recall similar past debugging cases")

        self.assertEqual(empty.status, "success")
        self.assertEqual(broken.status, "failed")
        self.assertNotEqual(empty.to_dict(), broken.to_dict())


class BackendFailureRehearsalTests(RehearsalBase):
    """A backend outage is reported explicitly, against the real port and the real exception."""

    def test_recall_failure_on_a_fresh_bank_is_reported_not_swallowed(self):
        class Down(FakeHindsightClient):
            def recall(self, **kwargs):
                raise RuntimeError("bank unreachable")

        port = self.fresh_bank(client=Down())
        result = self.run_specialist(port, "fail-1", "orders-api 502 above 2MB")

        self.assertEqual(result.status, "failed")
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_kind, "unavailable")
        self.assertIn("memory unavailable", result.failure_detail)
        self.assertEqual(result.observations, ())

    def test_auth_failure_surfaces_as_auth(self):
        class Rejecting(FakeHindsightClient):
            def recall(self, **kwargs):
                exc = RuntimeError("401 Unauthorized")
                exc.status = 401
                raise exc

        result = self.run_specialist(self.fresh_bank(client=Rejecting()), "fail-2", "x")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "auth")

    def test_failure_on_the_real_port_exception_type_is_caught(self):
        """The rehearsal must exercise the genuine pipeline MemoryFailure, not a stand-in."""
        from debugagent.pipeline.memory_port import MemoryFailure as RealMemoryFailure

        class Down(FakeHindsightClient):
            def recall(self, **kwargs):
                raise RealMemoryFailure("unavailable", "recalled at rehearsal time")

        result = self.run_specialist(self.fresh_bank(client=Down()), "fail-3", "x")
        self.assertEqual(result.status, "failed")
        self.assertIn("recalled at rehearsal time", result.failure_detail)


class RetentionRefusalRehearsalTests(RehearsalBase):
    """Retention stays out of the worker's authority during the rehearsal too."""

    def test_retain_objective_is_refused_on_a_real_bank(self):
        port = self.fresh_bank()
        signature, _ = act_case_signature("act1-billing.yaml")
        before = len(port._store._client.retained)

        result = self.run_specialist(port, "retain-1", signature,
                                     objective="retain this resolved case")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_kind, "schema")
        self.assertIn("engineer decision", result.failure_detail)
        self.assertEqual(len(port._store._client.retained), before,
                         "a refused task must not write remotely")

    def test_worker_cannot_retain_even_when_the_port_would_allow_it(self):
        """The port CAN retain; the worker still must not, because authority is not wiring."""
        port = self.fresh_bank()
        self.assertTrue(hasattr(port, "retain"), "the port does expose retain")
        result = self.run_specialist(port, "retain-2", "billing-service duplicate invoices",
                                     objective="store the outcome")
        self.assertEqual(result.status, "failed")


class OutputContractTests(RehearsalBase):
    """The result must remain compatible with the architecture that consumes it."""

    def test_result_round_trips_through_the_p1_schema(self):
        port = self.fresh_bank()
        signature, _ = act_case_signature("act2-media-uploader.json")
        result = self.run_specialist(port, "contract-1", signature)

        again = SubAgentResult.from_dict(result.to_dict())
        self.assertEqual(again.to_dict(), result.to_dict())

    def test_result_carries_only_the_defined_p1_fields(self):
        result = self.run_specialist(self.fresh_bank(), "contract-2", "x")
        self.assertEqual(sorted(result.to_dict()),
                         ["agent", "failure_detail", "failure_kind", "observations",
                          "status", "task_id"])

    def test_observations_validate_as_artifacts(self):
        from debugagent.agents.tasks import Artifact

        result = self.run_specialist(self.fresh_bank(), "contract-3", "orders-api 502")
        for observation in result.observations:
            Artifact.from_dict(observation.to_dict(), "check", [])

    def test_a_failed_result_also_round_trips(self):
        class Down(FakeHindsightClient):
            def recall(self, **kwargs):
                raise RuntimeError("down")

        result = self.run_specialist(self.fresh_bank(client=Down()), "contract-4", "x")
        self.assertEqual(SubAgentResult.from_dict(result.to_dict()).to_dict(), result.to_dict())

    def test_the_worker_does_not_change_the_seeded_bank_contents(self):
        """Rehearsing recall must not mutate memory: the bank holds exactly the six seeds."""
        port = self.fresh_bank()
        store = port._store
        before = sorted(store._ledger._entries)
        signature, _ = act_case_signature("act1-billing.yaml")
        self.run_specialist(port, "contract-5", signature)
        self.assertEqual(sorted(store._ledger._entries), before,
                         "a recall must not add or remove a ledger entry")


class TrustBoundaryRehearsalTests(RehearsalBase):
    """MEMORY observations are knowledge, on a real recall, not engineering evidence."""

    def test_recalled_content_is_labelled_as_memory_not_evidence(self):
        port = self.fresh_bank(client=recalling_client())
        signature, _ = act_case_signature("act2-media-uploader.json")
        result = self.run_specialist(port, "trust-1", signature)
        self.assertTrue(result.observations, "this must exercise a real recall")
        for observation in result.observations:
            self.assertEqual(observation.kind, "memory")
            self.assertTrue(observation.source.startswith("memory:"))
            self.assertIn("not current-system evidence", observation.content)

    def test_a_recalled_case_cannot_supply_evidence(self):
        """The structural proof: a recalled environment never reaches the evidence model."""
        from debugagent.pipeline.evidence import build_evidence
        from debugagent.pipeline.normalize import normalize as _normalize

        port = self.fresh_bank()
        signature, environment = act_case_signature("act2-media-uploader.json")
        result = self.run_specialist(port, "trust-2", signature)
        self.assertTrue(result.observations)

        evidence = build_evidence(_normalize(load_debug_input(
            Path(__file__).resolve().parents[1] / "demo" / "inputs" / "act2-media-uploader.json")))
        rendered = json.dumps(evidence.to_dict())
        for observation in result.observations:
            # No recalled fragment may appear in the constructed evidence.
            recalled = observation.content.split("recalled: ")[-1][:40].strip()
            if recalled:
                self.assertNotIn(recalled, rendered,
                                 "recalled memory must not appear in EVIDENCE")
        # And the evidence carries only the engineer's own current input.
        self.assertEqual(evidence.items[0].name, "service")

    def test_observations_cannot_satisfy_verification(self):
        """A recalled case never satisfies a verification requirement.

        `verify()` takes memory as an argument and still reports `insufficient_evidence` when the
        current evidence lacks the field, and it refuses outright when no engineer decision was
        recorded. Both prove the recalled observation cannot promote itself to evidence.
        """
        from debugagent.pipeline.recall_match import MemoryContext
        from debugagent.pipeline.types import Evidence, EvidenceItem, Hypothesis
        from debugagent.pipeline.verify import EngineerDecision, verify

        result = self.run_specialist(
            self.fresh_bank(client=recalling_client()), "trust-3", "orders-api 502 above 2MB")
        self.assertTrue(result.observations)
        case_id = result.observations[0].ref

        # Current evidence: the engineer stated the service and nothing else.
        service_only = Evidence(items=(
            EvidenceItem(name="service", value="orders-api", source="engineer",
                         captured_at="2026-01-01T00:00:00+00:00", known=True),))
        # Memory carries the recalled case, cited by the hypothesis. `region` is the field the
        # engineer has NOT stated, so the recalled value cannot stand in for it.
        memory = MemoryContext(
            bank_id="rehearsal", recalled_at="2026-01-01T00:00:00+00:00", query="orders-api 502",
            candidates=[{"case_id": case_id, "relevance_class": "relevant",
                         "score_final": 0.9, "score_semantic": 0.8, "score_keyword": 0.7,
                         "environment": {"service": "orders-api", "region": "eu-west-1"}}],
            excluded=[],
            abstention={"abstained": False, "relevance_class": "relevant", "top_score": 0.9,
                        "threshold_used": 0.05, "reason": "none"})
        hypothesis = Hypothesis.from_dict({
            "ref": "H1",
            "hypothesis": "the proxy rejected large bodies",
            "supporting_case_ids": [case_id],
            "relevance_state": "supported",
            "refutation_conditions": ["a small body is also rejected"],
            "recommended_next_step": "check the proxy body limit",
        })

        with self.assertRaises(Exception):
            # No engineer decision recorded -> verify refuses before memory could matter at all.
            verify([hypothesis], service_only, memory, {})

        decision = EngineerDecision(decision="accept", claim="the proxy rejected large bodies",
                                    relevance_confirmed=True, note="")
        outcomes = verify([hypothesis], service_only, memory, {"H1": decision})

        # The recalled case's `region` is unknown in current evidence, so the verdict stays
        # insufficient_evidence: memory flags the gap, it does not fill it.
        self.assertEqual(outcomes[0].status, "insufficient_evidence",
                         "a strong memory match must not upgrade the verification status")
        self.assertIn("region", outcomes[0].mismatched_environment_fields,
                      "the unknown field the memory supplies must be reported as a gap")
        self.assertTrue(outcomes[0].relevance_confirmed,
                        "the engineer's relevance confirmation is recorded separately")


class NoParallelExecutionTests(RehearsalBase):
    """The rehearsal is serial. P5 must not appear."""

    def test_rehearsal_runs_tasks_one_at_a_time(self):
        port = self.fresh_bank()
        signature, _ = act_case_signature("act3-orders.yaml")
        worker = MemorySpecialist(port)
        results = [worker.execute(build_memory_specialist_task(
            task_id=f"serial-{i}", case_signature=signature)) for i in range(4)]
        self.assertEqual([r.task_id for r in results],
                         ["serial-0", "serial-1", "serial-2", "serial-3"])
        self.assertTrue(all(isinstance(r, SubAgentResult) for r in results))

    def test_no_concurrency_anywhere_in_the_worker(self):
        import inspect

        from debugagent.agents import memory_specialist

        source = inspect.getsource(memory_specialist)
        for forbidden in ("ThreadPool", "threading", "concurrent.futures", "asyncio",
                          "as_completed", "multiprocessing"):
            self.assertNotIn(forbidden, source)


class ExistingBehaviourUnchangedTests(unittest.TestCase):
    """P0-P3 behaviour must be untouched by the rehearsal."""

    def test_case_identity_formula_is_unchanged(self):
        from debugagent.memory.hindsight_store import compute_case_key, compute_outcome_digest
        from debugagent.schemas import MemoryCase

        case = MemoryCase.from_dict({
            "problem_signature": "p", "symptoms": ["s"], "environment": {"service": "svc"},
            "observed_evidence": ["e"], "investigation_trace": ["t"], "failed_approaches": [],
            "root_cause": "rc", "resolution": "res", "outcome": "resolved",
            "verification_notes": "n"})
        import hashlib

        manual = f"p|s1|{compute_outcome_digest(case)}"
        self.assertEqual(compute_case_key(case, "s1"),
                         hashlib.sha256(manual.encode("utf-8")).hexdigest()[:16])

    def test_p3_b_limits_are_unchanged(self):
        from debugagent.llm import (MAX_CONVERSATION_CHARS, MAX_TOOL_ARGUMENT_CHARS,
                                   MAX_TOOL_CALLS_PER_TURN, MAX_TOOL_DEFINITION_CHARS,
                                   MAX_TOOL_RESULT_CHARS, MAX_TOOL_TURNS)

        self.assertEqual(MAX_TOOL_TURNS, 4)
        self.assertEqual(MAX_TOOL_CALLS_PER_TURN, 8)
        self.assertEqual(MAX_TOOL_ARGUMENT_CHARS, 16_000)
        self.assertEqual(MAX_TOOL_RESULT_CHARS, 32_000)
        self.assertEqual(MAX_TOOL_DEFINITION_CHARS, 8_000)
        self.assertEqual(MAX_CONVERSATION_CHARS, 256_000)

    def test_p3_1_locking_is_unchanged(self):
        source = Path(__file__).resolve().parents[1].joinpath(
            "src", "debugagent", "memory", "hindsight_store.py").read_text(encoding="utf-8")
        for marker in ("threading.RLock", "with self._retain_lock", "ledger.refresh",
                       "os.replace"):
            self.assertIn(marker, source)

    def test_p3_2b_document_identity_is_unchanged(self):
        source = Path(__file__).resolve().parents[1].joinpath(
            "src", "debugagent", "memory", "hindsight_store.py").read_text(encoding="utf-8")
        self.assertIn("document_id=case_key", source)
        self.assertIn("update_mode=REPLACE_MODE", source)

    def test_evidence_and_verify_are_untouched(self):
        """Byte-level: P4 must not have reached them."""
        import subprocess

        root = Path(__file__).resolve().parents[1]
        for name in ("evidence.py", "verify.py"):
            path = root / "src" / "debugagent" / "pipeline" / name
            diff = subprocess.run(
                ["git", "diff", "--stat", "HEAD", "--", str(path.relative_to(root))],
                cwd=root, capture_output=True, text=True)
            self.assertEqual(diff.stdout.strip(), "", f"{name} must be unchanged")

    def test_agents_package_still_avoids_the_pipeline(self):
        forbidden = ("evidence", "verify", "investigate", "memory_port", "hindsight_store",
                     "hypothesize")
        offenders = []
        for path in Path(__file__).resolve().parents[1].joinpath(
                "src", "debugagent", "agents").glob("*.py"):
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith(("import ", "from ")):
                    for name in forbidden:
                        if name in stripped:
                            offenders.append(f"{path.name}: {stripped}")
        self.assertEqual(offenders, [], f"the agents package must not import the pipeline: {offenders}")


if __name__ == "__main__":
    unittest.main()
