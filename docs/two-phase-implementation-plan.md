# Two-Phase Implementation Plan

**Status:** Planning only — no application code written, no dependencies installed.
**Date:** 2026-09-27.
**Reference:** `docs/final-project-definition.md`, `docs/domain-neutral-system-design.md`,
`docs/architecture-review.md`, `docs/hindsight-capability-verification.md`,
`docs/implementation-plan.md` (original Phases 0–8, still valid as the detailed breakdown),
`docs/decision-log.md`.
**Architectural rules (binding):** Hindsight is the memory system, not the decision-maker;
memory never silently becomes evidence; the agent proposes; current evidence determines
verification status; the engineer decides. No unsupported Hindsight claims. No invented
benchmarks. EGER remains background inspiration only, not a dependency. No domain is
pre-selected; no VLSI-specific implementation before formal domain confirmation.

## 1. Delivery strategy

Two time-boxed deliveries against fixed dates. Phase 1 (due September 29 afternoon) ships one
complete vertical slice that a judge can follow end to end: the memory loop working live, with
the before/after difference visible on screen. Everything that does not strengthen that
demonstration moves to Phase 2 (due October 2, post-submission). If Phase 1 falls behind, scope
is cut from the demo surface inward — never from the memory loop outward: a working loop with a
plain CLI beats a polished UI over a broken loop.

## 2. Phase 1 objective

A complete, working, judgeable MVP: one engineering debugging workflow, one persona, one memory
loop, demonstrated live, proving the central hypothesis — persistent memory changes the
investigation behavior in a useful and observable way.

## 3. Phase 1 scope (must include)

- Hindsight integration (local or Cloud instance standing; thin `MemoryStore` layer).
- Memory retain (validated case write; explicit engineer-confirmed retention only).
- Memory recall (query + tags + types; scores surfaced for ordering).
- Relevant-memory handling (attach with original conditions; cite in proposal).
- Similar-but-not-identical handling (mismatches stated; conditional proposal).
- Irrelevant-memory handling (dropped from proposal, kept in trace log).
- Evidence/proposal separation (four layers rendered distinctly).
- Abstention / insufficient-evidence path (visible "no relevant memory" + generic path).
- One complete investigation workflow (intake → recall → compare → hypothesize → verify →
  propose → resolve → retain).
- Selective retention after resolution (verified outcomes only; failed approaches included).
- CLI or minimal usable interface (describe, recall view, verify prompt, resolve record).
- Representative seed cases (single domain taxonomy as config; 3–4 recurring patterns).
- Basic tests (Hindsight-layer smoke, golden-path loop, abstention).
- Demo-ready output (before/after narrative rehearsed; verification gate visible both ways).
- README setup instructions (install, configure, run demo).
- Architecture documentation (this plan plus existing docs; no new architecture needed).
- Demo scenario (Section 9).

## 4. Phase 1 non-goals

Full production UI; advanced analytics; knowledge pages; `reflect()`-driven reasoning;
multi-agent architecture; multiple domains; large-scale evaluation infrastructure;
EDA/log/ticket integrations; auto-fix execution; production hardening (auth, scale, monitoring,
backup); content deliverables beyond the demo video itself. Each exclusion is deliberate:
none of them makes the memory effect more visible.

## 5. Phase 1 architecture

Unchanged from `docs/domain-neutral-system-design.md` and `docs/architecture-review.md`:
Intake → Normalizer (deterministic-first) → `MemoryStore` (Hindsight-backed, verified calls
only) → Orchestrator (application ranking, hypothesis synthesis) → Verification gate
(deterministic env-diff + engineer confirmation) → Outcome capture → selective retention.
Interface contract (case JSON shape, layer function signatures, proposal/verification shapes)
is frozen jointly before parallel work begins.

## 6. End-to-end workflow

```
Issue described (+ environment identifiers)
  → normalized to case draft
  → recall (candidates + scores + provenance)
  → env comparison → match report (supporting / contradicting / irrelevant)
  → ranked hypotheses, each citing cases + refutation conditions
  → verification: preconditions checked against current evidence; engineer confirms
  → proposal rendered with citations
  → engineer decides, executes, reports result
  → outcome (incl. failures + verification notes) validated and retained
```

Abstention branch: weak/empty recall → visible abstention → generic checklist → resolve →
retain (this path must also be demoable).

## 7. Hindsight operations required (all verified)

`retain()` (case write); `recall()` with `tags`/`tags_match`, `types`, `max_tokens`, tuned
`min_scores`; PATCH edit/invalidate for corrections and staleness; bank isolation (one bank);
opt-in Memory Defense redaction. Nothing else. `reflect()`, mental models, webhooks, and the
LLM auto-retain wrapper are explicitly excluded from Phase 1.

## 8. Memory model

The 8 essential fields from the readiness review (signature, symptoms, environment, evidence
refs, trace, failed approaches, root cause + resolution, outcome, verification notes),
carried as content + flat metadata/tags/context convention with application-side validation.
Timestamps and IDs come from Hindsight natively.

## 9. Demo scenario

Reuses the documented hold-causes-setup-pessimism-style narrative shape with the chosen
domain's cases (no numbers claimed): Issue A on an empty bank → generic path → resolve →
case retained visibly. Issue B (similar, adjacent) → recall hit with original conditions →
failed-approach warning → env comparison on screen → cited proposal → engineer verifies →
resolve → retain. Side-by-side path contrast closes the demo. A third beat — weak-match
abstention — is rehearsed as backup if time permits.

## 10. Phase 1 acceptance criteria

- Phase 0 five items logged (language, hosting, key owner, split, pre-seed rule).
- Spike green: 5 retains, steerable recall, calibrated abstention, zero hallucinated cases, A–E probes logged.
- Full loop runs live without errors; every applied recalled fix traces to a logged check.
- Contradiction case surfaces both candidates; weak case abstains visibly.
- Bank inspection: complete cases with failed approaches; no unverified hypotheses stored.
- CLI walkthrough completable by the non-author; setup instructions verified from a clean checkout.
- Demo video recorded (2–5 min); live runnable rehearsed; no sensitive data visible.

## 11. Phase 1 testing strategy

Smoke tests (Hindsight layer: retain/recall/PATCH/invalidate round-trip); golden-path test
(seed bank → known issue → expected recall + proposal shape); abstention tests (empty bank,
weak-match bank → generic path, no invented cases); schema-validation tests (malformed case
rejected with explicit errors). Evaluation observations (precision, utility, avoided retries,
traceability) recorded raw during rehearsal — no thresholds asserted.

## 12. Phase 1 submission checklist

- [ ] MVP loop live and rehearsed
- [ ] Demo video recorded (2–5 min, before/after visible)
- [ ] GitHub repo runnable from README (install → configure → run)
- [ ] Technical article per member (from observed behavior only)
- [ ] Social post per member
- [ ] Submission form complete before the afternoon deadline
- [ ] No secrets, proprietary data, or invented metrics anywhere in repo/video/posts

## 13. Phase 2 objective

Post-submission hardening and enrichment (due October 2): make the proven loop more robust,
more instructive, and better presented — without changing the frozen architecture or demo story.

## 14. Phase 2 candidate features (uncommitted)

Better recall/ranking (learned weights from retained verification notes); more seed cases;
abstention recalibration on larger banks; additional domain adapter(s); improved CLI/UI;
memory curation workflows (edit/invalidate UX); broader evaluation (more paired runs, agreement
tracking); additional test coverage; basic observability (run logging, recall-trace export);
documentation improvements (diagrams, cookbook-style examples).

## 15. Phase 2 prioritization criteria

After Phase 1 ships, rank candidates by: (a) judge/reviewer feedback from submission;
(b) effort-to-visible-value ratio under the 3-day window; (c) risk of destabilizing the frozen
demo (anything touching the loop needs a re-rehearsal budget); (d) portfolio value per member.
Anything failing (c) is cut first. No Phase 2 item is pre-approved by this plan.

## 16. Risk management

| Risk | Phase | Mitigation |
|------|-------|------------|
| Phase 0 indecision burns Sept 27–28 | 1 | Language and LLM provider decisions are now confirmed; remaining defaults: Hindsight hosting = local Docker, key provisioning by the team, all via config |
| Pre-seed rule forbids seeded bank | 1 | Ask organizers first (Day-0 action); fallback: live-retain the first case during the demo itself |
| Similarity quality disappoints in spike | 1 | Tag-heavy recall fallback; narrow domain patterns further |
| Seed authorship overruns | 1 | Start immediately post-spike; minimum viable set is 6 cases over 2 patterns |
| Abstention misfires live | 1 | Conservative threshold + manual override command in CLI |
| Scope creep (UI, 2nd domain, analytics) | 1 | Joint decision-log entry required; default answer is no |
| Demo-day environment failure | 1 | Pre-recorded full run as backup; local + Cloud bank snapshots |
| Phase 2 destabilizes demo | 2 | Freeze demo branch at submission; Phase 2 work on separate branches only |

## 17. Dependency ordering

Phase 0 decisions → Hindsight standing → spike (steerability + abstention + A–E) → interface
contract sign-off → schema freeze → seed authoring (critical path, overlaps loop build) →
recall behavior → loop → CLI → tests/eval → rehearsal → video → submission. Phase 2 starts
only from the frozen submission state.

## 18. Team work split

Per `docs/team-task-split.md` (still proposed — confirmation is a Phase 0 item): Rama →
Hindsight layer + spike + schema/seeds; Mukul → agent loop + CLI; shared → tests, rehearsal,
video, hygiene. Joint checkpoints (no solo decisions): interface contract, spike results,
seed-pattern review, demo rehearsal sign-off. Neither stream may change shared interfaces
without the other's review.

## 19. Critical path

Organizer pre-seed answer → Phase 0 decisions → Hindsight standing → spike green →
schema freeze → seed set complete → loop end-to-end → CLI walkthrough → rehearsal →
video → submission. The longest pole is seed authoring + loop integration; protect it by
starting seeds the hour the spike passes and by refusing all non-demo work until rehearsal.

## 20. Definition of done

- **Phase 1 done:** all Section 10 criteria met; submission checklist complete; repo tagged at
  submission state; demo reproducible from README on a clean checkout.
- **Phase 2 done (Oct 2):** selected candidates from Section 14 implemented behind the frozen
  demo, tests green, docs updated, no regressions in the recorded demo path.
- **Project done:** portfolio-ready repo, articles/posts published, retrospective logged in
  decision log.
