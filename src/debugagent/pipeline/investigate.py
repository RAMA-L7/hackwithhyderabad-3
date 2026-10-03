"""MK7: one investigation, end to end. Owns the seam calls and the retention call site.

normalize -> recall -> EVIDENCE -> hypothesize -> engineer verifies -> engineer resolves -> retain.
retain() is called only after every hypothesis has an engineer decision AND the engineer reported a
resolution (T5). The Engineer is injected, so the CLI and the tests drive the same code.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol

from debugagent.pipeline.evidence import add_facts, build_evidence
from debugagent.pipeline.hypothesize import Proposal, generate_hypotheses
from debugagent.pipeline.memory_port import MemoryPort, check_retention
from debugagent.pipeline.normalize import normalize
from debugagent.pipeline.recall_match import MemoryContext, recall
from debugagent.pipeline.render import (render_decision, render_evidence, render_memory,
                                        render_memory_delegation, render_patch_proposals,
                                        render_proposal, render_retention,
                                        render_worker_verifier)
from debugagent.pipeline.types import DebugInput, Evidence, Hypothesis, NormalizedDebugCase, Resolution, VerificationResult
from debugagent.pipeline.verify import EngineerDecision, build_resolution, evidence_gaps, verify
from debugagent.pipeline.worker_stage import run_worker_stage


class Engineer(Protocol):
    def show(self, text: str) -> None: ...
    def current_facts(self, evidence: Evidence) -> dict[str, str | None]: ...
    def decide(self, hypothesis: Hypothesis, mismatched: list[str], missing: list[str]) -> EngineerDecision: ...
    def resolve(self, session: "Session") -> dict | None:
        """Keyword answers for build_resolution, or None when the issue was not resolved."""


@dataclass
class Session:
    session_id: str
    raw: DebugInput
    case: NormalizedDebugCase | None = None
    memory: MemoryContext | None = None
    evidence: Evidence | None = None
    proposal: Proposal | None = None
    verifications: list[VerificationResult] = field(default_factory=list)
    resolution: Resolution | None = None
    retention: dict | None = None
    # P4: the Memory Specialist's own result for this session, when one was delegated. Kept
    # SEPARATE from `memory` on purpose: `memory` is the ranked MemoryContext the proposal is
    # built from, while this is the worker's report. It is a proposal input and never evidence.
    memory_delegation: dict | None = None
    # P6: what the Code/Log Verifier and the Patch Generator contributed, as ONE structured record.
    # Held separately from `evidence` and from `proposal` on purpose:
    #   - `verifier` holds observations of the CURRENT system, which belong on the evidence SIDE of
    #     the trust boundary but must never become an `Evidence` item or a verification status;
    #   - `patches` holds PROPOSALS, which are never applied and never verified.
    # Both are None unless a repository was supplied, so a Phase 1 session serialises exactly the keys
    # it always did.
    workers: dict | None = None
    trace: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        memory = self.memory
        return {
            "session_id": self.session_id,
            "input": self.raw.to_dict(),
            "case": self.case.to_dict() if self.case else None,
            "memory": None if memory is None else {
                "bank_id": memory.bank_id, "recalled_at": memory.recalled_at, "query": memory.query,
                "abstention": memory.abstention,
                "candidates": [{k: c[k] for k in ("case_id", "relevance_class", "environment", "reason")} for c in memory.candidates],
                "excluded": [{k: c[k] for k in ("case_id", "relevance_class", "reason")} for c in memory.excluded]},
            "evidence": self.evidence.to_dict() if self.evidence else None,
            "proposal": None if self.proposal is None else {
                "provider": self.proposal.provider, "model": self.proposal.model,
                "fallback_used": self.proposal.fallback_used, "attempts": self.proposal.attempts,
                "hypotheses": [h.to_dict() for h in self.proposal.hypotheses]},
            "verifications": [v.to_dict() for v in self.verifications],
              "resolution": self.resolution.to_dict() if self.resolution else None,
              "retention": self.retention,
              # Absent unless a Memory Specialist was delegated, so a Phase 1 session without one
              # serialises exactly the keys it always did.
              **({"memory_delegation": self.memory_delegation} if self.memory_delegation is not None
                 else {}),
              # Absent unless worker stages ran, so a Phase 1 session's key set is unchanged.
              **({"workers": self.workers} if self.workers is not None else {}),
              "trace": self.trace,
          }


def _delegate_memory(coordinator, session: Session) -> dict:
    """Run ONE memory task for this session through the Coordinator.

    The Coordinator builds the spec through the P1 seam (`build_task_spec` -> `authorize`) and
    authorises again immediately before execution, so this function only supplies the session's
    identity. That keeps the authorisation path identical to the one every other delegation uses,
    rather than introducing a second way in.

    The returned dict is the worker's `SubAgentResult.to_dict()`. A worker that *raises* never
    reaches this point: the Coordinator has already turned that into a failed result, because the
    engineer's investigation must still be able to proceed with whatever memory the direct `recall()`
    finds, exactly as it does today with no worker at all. An *authorisation* failure does reach here,
    as an exception, because a refused delegation must not be presented to the engineer as an outage.
    """
    case_signature = session.case.problem_signature if session.case else session.raw.description
    result = coordinator.delegate_memory(
        task_id=f"{session.session_id}-memory",
        case_signature=case_signature,
        symptoms=tuple(session.case.symptoms) if session.case else (),
    )
    return result.to_dict()


def assemble_memory_case(session: Session) -> dict:
    """plan section 4. Unknown environment values are dropped: a stored case never carries a guess."""
    case, resolution, evidence = session.case, session.resolution, session.evidence
    environment = {item.name: item.value for item in evidence.items if item.known and item.name in case.environment}
    notes = "; ".join(f"{v.hypothesis_ref} {v.engineer_decision}, evidence {v.status}"
                      + (f": {v.engineer_note}" if v.engineer_note else "") for v in session.verifications)
    return {
        "problem_signature": case.problem_signature,
        "symptoms": list(case.symptoms),
        "environment": environment,
        "observed_evidence": list(resolution.evidence_refs),
        "investigation_trace": list(session.trace),
        "failed_approaches": [f.to_dict() for f in resolution.failed_approaches_this_session],
        "root_cause": resolution.root_cause_confirmed,
        "resolution": f"{resolution.action_taken} (observed: {resolution.observed_result})",
        "outcome": resolution.outcome,
        "verification_notes": notes or "engineer recorded no notes",
        "session_id": session.session_id,
    }


def investigate(raw: DebugInput, port: MemoryPort, llm, engineer: Engineer, *, session_id: str | None = None,
                on_step=None, memory_specialist=None, coordinator=None, repository=None,
                verifier_targets=(), sdc_targets=(), patch_requests=(), worker_timeout=None) -> Session:
    """Run one session and return it. `on_step(session)` is called after each stage so a caller can persist progress.

    P4: `memory_specialist` is optional. When supplied, one memory task is delegated to it through
    the P1 authorisation seam and the result is recorded on `session.memory_delegation`. When it is
    `None` - the Phase 1 default - nothing about the flow changes.

    P5: `coordinator` is the same delegation expressed by its owner. Both are accepted, and both end
    up on the one `Coordinator`, so there is a single dispatch path rather than a coordinator one and
    a bare-worker one. Passing both is an error rather than a silent preference for one.

    P5 concurrency: when a Coordinator is supplied, every Hindsight access this function makes - the
    delegation, its own direct `recall()`, and the retention write - runs inside that Coordinator's
    `MemoryLane`, so the client is only ever touched by one thread at a time. This is required because
    `hindsight_client` caches a single loop-bound aiohttp session and is not thread-safe, for reads as
    well as writes.

    P6: `repository` is a `RepositoryRuntime` - the real `FileSourcePort` / `RepoPatchSourcePort` adapters
    over a real directory, with the verifier and patch generator registered on the session's own
    Coordinator. With it, `verifier_targets` and `patch_requests` ask for the workers to contribute, and
    the result lands on `session.workers` as ONE structured record. Without it - the Phase 1 default -
    no worker stage runs and the session serialises exactly the keys it always did.

    What each worker's result is allowed to become, and what the flow does with it:

      - the Memory Specialist's result stays in `session.memory_delegation`: knowledge of past cases,
        shown as MEMORY, never added to `evidence`;
      - the Code/Log Verifier's findings land in `session.workers["verifier"]`, RANKED deterministically.
        They are observations of the CURRENT system - the evidence SIDE of the trust boundary - and are
        deliberately NOT `Evidence` items: `build_evidence` and `verify` are untouched, an unconfirmed
        observation stays an observation, and the engineer confirms it as they confirm any observation;
      - the Patch Generator's output lands in `session.workers["patches"]` as PROPOSALS. Nothing is
        applied, written, committed or run, no proposal is applied automatically, and no proposal is ever
        verified. `patch_requests` must supply both a target and a proposed body, because a worker picking
        its own target would be a worker deciding what to change.
      - `sdc_targets` names constraint files for the SDC Analyzer. They are dispatched in the SAME fan-out
        as `verifier_targets` and land in the SAME `session.workers["verifier"]` section, because both are
        observations about the CURRENT system at the same authority, confirmed by the engineer the same
        way. There is deliberately no `workers["sdc"]`: a channel per domain would turn the generic worker
        layer into a catalogue of domains, and rendering one would need a per-domain branch in the view
        model - at which point the UI would know what an SDC finding is, which is the one thing it must
        not know.

    The stage runs after the evidence set is built and before hypotheses are generated. That ordering is
    for the ENGINEER: by the time they are asked for a decision, the verifier's observations and any
    proposal have already been shown to them, so the decision is taken with them in view. It is
    deliberately NOT a way of informing the model - `build_prompt` receives only the case and the memory
    context, so worker output never enters the proposal prompt. Putting it there would move worker
    observations into the proposing step, which is a trust-boundary change deserving its own decision
    rather than one arriving through plumbing.

    The authority boundaries above stay exactly where they were.

    The usage rule that follows: pass the SAME Coordinator to every call that shares a client. A
    Coordinator per call means a lane per call, and the boundary would be per-session while the hazard
    is per-client. With no Coordinator - the Phase 1 default - there is no lane and this function is
    exactly what it was.

    The worker is a SIDE CHANNEL, not a replacement. The ranked `session.memory` the proposal is built
    from is still produced by the existing `recall()`; the worker's observations are reported
    separately and never enter `build_evidence`. Delegating memory does not move the trust boundary:
    the worker retrieves knowledge, and the engineer still decides.
    """
    if coordinator is not None and memory_specialist is not None:
        raise ValueError(
            "pass `coordinator` or `memory_specialist`, not both: they are two ways to supply the "
            "same single memory worker, and guessing which was meant would hide a wiring mistake")

    hub = coordinator
    if hub is None and memory_specialist is not None:
        from debugagent.agents.coordinator import Coordinator

        hub = Coordinator.with_memory_specialist(memory_specialist)

    if hub is not None:
        # One client -> one Coordinator -> one MemoryLane. Binding here rather than only in the
        # composition root means the invariant holds for library callers too, who never touch
        # `composition.py`. Re-binding the SAME Coordinator is free and is the normal multi-session
        # case; a second, different Coordinator over one client is refused, because it would hand the
        # same non-thread-safe client a second lane and serialise nothing.
        from debugagent.composition import bind

        bind(port, hub)

    s = Session(session_id or uuid.uuid4().hex[:12], raw)

    def step(note: str) -> None:
        s.trace.append(note)
        if on_step:
            on_step(s)

    s.case = normalize(raw)
    step(f"normalized: {s.case.problem_signature}")

    # P4/P5: delegate MEMORY retrieval to the Memory Specialist first, when a hub was supplied. The
    # worker's report is recorded and shown, but the ranked MemoryContext below is still built by
    # the existing `recall()`, so the proposal path and the trust boundary are untouched.
    if hub is not None:
        s.memory_delegation = _delegate_memory(hub, s)
        report = s.memory_delegation
        engineer.show(render_memory_delegation(report))
        step(f"memory specialist {report['status']}: "
             f"{len(report.get('observations') or [])} observation(s)"
             + (f", {report['failure_kind']}" if report.get("failure_kind") else ""))

    # P5: the direct recall is a Hindsight access like any other, so it takes the same lane the
    # Coordinator used for delegation. Without this the boundary would only cover dispatched work and
    # the client would still be reachable from two threads at once. With no Coordinator - the Phase 1
    # default - there is no lane and this call is exactly what it always was.
    s.memory = hub.lane.call(recall, port, s.case) if hub is not None else recall(port, s.case)
    engineer.show(render_memory(s.memory, s.case.environment))
    step(f"recalled {len(s.memory.candidates)} candidate(s), {len(s.memory.excluded)} excluded; "
         f"abstained={s.memory.abstained} ({s.memory.abstention['relevance_class']})")

    s.evidence = build_evidence(s.case)
    facts = engineer.current_facts(s.evidence)
    if facts:
        s.evidence = add_facts(s.evidence, facts)
    engineer.show(render_evidence(s.evidence))
    step(f"evidence: {sum(i.known for i in s.evidence.items)} known item(s), unknown: {s.evidence.unknown_fields or 'none'}")

    # P6: the Code/Log Verifier and the Patch Generator, through the SAME Coordinator. Placed after the
    # evidence set is built and before hypotheses are generated, so that what the engineer is SHOWN reaches
    # them before they are asked to decide. The ordering serves the engineer's decision, NOT the model's
    # prompt: `build_prompt` takes only the case and the memory context, so no worker output reaches it.
    # Neither section is added to `s.evidence` and neither changes a verification status: findings are
    # observations, patches are proposals, and the engineer still confirms and decides.
    if repository is not None and (verifier_targets or sdc_targets or patch_requests):
        # One Coordinator per client. If the repository runtime carries a different Coordinator than the
        # memory side, the two would each hold their own lane and the same client could be reached from
        # two threads at once - the exact P5 hazard, reintroduced by composition. Refused loudly rather
        # than quietly running on two coordinators.
        if hub is None or repository.coordinator is not hub:
            raise ValueError(
                "repository runtime must share the session Coordinator "
                "(build it with coordinator=runtime.coordinator); two Coordinators means two MemoryLanes")
        stage = run_worker_stage(repository, s.case, verifier_targets=verifier_targets,
                                 sdc_targets=sdc_targets,
                                 patch_requests=patch_requests, timeout=worker_timeout)
        s.workers = stage.to_dict()
        if stage.verifier is not None:
            engineer.show(render_worker_verifier(stage.verifier))
            step(f"verifier: {len(stage.verifier['findings'])} finding(s) of "
                 f"{stage.verifier['findings_total']}, "
                 f"{len(stage.verifier['refusals'])} refusal(s) - observations, not evidence")
        if stage.patches is not None:
            rendered = render_patch_proposals(stage.patches)
            if rendered:
                engineer.show(rendered)
            step(f"patch generator: {len(stage.patches['proposals'])} proposal(s), "
                 f"{len(stage.patches['refusals'])} refusal(s) - proposals, nothing applied")

    s.proposal = generate_hypotheses(s.case, s.memory, llm)
    engineer.show(render_proposal(s.proposal))
    step(f"proposed {len(s.proposal.hypotheses)} hypothesis(es) via {s.proposal.provider} "
         f"(fallback_used={s.proposal.fallback_used}): "
         + ", ".join(f"{h.ref} {h.relevance_state} cites {h.supporting_case_ids or '[]'}" for h in s.proposal.hypotheses))

    decisions = {}
    for h in s.proposal.hypotheses:
        mismatched, missing = evidence_gaps(h, s.evidence, s.memory)
        decisions[h.ref] = engineer.decide(h, mismatched, missing)
    s.verifications = verify(s.proposal.hypotheses, s.evidence, s.memory, decisions)
    engineer.show(render_decision(s.proposal.hypotheses, s.verifications))
    step("decisions: " + ", ".join(f"{v.hypothesis_ref} {v.engineer_decision}/{v.status}" for v in s.verifications))

    answers = engineer.resolve(s)
    if answers is None:
        engineer.show(render_retention(None, "the engineer did not report a resolution"))
        step("not resolved; nothing retained")
        return s
    s.resolution = build_resolution(s.proposal.hypotheses, s.verifications, **answers)
    step(f"resolved: {s.resolution.outcome}; root cause {'confirmed' if s.resolution.root_cause_confirmed else 'unconfirmed'}; "
         f"{len(s.resolution.failed_approaches_this_session)} failed approach(es)")

    # The retention write touches the client too, so it shares the lane. Retention still happens only
    # because the ENGINEER resolved the case - taking a lock grants no authority.
    retained = hub.lane.call(port.retain, assemble_memory_case(s)) if hub is not None \
        else port.retain(assemble_memory_case(s))
    s.retention = check_retention(retained)
    engineer.show(render_retention(s.retention))
    step(f"retention: retained={s.retention['retained']} id={s.retention.get('memory_case_id')}")
    return s
