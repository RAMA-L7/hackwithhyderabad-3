# Audit 001: production readiness of the complete investigation flow

Date: 2026-10-01. Scope: `investigate()` end to end — memory, evidence, the P6 worker stage, verifier
findings, patch proposals, hypotheses, verification, resolution, retention. Commit under audit:
`fb9bcbf` plus the uncommitted P4/P5/P6/ADK/P6-integration work.

**Verdict: the flow is production-ready within its stated boundaries, and three concrete defects were
found and fixed.** No boundary was violated in the shipped code. One documented claim was false, and two
ranking defects made a reported measurement meaningless. All three are recorded below rather than quietly
patched, because what the audit was really for is the list.

## What was audited, and how

A realistic scenario rather than a synthetic one: the repository's own
`demo/inputs/act2-media-uploader.json` (an nginx body-limit outage) driving a temporary repository
containing a real nginx config, a real nginx error log, and unrelated application code — through the real
`FileSourcePort` / `RepoPatchSourcePort`, a real `Coordinator` and a real `MemoryLane`.

Only four things are doubled, and each sits outside the code under test: the LLM, the Hindsight client, the
engineer terminal, and time. The doubles come from the repository's own `loop_support` / `support` helpers
rather than bespoke ones, so a contract change surfaces here instead of hiding behind a purpose-built fake.

58 audit tests in `tests/test_p7_production_audit.py`, plus 942 pre-existing tests unchanged.

## Boundaries verified

| Boundary | How it was proven | Result |
|---|---|---|
| Full chain runs and produces one structured result | every link asserted present, and the trace ordering asserted | holds |
| Memory is knowledge, never evidence | real `HindsightMemoryPort` over real seed cases; recalled ids and candidates checked absent from `Evidence`; evidence values compared against the case | holds |
| Verifier observations never become facts | every finding's `ref` checked against evidence item names and values; item `source` restricted to `case`/`engineer` | holds |
| Engineer decisions authoritative | rejection enforced for all hypotheses with findings present; decisions asked once per hypothesis; resolution is the engineer's | holds |
| Authorization before every execution | an ordered log of `authorize` / `execute` events; every execution asserted to have a prior authorization of the same task; worker resolution asserted to follow authorization | holds |
| One Coordinator, one MemoryLane | a second coordinator refused; every `lane.call` in a session asserted to be the same object | holds |
| Memory serialised, non-memory overlapping | lane held by hand: non-memory dispatch **completes** while memory work **blocks** | holds |
| Refusal / failure / empty success / success distinguishable | all four produced in one flow and asserted distinct in one serialized record | holds |
| Path containment and read-only | absolute, traversal and symlink escapes refused; full-tree content + mtime + size digest unchanged after a complete run | holds |
| Deterministic ordering | repeated runs, reordered targets, and a **fresh interpreter** (different hash seed) all byte-identical | holds |
| Phase 1 / P3 / P4 unchanged when worker inputs omitted | exact Phase 1 key set; P4-equivalent control compared after stripping volatile fields | holds |
| ADK optional | `google.adk` blocked at `builtins.__import__` for a full run; `adk_bridge` never imported | holds |

## Findings

### F1 — a documented claim was false (fixed, no code change)

`investigate()` and the P6 documentation stated that placing the worker stage before hypothesis generation
meant "the engineer's proposal can be informed by what the workers observed."

It cannot. `build_prompt(case, memory)` receives only the normalized case and the ranked memory context;
worker output never reaches the model. The ordering is real, but what it buys is narrower than claimed: by
the time the engineer is asked to decide, the findings and any proposal have already been *shown* to them.

The wording now says exactly that, in `investigate()` and its docstring, and a test asserts worker output
is absent from the prompt.

This is deliberately **not** "fixed" by passing findings to the LLM. Doing so would move worker observations
into the proposing step — a trust-boundary change that needs its own decision, not one that should arrive
through plumbing.

### F2 — the relevance score counted the template, not the content (fixed)

Found by putting a deliberately irrelevant file (`app/unrelated.py`, contents `VERSION = '1.0'`) in the same
run as the real culprit. Every finding scored **2**:

```
app/unrelated.py    score=2
nginx/site.conf     score=2
logs/nginx-error.log score=2
```

Cause: every worker observation opens with a fixed sentence that echoes the issue signature — `OBSERVED in
<file> for issue '<signature>'. Current-system observation, not a verification and not a root cause.` — and
the score was computed over the whole string. So the template matched the case's own terms on every finding.
The ranking's primary key carried no information, the ordering degenerated to alphabetical by `ref`, and the
transcript told the engineer each file "matches 2 terms".

Fix: score only the quoted material, with the echoed signature removed (`_scorable_text`). An unrelated file
now scores 0, and a file that genuinely contains a reported symptom scores above it.

### F3 — symptom terms were stranded by punctuation (fixed)

`_relevance_terms` split symptoms on whitespace and separators, which kept the trailing character:
`"fail with ECONNRESET;"` produced the term `econnreset;`. No log line contains a semicolon, so the term was
unfindable and every symptom-driven score came back zero — including in a log quoting the symptom verbatim.

Fix: tokenise with `[a-z0-9][a-z0-9._+-]*`, which drops separators at the edges.

Both F2 and F3 are small, local corrections to a function I wrote one milestone ago. They are recorded here
because a ranking that cannot discriminate is worse than no ranking: it looks like a measurement.

### Not a defect: outcomes echo input order

The audit initially flagged that changing the order of `verifier_targets` changes the serialized record.
It does not — but only the per-task list changes, and it should. `FanOutResult` is positional by contract
(P5), so a caller needs to know which slot belongs to which request. The *ranked* `findings`, which is what
a reader consumes, are identical under every ordering. Both halves are now asserted separately; conflating
them is what hid the distinction.

### Accepted limitation, stated rather than fixed

Relevance is lexical: a finding scores only if the quoted material literally contains an environment value or
a symptom token. For the audited scenario the nginx config — the actual culprit — scores 0, because the
issue says "2 MB" and the config says `client_max_body_size 2m`. Semantic matching would fix that and is
new architecture, so it is out of scope for this audit. The honest current behaviour: ties are broken
deterministically by `(-score, kind, ref, task_id, content)`, so the report is stable and correct, merely not
as discriminating as a human reader would be.

> **RESOLVED after this audit.** Relevance matching now compares a canonical form of the text, so the
> config that states `client_max_body_size 2m` matches an issue that says "uploads over 2 MB". This was
> done inside the existing scorer - no new dependency, no model, no network, and no change to the trust
> boundary - rather than as the "new architecture" this entry deferred. The determinism guarantee below is
> unaffected: the sort key is unchanged, and equivalent terms are counted once so widening the equivalence
> table cannot inflate a score. Cross-unit reasoning (`500ms` versus `0.5 s`) remains out of scope,
> because it needs arithmetic rather than a table. See `tests/test_p7_relevance_matching.py`.

## Mutation evidence

Twelve mutations against the boundaries this audit claims to have proven. Every one is caught.

| Mutation | Caught by | Failing |
|---|---|---|
| Verifier findings folded into `Evidence` | P7 | 40 |
| Patch proposals written to disk | P7 | 21 |
| Worker dispatch bypassing `authorize()` | P7 | 18 |
| Worker stage skipped entirely | P7 | 48 |
| Second Coordinator constructed for the patch stage | P7 | 3 |
| Refusals widened to any error | P6 | 1 |
| Verifier section withheld until after the decision | P7 | 3 |
| Boilerplate restored into the relevance score | P7 | 1 |
| Symptom punctuation kept again | P7 | 3 |
| Ranking tiebreak made arbitrary | P6 + P7 | 1 + 2 |
| "observations" reworded to "confirmed facts" | P7 | 1 |
| Repository containment check inverted | P7 + P6 + adapters | 2 + 2 + 14 |

Two mutations are caught only by the suite that introduced the property, not by the audit:

- **Refusal semantics** is caught by P6's direct test on the record builder. It is not reachable end to end
  because the Coordinator converts worker faults into failed *results*, so the only refusals reaching the
  stage are authorization ones. P7 confirms the distinction survives a full flow; P6 pins the definition.
- **Ranking tiebreak** is caught by both, but the *score's meaning* (F2, F3) is caught only by P7. P6 tests
  that ranking is stable, not that the score measures anything.

## Regression surface

`tests/test_p7_production_audit.py` — 58 tests, 1 skipped on Windows (symlink traversal is not an escape
there, so the test would be vacuous rather than wrong).

Suite totals: **1000 tests, 0 failures, 13 skipped** (was 942 before this audit; +58).

## Residual risks

1. **Relevance is lexical** (above). An engineer reading the report must still judge relevance; the score is
   a hint, not a filter. The transcript says "observations", which is the correct word.
   *(Partly addressed: matching is now canonical, so equivalent spellings match. Still no cross-unit
   reasoning, and the score remains a hint rather than a filter.)*
2. **Worker stage ordering is presentational.** If someone later wires findings into the prompt, the
   trust-boundary analysis in this document stops applying and needs redoing.
3. **`investigate()` refuses a mismatched repository Coordinator late** — after evidence is built and memory
   has run. Fail-fast would be nicer, but the refusal is loud and the session is abandoned, so no partial
   result is reported as complete.
4. **Windows path semantics differ.** Containment is tested with absolute and `..` traversal on Windows;
   symlink escapes are covered on POSIX only. `RepositoryScope` resolves before checking, which is the
   property that matters, but the Windows symlink/junction case is not exercised here.
