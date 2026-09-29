"""P1: delegation seam — schemas, closed registry, tool authorization, anti-recursion.

Covers docs/adr-001-hub-spoke-coordinator.md P1: "TaskSpec / SubAgentResult schemas + tool registry;
`task` refused for non-coordinator roles". No worker is executed anywhere in this file, and none of
the Phase 1 pipeline is touched.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from debugagent.agents import (
    AGENT_DEFINITION_FIELDS,
    COORDINATOR_TOOLS,
    DELEGATOR,
    MAX_SPAWN_DEPTH,
    TASK_TOOL,
    WORKER_IDS,
    WORKER_ROSTER,
    AgentDefinition,
    Artifact,
    AuthorizationError,
    SubAgentResult,
    TaskSpec,
    TaskSpecError,
    WorkerContext,
    agent_names,
    authorize,
    authorize_tool,
    build_task_spec,
    get_agent,
)

ADR = Path(__file__).resolve().parents[1] / "docs" / "adr-001-hub-spoke-coordinator.md"

# The tool sets fixed by the ADR. Written out literally here so a change to the registry that
# silently widens or narrows a permission fails this test.
ADR_TOOL_SETS = {
    "memory_specialist": ("hindsight_recall", "hindsight_get_facts"),
    "code_log_verifier": ("read", "grep", "glob"),
    "patch_generator": ("read", "diff"),
    "coordinator": ("task", "read", "grep", "glob"),
}


def context_dict(**overrides) -> dict:
    base = {
        "case_signature": "media-uploader resets connections on uploads over 2 MB behind nginx",
        "symptoms": ["20/20 uploads of 2.5MB fail with ECONNRESET"],
        "environment": {"service": "media-uploader", "runtime": "node20", "proxy": ""},
        "artifacts": [{"kind": "log", "ref": "nginx-error", "content": "413 request entity too large"}],
    }
    base.update(overrides)
    return base


def spec_dict(**overrides) -> dict:
    base = {
        "task_id": "task-1",
        "agent": "code_log_verifier",
        "delegated_by": DELEGATOR,
        "objective": "Confirm whether nginx is rejecting the request body",
        "context": context_dict(),
        "allowed_tools": ["read", "grep"],
        "model": "primary",
        "depth": 1,
    }
    base.update(overrides)
    return base


def result_dict(**overrides) -> dict:
    base = {
        "task_id": "task-1",
        "agent": "code_log_verifier",
        "status": "success",
        "observations": [{"kind": "log", "ref": "nginx-error", "content": "413 from nginx"}],
    }
    base.update(overrides)
    return base


class TaskSpecTests(unittest.TestCase):
    def test_valid_taskspec_is_accepted(self):
        spec = TaskSpec.from_dict(spec_dict())
        self.assertEqual(spec.task_id, "task-1")
        self.assertEqual(spec.agent, "code_log_verifier")
        self.assertEqual(spec.delegated_by, DELEGATOR)
        self.assertEqual(spec.depth, 1)
        self.assertEqual(spec.allowed_tools, ("read", "grep"))
        self.assertIsInstance(spec.context, WorkerContext)
        self.assertEqual(spec.context.env_dict()["service"], "media-uploader")

    def test_context_drops_unstated_values_rather_than_guessing(self):
        spec = TaskSpec.from_dict(spec_dict())
        # proxy="" was not stated, so it must not appear as a fact.
        self.assertNotIn("proxy", spec.context.env_dict())
        self.assertEqual(spec.context.env_dict(), {"runtime": "node20", "service": "media-uploader"})

    def test_context_is_immutable_and_hashable(self):
        context = TaskSpec.from_dict(spec_dict()).context
        self.assertIsInstance(context.symptoms, tuple)
        self.assertIsInstance(context.artifacts, tuple)
        self.assertIsInstance(context.environment, tuple)
        with self.assertRaises(AttributeError):
            context.case_signature = "mutated"  # type: ignore[misc]

    def test_malformed_taskspec_is_rejected(self):
        for label, payload in [
            ("not an object", "nope"),
            ("missing task_id", {k: v for k, v in spec_dict().items() if k != "task_id"}),
            ("blank objective", spec_dict(objective="   ")),
            ("empty allowed_tools", spec_dict(allowed_tools=[])),
            ("non-string tool", spec_dict(allowed_tools=["read", 7])),
            ("missing context", {k: v for k, v in spec_dict().items() if k != "context"}),
            ("context wrong type", spec_dict(context="some text")),
            ("non-integer depth", spec_dict(depth="1")),
            ("unknown field", spec_dict(escalate=True)),
            ("bad artifact kind", spec_dict(context=context_dict(
                artifacts=[{"kind": "gossip", "ref": "x", "content": "y"}]))),
            ("artifact missing content", spec_dict(context=context_dict(
                artifacts=[{"kind": "log", "ref": "x"}]))),
        ]:
            with self.subTest(label):
                with self.assertRaises(TaskSpecError):
                    TaskSpec.from_dict(payload)

    def test_errors_name_the_offending_field(self):
        with self.assertRaises(TaskSpecError) as caught:
            TaskSpec.from_dict(spec_dict(objective=""))
        self.assertIn("objective", str(caught.exception))


class AntiRecursionTests(unittest.TestCase):
    def test_coordinator_may_request_task(self):
        self.assertIn(TASK_TOOL, COORDINATOR_TOOLS)
        self.assertTrue(get_agent(DELEGATOR).permits(TASK_TOOL))
        authorize_tool(DELEGATOR, "read")  # does not raise

    def test_taskspec_can_never_carry_the_task_tool(self):
        # Structural: refused by the schema, so it holds even if the registry is bypassed.
        with self.assertRaises(TaskSpecError) as caught:
            TaskSpec.from_dict(spec_dict(allowed_tools=["read", TASK_TOOL]))
        self.assertIn(TASK_TOOL, str(caught.exception))

    def test_worker_cannot_request_task(self):
        for worker in WORKER_IDS:
            with self.subTest(worker):
                self.assertNotIn(TASK_TOOL, get_agent(worker).allowed_tools)
                with self.assertRaises(AuthorizationError):
                    authorize_tool(worker, TASK_TOOL)
                with self.assertRaises(TaskSpecError):
                    build_task_spec(spec_dict(agent=worker, allowed_tools=[TASK_TOOL]))

    def test_a_worker_may_not_delegate(self):
        for worker in WORKER_IDS:
            with self.subTest(worker):
                with self.assertRaises(TaskSpecError) as caught:
                    TaskSpec.from_dict(spec_dict(delegated_by=worker))
                self.assertIn("may not delegate", str(caught.exception))

    def test_depth_beyond_one_is_rejected(self):
        self.assertEqual(MAX_SPAWN_DEPTH, 1)
        with self.assertRaises(TaskSpecError) as caught:
            TaskSpec.from_dict(spec_dict(depth=2))
        self.assertIn("max_spawn_depth", str(caught.exception))
        with self.assertRaises(AuthorizationError):
            authorize(TaskSpec(task_id="t", agent="code_log_verifier", delegated_by=DELEGATOR,
                               objective="o", context=WorkerContext(case_signature="s"),
                               allowed_tools=("read",), model="primary", depth=2))

    def test_depth_zero_is_rejected(self):
        with self.assertRaises(TaskSpecError):
            TaskSpec.from_dict(spec_dict(depth=0))


class RegistryTests(unittest.TestCase):
    def test_registry_contains_exactly_the_three_approved_workers(self):
        self.assertEqual(set(WORKER_ROSTER), {"memory_specialist", "code_log_verifier", "patch_generator"})
        self.assertEqual(len(agent_names()), 3)
        self.assertEqual(agent_names(), WORKER_IDS)

    def test_registry_is_closed(self):
        for unknown in ["worker_4", "Coordinator", "", "memory specialist", "root", "shell"]:
            with self.subTest(unknown):
                with self.assertRaises(AuthorizationError) as caught:
                    get_agent(unknown)
                self.assertIn("closed", str(caught.exception))

    def test_every_worker_has_exactly_the_four_payload_fields(self):
        for worker, definition in WORKER_ROSTER.items():
            with self.subTest(worker):
                self.assertEqual(set(definition.to_dict()), set(AGENT_DEFINITION_FIELDS))
                self.assertEqual(len(definition.to_dict()), 4)
                for field in ("description", "prompt", "model"):
                    self.assertTrue(getattr(definition, field).strip(), f"{worker}.{field} is empty")
                self.assertTrue(definition.allowed_tools, f"{worker}.allowed_tools is empty")
                for tool in definition.allowed_tools:
                    self.assertIsInstance(tool, str)
                    self.assertTrue(tool.strip(), f"{worker}.allowed_tools has a blank tool")

    def test_no_worker_has_task(self):
        for worker, definition in WORKER_ROSTER.items():
            self.assertNotIn(TASK_TOOL, definition.allowed_tools, worker)

    def test_worker_tool_permissions_match_the_adr(self):
        for worker, expected in ADR_TOOL_SETS.items():
            if worker == "coordinator":
                continue
            with self.subTest(worker):
                self.assertEqual(tuple(get_agent(worker).allowed_tools), expected)
        self.assertEqual(tuple(get_agent(DELEGATOR).allowed_tools), ADR_TOOL_SETS["coordinator"])

    def test_agent_definition_rejects_extra_and_missing_fields(self):
        payload = get_agent("patch_generator").to_dict()
        with self.assertRaises(AuthorizationError):
            AgentDefinition.from_dict({**payload, "temperature": 0.7}, "extra")
        incomplete = {k: v for k, v in payload.items() if k != "model"}
        with self.assertRaises(AuthorizationError):
            AgentDefinition.from_dict(incomplete, "incomplete")

    def test_agent_definition_refuses_task_in_allowed_tools(self):
        payload = get_agent("patch_generator").to_dict()
        payload["allowed_tools"] = ["read", TASK_TOOL]
        with self.assertRaises(AuthorizationError):
            AgentDefinition.from_dict(payload, "worker-with-task")

    def test_unauthorized_tool_is_rejected(self):
        # read-only tools on a memory-only worker, and hindsight tools on a code worker.
        with self.assertRaises(AuthorizationError):
            authorize_tool("memory_specialist", "read")
        with self.assertRaises(AuthorizationError):
            authorize_tool("code_log_verifier", "hindsight_recall")
        with self.assertRaises(AuthorizationError):
            authorize_tool("patch_generator", "write")

    def test_unknown_agent_in_a_taskspec_is_rejected(self):
        with self.assertRaises(AuthorizationError):
            build_task_spec(spec_dict(agent="patch_genrator"))
        with self.assertRaises(AuthorizationError):
            build_task_spec(spec_dict(agent=DELEGATOR))

    def test_task_cannot_target_the_coordinator(self):
        with self.assertRaises(AuthorizationError) as caught:
            build_task_spec(spec_dict(agent=DELEGATOR, allowed_tools=["read"]))
        self.assertIn("delegates", str(caught.exception))

    def test_model_must_match_the_registered_definition(self):
        with self.assertRaises(AuthorizationError) as caught:
            build_task_spec(spec_dict(model="some-other-model"))
        self.assertIn("registered with model", str(caught.exception))

    def test_authorized_spec_round_trips(self):
        spec = build_task_spec(spec_dict())
        self.assertEqual(spec.agent, "code_log_verifier")
        self.assertEqual(TaskSpec.from_dict(spec.to_dict()), spec)


class SubAgentResultTests(unittest.TestCase):
    def test_valid_result_is_accepted(self):
        result = SubAgentResult.from_dict(result_dict())
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "success")
        self.assertIsNone(result.failure_kind)
        self.assertEqual(len(result.observations), 1)

    def test_failed_result_is_distinguishable_from_success(self):
        failed = SubAgentResult.from_dict(result_dict(status="failed", observations=[],
                                                      failure_kind="unavailable", failure_detail="timed out"))
        self.assertFalse(failed.ok)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.failure_kind, "unavailable")
        self.assertEqual(failed.observations, ())

    def test_partial_result_carries_both_findings_and_a_failure(self):
        partial = SubAgentResult.from_dict(result_dict(status="partial", failure_kind="timeout"))
        self.assertFalse(partial.ok)
        self.assertEqual(partial.status, "partial")
        self.assertEqual(partial.failure_kind, "timeout")
        self.assertEqual(len(partial.observations), 1)

    def test_malformed_result_is_rejected(self):
        for label, payload in [
            ("not an object", 5),
            ("unknown status", result_dict(status="mostly-ok")),
            ("success with a failure_kind", result_dict(failure_kind="timeout")),
            ("success with no observations", result_dict(observations=[])),
            ("failed with no failure_kind", result_dict(status="failed", observations=[])),
            ("failed with observations", result_dict(status="failed", failure_kind="auth")),
            ("partial with no failure_kind", result_dict(status="partial")),
            ("unknown failure kind", result_dict(failure_kind="kaboom")),
            ("missing task_id", {k: v for k, v in result_dict().items() if k != "task_id"}),
            ("unknown field", result_dict(root_cause="the proxy")),
            ("bad observation", result_dict(observations=[{"kind": "vibes"}])),
        ]:
            with self.subTest(label):
                with self.assertRaises(TaskSpecError):
                    SubAgentResult.from_dict(payload)

    def test_failure_is_never_reported_as_nothing_found(self):
        # A failure must always name a kind, so it can never be read as an empty successful recall.
        with self.assertRaises(TaskSpecError):
            SubAgentResult.from_dict(result_dict(status="failed", observations=[]))

    def test_observations_keep_non_engineer_provenance(self):
        # ADR refinement: tool-derived findings keep a non-`engineer` source label.
        result = SubAgentResult.from_dict(result_dict())
        self.assertEqual(result.observations[0].source, "log:nginx-error")
        self.assertNotEqual(result.observations[0].source, "engineer")

    def test_result_cannot_smuggle_an_evidence_or_decision_field(self):
        for field in ("evidence", "engineer_decision", "retained", "hypothesis", "root_cause_confirmed"):
            with self.subTest(field):
                with self.assertRaises(TaskSpecError) as caught:
                    SubAgentResult.from_dict(result_dict(**{field: "x"}))
                self.assertIn(field, str(caught.exception))


class ArtifactTests(unittest.TestCase):
    def test_artifact_source_prefixes(self):
        for kind, expected in [("log", "log:x"), ("code", "code:x"), ("memory", "memory:x")]:
            self.assertEqual(Artifact(kind=kind, ref="x", content="c").source, expected)

    def test_artifact_ref_must_not_repeat_its_prefix(self):
        errors: list[str] = []
        Artifact.from_dict({"kind": "log", "ref": "log:nginx", "content": "c"}, "a", errors)
        self.assertTrue(any("prefix" in e for e in errors))

    def test_artifact_rejects_unknown_kind(self):
        errors: list[str] = []
        Artifact.from_dict({"kind": "shell", "ref": "x", "content": "c"}, "a", errors)
        self.assertTrue(any("kind" in e for e in errors))


class TrustBoundaryTests(unittest.TestCase):
    """P1 must offer no path from a worker result to EVIDENCE, decisions, retention or patches."""

    FORBIDDEN = ("evidence", "verify", "investigate", "memory_port", "hindsight_store", "hypothesize")

    def test_agents_package_does_not_import_the_phase1_pipeline(self):
        offenders = []
        for path in Path(__file__).resolve().parents[1].joinpath("src", "debugagent", "agents").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith(("import ", "from ")):
                    for name in self.FORBIDDEN:
                        if name in stripped:
                            offenders.append(f"{path.name}: {stripped}")
        self.assertEqual(offenders, [], f"the P1 seam must not reach the Phase 1 pipeline: {offenders}")

    def test_subagent_result_exposes_no_write_or_decision_method(self):
        for method in ("apply", "write_evidence", "decide", "retain", "commit", "approve", "execute", "run"):
            self.assertFalse(hasattr(SubAgentResult, method), method)
            self.assertFalse(hasattr(TaskSpec, method), method)

    def test_result_is_data_only(self):
        result = SubAgentResult.from_dict(result_dict())
        with self.assertRaises(AttributeError):
            result.status = "failed"  # type: ignore[misc]
        self.assertEqual(result.to_dict()["status"], "success")

    def test_adr_exists_and_p1_is_still_scoped_to_the_seam(self):
        self.assertTrue(ADR.is_file(), "the ADR is the authority for this layer")
        text = ADR.read_text(encoding="utf-8")
        self.assertIn("max_spawn_depth = 1", text)
        self.assertIn("TaskSpec", text)
        self.assertIn("SubAgentResult", text)


if __name__ == "__main__":
    unittest.main()
