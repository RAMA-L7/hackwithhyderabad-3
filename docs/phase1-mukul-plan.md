# Phase 1 — Mukul-Side Milestones

**Status:** MK0–MK8 done; MK9 (joint merge) remains. §5 answers still to be confirmed with Rama; the code uses the proposals.
**Date:** 2026-09-28 · **Deadline:** MVP demo-ready 29 Sept afternoon.
**Branch:** `mukul-loop`, cut from `main` @ `1800c62`. **Independent of `rama-m0`:** nothing on
this branch imports, copies, or edits Rama's code. The two sides meet only through the memory
port pinned in §2, and are joined in one integration step (MK9) after both are done.
**Reference (on `rama-m0` @ `7254fc3`, read with `git show rama-m0:<path>`):**
`docs/m1-contract.md`, `docs/handoff-mukul-memory.md`. §2 below pins the parts this branch needs.

```
MEMORY informs · EVIDENCE verifies · AGENT proposes · ENGINEER decides
```

## 0. Agreed split (from Rama, 2026-09-28)

| Side | Owns |
|---|---|
| Rama | MemoryStore / Hindsight, memory schema, seeds + loading, recall + matching, abstention, M0/M1 infra |
| Mukul | investigation loop, normalization, hypothesis generation, evidence/proposal formatting, verify → resolve, CLI |
| Shared | final interface contract, integration, tests, demo rehearsal, submission/video |

```
Mukul  Input → Normalize → investigate()
                              ↓
Rama              MemoryPort.recall_and_classify(query)
                              ↓
Mukul              Evidence + MEMORY render
                              ↓
Mukul          Hypothesize (1 LLM call) → Verify → Resolve
                              ↓
Rama              MemoryPort.retain(case)   (called only after engineer_decision)
```

## 1. Branch independence rules

**Mukul-owned paths (only these are created on `mukul-loop`):**

```
src/debugagent/cli.py, app.py, logging_setup.py
src/debugagent/{controllers,services,domain,ports,views,adapters}/
tests/loop_support.py, tests/test_loop_*.py, tests/live_flows.py
demo/offline-memory.json  (pre-MK9 stand-in memory for rehearsal)
mk0/probe.py, mk0/results/
docs/phase1-mukul-plan.md, docs/phase1-mukul-m0-plan.md
```

**Code structure (layered, 2026-09-28).** Dependencies point inwards only; `tests/test_loop_architecture.py`
fails the build if a layer imports what it may not.

| Layer | Holds | May import |
|---|---|---|
| `domain/` | `models.py` (contract types), `investigation.py` (MemoryContext, Proposal, EngineerDecision, Session), `errors.py` (one hierarchy) | nothing |
| `ports/` | `memory_port.py` (+ boundary checks), `llm_port.py`, `engineer_port.py` | domain |
| `services/` | normalization, recall, evidence, hypothesis, verification, retention, investigation | domain, ports, logging |
| `adapters/` | `llm/` (settings, transport, response, router), `offline_memory.py`, `session_store.py`, `env_file.py`; MK9 adds `hindsight_memory.py` | domain, ports, logging |
| `views/` | `sections.py`: MEMORY / EVIDENCE / PROPOSAL / DECISION / RETENTION | domain |
| `controllers/` | `terminal_engineer.py` (implements the engineer port), `debug_controller.py`, `inspect_controller.py` | domain, ports, services, views, adapters |
| `app.py`, `cli.py` | composition root and entry point: the only place adapters are wired to services | anything |

`logging_setup.get_logger(__name__)` everywhere; only the CLI calls `configure_logging()`. Every log line
carries `session=<id>`.

**Never created or edited on this branch** (they exist on `rama-m0`; touching them causes merge
conflicts): `src/debugagent/__init__.py`, `config.py`, `schemas.py`, `memory/`, `seeds/`,
`pyproject.toml`, `.env.example`, `m0/`, `tests/support.py`, `tests/test_schemas.py`.

- `debugagent` stays a **namespace package** here (no `__init__.py`), so it merges cleanly with
  Rama's regular package.
- Run without packaging: `PYTHONPATH=src python3 -m debugagent.cli debug` ·
  tests: `PYTHONPATH=src python3 -m unittest discover -s tests`.
- Stdlib only (`urllib`, `argparse`, `json`, `unittest`, `uuid`). No `pyproject.toml` until MK9.
- LLM settings read from env inside `llm/router.py`, using the variable names already agreed in
  `rama-m0:.env.example`: `LLM_PRIMARY_{PROVIDER,MODEL,BASE_URL,API_KEY}`,
  `LLM_FALLBACK_{PROVIDER,MODEL,BASE_URL,API_KEY}`, `LLM_TIMEOUT_S`, `LLM_MAX_RETRIES_PRIMARY`.
- Mukul's schemas live in `pipeline/types.py` with the contract §3 names, so they can be
  promoted into the shared `schemas.py` later without renaming.

## 2. Memory port (the pinned interface)

Defined on this branch in `ports/memory_port.py`. Plain dicts, shaped exactly like the
`to_dict()` output of Rama's dataclasses at `rama-m0` `7254fc3`, so the MK9 adapter is a
thin wrapper.

```python
class MemoryFailure(RuntimeError):
    kind: str            # "unavailable" | "auth" | "schema" — never shown as "no memory"

class MemoryPort(Protocol):
    def recall_and_classify(self, query: str) -> dict: ...   # MemoryView
    def retain(self, case: dict) -> dict: ...                # MemoryCase dict -> RetentionDecision dict
```

```python
MemoryView = {
  "bank_id": str, "recalled_at": str,
  "report": {                                  # MatchReport.to_dict()
    "candidates": [Candidate], "excluded": [Candidate],
    "top_score": float, "threshold_used": float },
  "abstention": {                              # AbstentionDecision.to_dict()
    "abstained": bool,
    "relevance_class": "relevant|partial|irrelevant|contradictory|stale",
    "top_score": float, "threshold_used": float, "reason": str },
}
Candidate = {                                  # MatchCandidate.to_dict() + two joined fields (Q8)
  "case_id": str, "relevance_class": str, "score_final": float,
  "environment": {str: str}, "reason": str, "conflicts_with": [str],
  "text": str,            # joined from RecallResult.text by case_id
  "outcome": str|None }   # joined from RecallResult.outcome

MemoryCase (dict, all 10 keys required; validated fail-closed by Rama) = {
  "problem_signature": str, "symptoms": [str], "environment": {str: str (non-empty)},
  "observed_evidence": [str (URI)], "investigation_trace": [str],
  "failed_approaches": [{"approach": str, "why_failed": str}],
  "root_cause": str|None, "resolution": str|None,
  "outcome": "resolved|workaround|escalated|deferred",
  "verification_notes": str, "session_id": str }

RetentionDecision = { "retained": bool, "reason": str, "memory_case_id": str|None,
                      "validated": bool, "case_key": str|None }
```

Rules the pipeline holds regardless of the port implementation:
- Cite only `report.candidates[*].case_id`. `excluded` is shown in the trace, never cited.
- `abstention.abstained == True` → every hypothesis is `generic` with no citations.
- `partial` is not abstention: show it, labelled as a weak reference.
- Scores are never shown as quality or compared across queries; `reason` is safe to show.
- A candidate's `environment` / `text` is displayed as **past conditions**, never enters `Evidence`.
- `MemoryFailure` propagates to the CLI as a one-line error, never as "no relevant memory".

## 3. Milestones

**Progress (2026-09-28):** MK0 ✅ (14/14 on final config, see `phase1-mukul-m0-plan.md` §6) ·
MK1 ✅ (`pipeline/types.py`, `pipeline/memory_port.py`, `tests/loop_support.py`; 21 tests green),
built on the §5 proposals Q1/Q3/Q5 plus `EvidenceItem.name` (Q4) — each is one field to change if
Rama disagrees. · MK4 ✅ (`llm/router.py`; 28 fake-transport tests; live smoke:
Baseten served in 2.8 s, forced failover served by OpenRouter in 4.1 s with `fallback_used=true`) ·
MK3 ✅ (`pipeline/normalize.py`; 12 tests; hint/text conflict fails closed, decided 2026-09-28). · MK2 ✅ MK5 ✅ MK6 ✅ MK7 ✅ MK8 ✅
(`recall_match.py`, `hypothesize.py`, `evidence.py`, `verify.py`, `investigate.py`, `render.py`, `cli.py`;
104 tests; live CLI run through Baseten with the offline memory file). **Only MK9 remains.**

MK1–MK8 run fully **offline** against `FakeMemoryPort` (canned views + retain spy) and a fake
LLM transport, both in `tests/loop_support.py`. No keys, no Hindsight, no Rama code.

| ID | Work | Files | Exit criterion | Depends on |
|---|---|---|---|---|
| **MK0** | Runtime verification of providers, real hypothesis schema, response shapes, limits, versions (`docs/phase1-mukul-m0-plan.md`) | `mk0/probe.py` | Every probe PASS or has a recorded decision | keys (E2) |
| **MK1** | Types: `DebugInput`, `NormalizedDebugCase`, `Evidence`, `Hypothesis`, `VerificationResult`, `Resolution` (frozen dataclasses, `from_dict` fail-closed) + `MemoryPort` / `MemoryFailure` + `FakeMemoryPort` | `pipeline/types.py`, `pipeline/memory_port.py`, `tests/loop_support.py` | T1 for the new types green | §5 answers |
| **MK2** | `recall_match`: `port.recall_and_classify(query)` → MEMORY section text (case id, original env verbatim, class, reason, `conflicts_with`; abstention reason when abstained) | `pipeline/recall_match.py` | Tests: relevant / partial / contradictory / abstained render; `MemoryFailure` propagates | MK1 |
| **MK3** | Deterministic normalizer: signature = first line; symptoms = other lines + measurements; environment = hints + `key=value` from text for `service`, `runtime`, `proxy`, `region`; missing → `None` | `pipeline/normalize.py` | Tests: nothing invented; engineer values never overwritten; blank → `InputError` | MK1 |
| **MK4** | `llm/router.py`: OpenAI-compatible POST via `urllib`; status → error class; strict routing, falling back to schema hint on 400/404 for the same provider; primary → fallback router; `LLMResult(provider, model, fallback_used)` | `llm/router.py` | Fake-transport tests: failover on timeout/429/5xx only; 400/401/402/403/404 never fail over; invalid JSON → retry once → failover → `StructuredOutputError` | — |
| **MK5** | Hypothesis generation: one prompt (normalized case + candidate texts), response JSON schema, local validation; app derives `relevance_state` (Q3); abstained → all `generic` | `pipeline/hypothesize.py` | **T3** citation integrity + **T6** generic path green | MK2, MK4 |
| **MK6** | Evidence from engineer input only (`source`, `captured_at`, `known`); verify with `mismatched_environment_fields` + `insufficient_evidence` rule (Q4) + required engineer decision; resolution from engineer answers | `pipeline/evidence.py`, `pipeline/verify.py` | **T4** no auto-decide green; no candidate field reaches `Evidence` | MK1 |
| **MK7** | `investigate()` + `MemoryCase` assembly (§4) + retain call site only after `engineer_decision`; `session_id = uuid4` | `pipeline/investigate.py` | **T5** retention gate green (spy); `MemoryFailure(kind="schema")` surfaces, nothing silently dropped | MK5, MK6 |
| **MK8** | CLI (`argparse` + `input()`): interactive `debug` renders MEMORY / EVIDENCE / PROPOSAL / DECISION, prompts verify → resolve → retain, shows `memory_case_id`; `inspect` prints last session trace; one-line errors. Port is injected, so tests drive it with the fake | `cli.py` | Test: scripted stdin through the full loop with fake port + fake LLM completes | MK7 |
| **MK9** | **Integration (joint, the only step touching both sides):** merge `rama-m0` and `mukul-loop`; add `pipeline/memory_adapter.py` (wraps `HindsightMemoryStore.recall` + `classify_candidates` + `MemoryCase.from_dict` + error mapping into `MemoryPort`); add the console entry point to `pyproject.toml` | adapter + `pyproject.toml` | Existing 78 memory tests + all loop tests green on the merged branch; live Act 1 → retain → Act 2 | MK8 + Rama done |

### Shared steps after MK9

| Step | What | Who |
|---|---|---|
| Seed fix | Resolve the demo/seed conflict (§6) | Rama (seeds) + Mukul (script) |
| Calibration | C2 threshold pass on the **final** seed bank | Rama |
| Rehearsal | Scripted-stdin runs of Act 1/2/3 on the final bank; README clean-checkout check | both |
| Submission | video, README, checklist | both |

### Timeline

| Slot | Mukul |
|---|---|
| 28 Sept, now | MK0 (≈1 h) ∥ Q1–Q8 with Rama → MK1 → MK2 → MK3 |
| 28 Sept, evening | MK4 → MK5 → MK6 |
| 29 Sept, morning | MK7 → MK8 → **MK9 with Rama** |
| 29 Sept, midday | Seed fix, calibration, rehearsal, video, submission |

Critical path: MK1 → MK2 → MK5 → MK7 → MK8 → MK9. MK3, MK4 and MK6 fit in around it.
**MK9 is the single biggest risk:** book the slot with Rama now, and do a dry-run merge
(`git merge --no-commit rama-m0` then abort) after MK2 to confirm the paths really don't collide.

**Cut order if behind** (never cut the loop): `inspect` → `modify` decision (treat as reject +
note) → rendering polish. The LLM fallback stays: it is a DoD item.

## 4. `MemoryCase` assembly (MK7)

| Field | Source |
|---|---|
| `problem_signature`, `symptoms` | `NormalizedDebugCase` |
| `environment` | normalized environment with `None` values **dropped** |
| `observed_evidence` | `Resolution.evidence_refs` (URIs only, per C5) |
| `investigation_trace` | session log: recall summary, hypotheses shown, verification statuses, decision |
| `failed_approaches` | `Resolution.failed_approaches_this_session` |
| `root_cause` | `Resolution.root_cause_confirmed` (`None` unless confirmed) |
| `resolution` | `Resolution.action_taken` |
| `outcome` | `Resolution.outcome` (Q1) |
| `verification_notes` | engineer note(s) from `VerificationResult` |
| `session_id` | the investigation uuid |

## 5. Questions to confirm with Rama before MK1

| # | Question | Proposal |
|---|---|---|
| Q0 | Branch independence + memory port (§1–§2) | Adopt. Rama keeps `rama-m0` as is; Rama writes nothing new, MK9 adds the adapter |
| C1/C3/C4/C5 | Freeze items | `llm/` = Mukul · service/API domain · labels MEMORY / EVIDENCE / PROPOSAL / DECISION · evidence as URIs |
| Q1 | `Resolution` lacks `outcome`, which `MemoryCase` requires | Add `outcome` to `Resolution` |
| Q3 | `Hypothesis.relevance_state = supported` reads like verification | Derive it in code from cited classes (relevant → supported, partial → weak-reference, contradictory → conditional, none → generic); CLI shows it as "memory-backed", never with verification styling |
| Q4 | Nothing defines what `verify()` checks | `insufficient_evidence` when Evidence has no known items or lacks an env key the cited case depends on; otherwise the engineer marks supported/contradicted against EVIDENCE. Never computed from memory |
| Q5 | `hypothesis_ref` format | `H1`, `H2`, … per session |
| Q6 | Separate `verify` / `resolve` commands need saved session state | One interactive `debug` session + `inspect` |
| Q7 | Abstained **and** contradictory | MEMORY shows both sides; PROPOSAL generic |
| Q8 | `MatchCandidate` has no case text or outcome, but hypotheses and the "previously failed approach" warning need them | Adapter joins `text` + `outcome` from `RecallResult` by `case_id` (§2). No change to Rama's code |

## 6. Demo/seed conflict (blocks rehearsal, fixed after MK9)

The demo script (`docs/phase1-demo-scenario.md`, on `rama-m0`) doesn't match the seeds:

- **Act 1** ("payments-service returns 502 for payloads above 2 MB") should show **no useful
  memory**, but `seed-002` (*orders-api: upstream 502 for oversized bodies*) is close to the same
  issue and will likely be recalled as relevant.
- **Act 2** expects a match on a **different service**, but `seed-003` is on the same
  service (`media-uploader`).

Fix: change the Act 1 query to a family with no seeds (or drop `seed-002`), and change the Act 2
service name. Confirm with real scores right after MK9.

## 7. Not in Phase 1 scope for Mukul

`update()` / `invalidate()` (non-functional on SDK 0.10.1) · LLM normalizer · `reflect()` ·
web UI · second domain · auto-retention · new dependencies.

## 8. MK4 / MK3 design

### MK4 — `llm/router.py` (every value comes from MK0 §6)

- **Interface:** `LLMRouter.complete_structured(prompt, *, schema, name, check=None) -> StructuredResult`
  (`data`, `provider`, `model`, `fallback_used`, `attempts`). `complete()` for plain text is
  skipped: its only planned consumer is hypothesis generation, which is structured.
- **Routes from env:** `LLM_{PRIMARY,FALLBACK}_{PROVIDER,MODEL,BASE_URL,API_KEY}`; timeout
  `LLM_{ROLE}_TIMEOUT_S`, else `LLM_TIMEOUT_S` (primary 10 s, fallback 17 s in `.env.live`); invalid-output
  retries `LLM_MAX_RETRIES_PRIMARY` (1). Unknown provider or missing value → `LLMConfigError` at startup.
- **Payload:** `response_format` json_schema strict, `max_tokens` 1500, plus per-provider reasoning
  control (baseten `chat_template_kwargs.thinking=false`, openrouter `reasoning.effort=low`). Never
  `require_parameters` (Space Bunny returns 404 with it).
- **Parser:** read `content` only (list content is joined); ignore `reasoning*`; `finish_reason=length`,
  null or whitespace content, non-JSON, non-object root → invalid; strip a ```json fence.
  Then local schema validation, then optional `check(data)` (e.g. citation integrity) → invalid.
- **Flow:** invalid output → retry the same route once → fall back → `StructuredOutputError`.
  Timeout / unreachable / 429 / 5xx / 200-with-error → fall back immediately. 401/403 → `LLMAuthError`,
  400/402/404/other 4xx → `LLMConfigError`, **never fail over**. Nothing unvalidated is ever returned.
- **Visibility:** every attempt is recorded (`route`, `provider`, `model`, `outcome`, `error_class`, `ms`)
  and logged on `debugagent.llm`; the API key never appears in reprs, logs or attempts.
- **Transport** is injectable `(url, key, payload, timeout) -> (status, text)`, raising
  `TimeoutError`/`OSError`, so tests use a fake with no network.

### MK3 — `pipeline/normalize.py` (built)

`normalize(raw: DebugInput) -> NormalizedDebugCase`, deterministic, no LLM.

| Output | Rule |
|---|---|
| `problem_signature` | first non-empty line of `description`, whitespace collapsed, trailing `.` stripped, case kept |
| `symptoms` | remaining non-empty description lines that are not pure `key=value` lines, then `measurements`; order kept, exact duplicates dropped |
| `environment` | every taxonomy key (`service`, `runtime`, `proxy`, `region`) present, default `None`; filled from `key=value` / `key: value` in the text (taxonomy keys only, so `size=2MB` is not read as environment); then `environment_hints` added (any valid key, engineer-supplied) |
| `raw_description` | the description, unchanged |
| `source_case_ids` | `[]` |

- Blank description → `InputError`. Output is validated with `NormalizedDebugCase.from_dict` →
  `NormalizationError` listing the fields.
- **Decided (2026-09-28):** a hint that disagrees with the text (text `service=a`, hint `service=b`),
  or the text stating one key twice with different values, **fails closed with `NormalizationError`**
  naming the key. Keys are matched as whole tokens, so `web-service=foo` is not `service`.
- Tests: nothing invented (unstated keys stay `None`); values verbatim; hints never overwritten;
  non-taxonomy `a=b` in text ignored; conflict raises; blank raises; same input → same output.

## 9. How to run (before MK9)

```
PYTHONPATH=src python3 -m unittest discover -s tests             # 112 offline tests
PYTHONPATH=src python3 -m debugagent.cli debug                   # live LLM + offline memory file
PYTHONPATH=src python3 -m debugagent.cli inspect                 # trace of the last session
PYTHONPATH=src python3 tests/live_flows.py                       # 6 live end-to-end flows (~6 LLM calls)
```

`debug` reads `.env.live`, recalls from `demo/offline-memory.json` (queries containing "upload" get
the relevant view; anything else gets the abstained view), and writes retained cases to
`.debugagent/offline-retained.jsonl` (git-ignored). MK9 swaps `--memory` to the Hindsight adapter.

## 10. MK9 checklist (joint)

1. Rama confirms §5 (or the proposals stand), pins `hindsight-client==0.10.1`, shares Cloud URL/key.
2. `git merge origin/rama-m0` into `mukul-loop` (verified clean; 139 tests pass together).
3. Add `adapters/hindsight_memory.py` (implements `MemoryPort`): `recall_and_classify` = `store.recall()` → `classify_candidates()` →
   `to_dict()` + join `text`/`outcome` by `case_id` (Q8); `retain` = `MemoryCase.from_dict()` → `store.retain()`
   → `to_dict()`; map `MemoryUnavailable`/`MemoryAuthError`/`MemorySchemaError` → `MemoryFailure` kinds.
   Test it with the seam check already proven in the trial merge.
4. `app.build_memory`: `--memory hindsight` builds the adapter; make it `DEFAULT_MEMORY`. The architecture test's `RAMA_MODULES` rule gets one exception: `adapters/hindsight_memory.py` may import `debugagent.memory` and `debugagent.schemas`.
5. `pyproject.toml`: console entry point `debugagent = "debugagent.cli:main"`; `.env.example`: provider swap +
   `LLM_{PRIMARY,FALLBACK}_TIMEOUT_S`.
6. Change-log entries for MK0–MK9.
7. Live: Act 1 → retain → Act 2 recalls it; re-run `mk0/probe.py --only D1,D2,S2,X3`.

## 11. Live flow test (`tests/live_flows.py`)

Real CLI, real providers, offline memory. Last run 2026-09-28: **all 6 flows pass** (30 checks).

| Flow | Checks |
|---|---|
| A · Act 1, no matching history | memory abstains; every hypothesis generic, no citations; case retained; no unknown env stored |
| B · Act 2, memory-backed with an evidence gap | relevant case + original environment + differences shown; memory-backed hypothesis cites it; current system described correctly; **insufficient evidence** despite the strong match; failed approach stored; `inspect` trace |
| C · primary down | primary failure logged; fallback served with `fallback_used=True`; case retained |
| D · not resolved | nothing retained |
| E · memory unavailable | one-line error; never "no memory" |
| F · bad API key | one-line auth error; no failover; nothing retained |

First run failed flow C (fallback added a `rank` field / returned a bare list); fixed as recorded in the decision log.
Re-run on demo morning.
