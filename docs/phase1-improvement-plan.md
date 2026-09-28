# Phase 1 Improvement Plan — against the hackathon criteria

**Date:** 2026-09-28, 22:45 IST · **Deadline:** MVP + submission 29 Sept afternoon (~15 h, incl. sleep).
**Base:** `integration/phase1` @ `1f29c30` plus the fixes on `mukul/phase1-fixes` (235 tests).
**What exists today:** `docs/phase1-implemented.md`. **Phase 2 plan:** section 7 below.
**Evidence:** fresh-bank dry runs on 2026-09-28 (banks `demo-dryrun-*`, never the shared or recording bank).

## 1. Where Phase 1 stands against the judging criteria

| Criterion (weight) | What judges look for (`hackathon-requirements.md`) | Today | Gap |
|---|---|---|---|
| **Innovation (30%)** | Novel use of persistent memory, not a chatbot | Memory-informed investigation with an evidence gate, abstention and contradiction surfacing | The demo *looks* like a form: ~25 prompts hide the idea |
| **Use of Hindsight (25%)** | Memory central; **visible learning curve**; before/after on a similar task | Real retain/recall on Hindsight Cloud; seeded memory changes Act 2 | **Every memory-backed moment uses a pre-seeded case.** Nothing learned *during* the demo is reused on camera |
| **Technical (20%)** | Code quality, architecture, proper Hindsight integration | 235 tests, typed contract, fail-closed validation, failover, live-verified | Layered refactor not merged; README setup not verified from a clean checkout |
| **User Experience (15%)** | Clear workflow, intuitive for the persona | Four trust sections render well | ~25 inputs per session; no non-interactive input; errors were multi-line (fixed) |
| **Real-world impact (10%)** | Genuine professional problem, realistic data | Real problem (debugging knowledge loss) | Seeds are labelled "synthetic"; the organizers ask for realistic scenarios |

**Verdict:** the engine is sound; the *demo* under-sells it. Two changes move the two biggest criteria
(55% of the score) and cost little: show learning that happens live (Act 4), and show the same issue
**with and without** memory side by side.

## 2. Already fixed tonight (`mukul/phase1-fixes`, needs Rama's review)

| Fix | Demo effect (verified live on a fresh bank) |
|---|---|
| `dedupe_by_case` joins a case's facts | Act 2 recalls the root cause and the fix, not only the symptom |
| Failed approaches, proxy, region in metadata | MEMORY shows *"Failed before: …"* and the full original environment |
| Adapter drops `"unknown"` placeholders | No more "runtime (then unknown, now …)" |
| Fallback retry, no extra fields, bare-array wrap | Removes the "two primary attempts returned INVALID_OUTPUT" seen in Rama's rehearsal |
| CLI never drops engineer input; typos don't end a session; one-line errors | Survives a live demo typo and a Hindsight 504 |
| Demo script narrates Act 3 as "conflict surfaced", not "abstains" | Matches behaviour (0/5 fresh banks abstained; 5/5 show both sides) |

## 3. The plan

### P0 — before recording (tonight / early morning)

| # | Item | Criteria moved | Owner | Size | Status |
|---|---|---|---|---|---|
| 1 | **Rama reviews and merges `mukul/phase1-fixes`** into `integration/phase1` (PR) | all | Rama | 20 min | waiting |
| 2 | **Act 4 — live learning.** After Act 3, the Act 1 issue recurs: *"billing-service creates duplicate invoice rows after the nightly job retries"*. MEMORY recalls **the case retained live in Act 1** with its root cause, fix and failed approach; the proposal cites it | Hindsight 25%, Innovation 30% | Mukul (script) | 15 min | **verified live 2026-09-28**: recalled as `relevant`, both memory-backed hypotheses cite it |
| 3 | **`--no-memory` baseline.** Run the Act 2 issue once with memory disabled (generic proposal), then with memory (memory-backed, leads with the recorded fix): the before/after on the *same* issue, which is the organizers' key moment | Hindsight, Innovation | Mukul | 30 min | to build: a MemoryPort that returns an abstained view with reason "memory disabled for this run"; nothing retained |
| 4 | **Slim input flow**: one paste box; verify only the hypothesis you pursue (`H1 s y`); short resolution (`what fixed it` + outcome letter); `--file issue.txt` for repeatable takes | UX 15% | Mukul | 2 h | planned; every trust rule kept (each hypothesis still gets a logged decision; unpursued = reject + note) |
| 5 | **README from a clean checkout**: install (`hindsight-client==0.10.1`), `.env` fields, fresh bank + ledger, seed, run, `inspect` | Technical 20% (DoD 1) | Mukul | 30 min | README section exists on the branch; verify on a clean clone |
| 6 | **Pin `hindsight-client==0.10.1`** in `pyproject.toml` | Technical | Rama | 2 min | open |

### P0 #4 in detail — slim input flow

Today one resolved session asks about **25** things; the target is about **6**, with every trust rule kept.

| Stage | Today | Slim flow | Trust rule kept |
|---|---|---|---|
| Describe the issue | 3 blocks (description, measurements, facts) | **1 paste box**: description, `key=value` lines and log lines together; the normalizer already sorts them | Unstated values stay `unknown` |
| Current facts | always asked | asked **only** when a field a cited case depends on is missing | Missing evidence ⇒ `insufficient_evidence` |
| Verify | up to 4 questions × 3 hypotheses = 12 | *"Which will you pursue? [H1/H2/H3/none]"* then one answer such as `s y` (supported, past case relevant) | Every hypothesis still gets a logged decision; unpursued ones = `reject` + note "not pursued" |
| Resolve | 7 questions | *"What fixed it?"* (blank = not resolved), *"What did you observe?"*, one-letter outcome, optional failed approaches; evidence refs optional | Only a reported resolution is retained; failed approaches still captured; the observed result is never defaulted |
| Repeatable input | none | `--file issue.txt` feeds the paste box | Same code path as typed input |

Two decisions taken: unpursued hypotheses use `reject` + note (no contract change); "what did you observe"
stays a required question (it is the verification evidence and must not be invented).

### P1 — improves the submission if time allows

| # | Item | Criteria | Owner | Size | Risk |
|---|---|---|---|---|---|
| 7 | Rewrite the 6 seeds as realistic incident write-ups (real error strings, tool names, log excerpts; still fictional systems) | Impact 10%, "realistic data" | Rama | 1 h | **re-rehearse all acts** (scores shift) |
| 8 | Recording aids: a one-line "memory state" banner (bank, cases, last retained id); a `--quiet` flag to hide the log lines on camera | UX | Mukul | 30 min | low |
| 10 | **Citation gate for contradictory cases**: `citable()` excludes `contradictory` candidates (from Rama's VLSI probe). Act 3 then shows both sides and stays generic on every bank | Innovation (trust), demo stability | Mukul | 20 min | low; re-run Act 3 once |
| 9 | Merge the layered refactor (`mukul-loop`: controllers / services / domain / ports / adapters / views + architecture test) | Technical 20% | Mukul + Rama | 3 h | moves every file under a rehearsed demo — **do after recording, before submission**, or in Phase 2 |

### Deliberately not in Phase 1

Threshold calibration beyond the demo queries, Hindsight tag filters, memory curation
(update/invalidate), ticket/CI/PR integrations, a web UI. They belong to Phase 2 (2 Oct) and the story
for the articles, not to tomorrow's recording.

## 4. Demo narrative (2–5 min) after P0

| Beat | Act | What the judge sees | Criteria |
|---|---|---|---|
| Problem | — | Debugging knowledge lives in people's heads; each session starts from zero | Impact |
| Before | 1 | New issue (billing duplicates): *no usable memory*, generic proposal, engineer resolves; **case retained, fact id shown** | Hindsight |
| After (seeded history) | 2 | Upload issue: past case recalled **with its original environment and "Failed before"**; H1 leads with the recorded fix; engineer verifies | Innovation |
| Same issue, no memory | 2b (`--no-memory`) | Generic proposal on the same issue: the difference memory makes, side by side | Hindsight (before/after) |
| Honest disagreement | 3 | Two past cases disagree: shown side by side, never resolved by the agent | Innovation, trust |
| **Learning curve** | 4 | Act 1's issue recurs: **the case learned 3 minutes ago** is recalled and cited, with the fix and the failed approach | Hindsight 25% |
| Trust | — | Memory informs, evidence verifies, the engineer decides: unknown facts stay unknown, fallback visible in logs | Technical |

Rules from the demo script still hold: fresh bank + fresh ledger, run the acts in order once, keep a
backup take, never narrate thresholds as validated.

## 5. Submission deliverables

| Deliverable | Owner | Suggested focus |
|---|---|---|
| Demo video (2–5 min) | both (Mukul records, per Rama's request) | Section 4 beats; ~3 min |
| Technical article — Mukul | Mukul | *"An LLM router that never returns unvalidated output"*: MK0 findings (reasoning exhausting the token budget, strict routing 404s, fallback quirks) and the verification gate |
| Technical article — Rama | Rama | *"What Hindsight actually stores"*: fact extraction, dedupe, metadata vs text, abstention and contradiction surfacing |
| Social post × 2 | each | The Act 4 moment: "it remembered what I fixed 3 minutes ago, including what didn't work" |
| Live runnable demo | both | README from a clean checkout (P0 #5) |

## 6. Timeline

| When (IST) | Mukul | Rama |
|---|---|---|
| 28 Sept, 23:00 | Send findings + PR link; build `--no-memory` (P0 #3) | Review/merge fixes (P0 #1), pin SDK (P0 #6) |
| 29 Sept, 08:00–10:30 | Slim input flow (P0 #4), README check (P0 #5) | Optional realistic seeds (P1 #7) → only if re-rehearsed |
| 10:30–11:30 | Full rehearsal on a fresh bank: Acts 1 → 2 → 2b → 3 → 4 | Rehearsal review |
| 11:30–13:00 | Record the demo (+ backup take) | Article draft |
| 13:00–afternoon | Article, social post, submission checklist | Article, social post |

## 7. Phase 2 (after submission, target 2 Oct)

Inputs: the limitations in `docs/phase1-implemented.md` §6, tonight's fresh-bank dry runs, and Rama's
synthetic VLSI evaluation (trust boundary held; retrieval did not generalise). Phase 2 is about making
retrieval **trustworthy across banks and domains** and taking the typing out of the engineer's day.

| # | Theme | Work | Owner | Exit criterion | Source |
|---|---|---|---|---|---|
| 2.1 | **Retrieval calibration** | Labelled query set per domain (relevant / partial / vague / irrelevant / contradictory); record `final` and `semantic` scores across several fresh banks; set thresholds per domain; expose `semantic_floor` and `contradiction_margin` through config (contract L5) | Rama | Irrelevant queries abstain on every bank in the set; results recorded raw | L1, L5; false "relevant" matches (payments-api, checkout-api, VLSI) |
| 2.2 | **Conflict handling** | Group recalled cases by normalized root cause instead of one margin; keep contradictory cases non-citable (P1 #10, if not done); multi-case conflict tests | Rama + Mukul | Act 3-style conflicts behave the same on every fresh bank | L2, L3; Rama's VLSI "unstable with multiple conflicts" |
| 2.3 | **Domain-neutral memory** | Store **every** environment key in metadata (`env.<key>`); rebuild the environment from them on recall; taxonomy as per-domain config for the normalizer | Rama (store) + Mukul (normalizer) | VLSI context (node, PVT corner, tool, stage) survives retain → recall → comparison | L4; Rama's VLSI finding |
| 2.4 | **Second domain: VLSI** | Realistic seed set from Rama's evaluation; domain taxonomy; calibration pass (2.1) for it | Rama | The four-act demo runs in the VLSI domain on a fresh bank | Original project definition (VLSI persona) |
| 2.5 | **Memory lifecycle** | Correct / retire / restore cases via the Hindsight REST API (SDK 0.10.1 has no binding); staleness policy; ledger keyed on bank id, or backend idempotency | Rama | A wrong root cause can be corrected and stops being recalled | L6, L8 |
| 2.6 | **Input capture** | Issue from a ticket, alert or CI failure; environment from deploy metadata; resolution and failed approaches extracted from the fix PR description, confirmed with one keystroke (a second LLM call per session); retention proposed when the fix merges, still engineer-approved | Mukul | A session started from a ticket needs ≤ 3 engineer inputs | L7; "too manual" feedback |
| 2.7 | **Code structure** | Integrate the layered refactor (`mukul-loop`) onto the integration branch; architecture test in CI; console entry point `debugagent` | Mukul + Rama | One structure, all tests green, entry point installed | Technical criterion |
| 2.8 | **Evaluation record** | Paired with/without-memory runs on the same issues: recall precision (engineer-marked relevance), failed approaches avoided, verification discipline; raw observations, no invented numbers | both | A published table of raw observations per run | Project definition, evaluation plan |
| 2.9 | **Realistic data** | Rewrite seeds as realistic incident write-ups (real error strings, tool names, log excerpts; fictional systems) | Rama | Seeds read like real post-incident notes | L9; organizer "realistic data" |

**Order:** 2.1 and 2.3 first (every later item depends on trustworthy retrieval), then 2.2 and 2.4, then
2.5–2.9. Out of Phase 2: a web UI, multi-user team banks, automatic fix execution.
