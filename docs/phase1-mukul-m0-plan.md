# MK0 — Runtime Verification for Mukul's Side

**Status:** Plan. Runs before MK1 (`docs/phase1-mukul-plan.md`).
**Date:** 2026-09-28 · **Branch:** `mukul-loop` (from `main`, independent of `rama-m0`).
**Goal:** find every external fact the loop depends on that may have drifted since the planning
docs were written (provider schemas, model availability, response shapes, limits, library
versions), **before** MK4/MK5 code is written against it. Output: recorded results plus the
config/code decisions they force (§5).

## 0. What is already known

**Rama's M0 (2026-09-27, `rama-m0:m0/results/m0-results-20260927T183607Z.md`)**, 17/17 PASS:
RT-1…RT-8 (availability, structured output, error classes, failover, auth-no-failover, latency)
and H-1…H-4 (Hindsight). Those probes used a **toy prompt and a toy schema**, not the real
hypothesis contract. MK0 does not repeat the Hindsight probes: memory is Rama's, and this branch
reaches it only through the memory port.

**Checked live today, no key needed (2026-09-28):**

| # | Fact | Source | Consequence |
|---|---|---|---|
| F1 | Python 3.11.4 (python.org build), TLS to `openrouter.ai` works from stdlib `urllib` | local | macOS certificate trap does not apply here; re-check on the demo machine |
| F2 | `stealth/space-bunny-alpha` is still listed: 1M context, 524,288 max output, no expiration date | `GET openrouter.ai/api/v1/models` | Still usable, but it is a **stealth preview**, so it can disappear without notice |
| F3 | Its `supported_parameters` include `response_format`, `reasoning`, `reasoning_effort`, **not `structured_outputs`** | same | Strict routing (`require_parameters`) cannot serve it. Schema-hint is the **normal** path for primary, not an exception. The M0 "strict first, then hint" order costs a failed round trip on every call |
| F4 | `deepseek/deepseek-v4.1-flash` is also on OpenRouter **with** `structured_outputs` | same | Emergency backup route on the same OpenRouter key, reached by an env change only |
| F5 | `hindsight-client` latest on PyPI = 0.10.1 (same as Rama's) | PyPI | No drift today, but `rama-m0:pyproject.toml` says `>=0.10`. Recommend Rama pins `==0.10.1` now so a release before the demo can't change the recall shape |
| F6 | Memory-port dict shapes (`phase1-mukul-plan.md` §2) match `to_dict()` in `rama-m0:src/debugagent/schemas.py` @ `7254fc3` | `git show` | Re-check only if `git log 7254fc3..rama-m0 -- src/debugagent/schemas.py` is non-empty |

## How to run

```
# env files: .env.example (tracked, identical to rama-m0's), .env.live (Hindsight Cloud),
# .env.local (local Docker Hindsight). .env.live and .env.local are git-ignored.
python3 mk0/probe.py --selftest          # offline parser/validator/classifier check
python3 mk0/probe.py                     # live run with .env.live
python3 mk0/probe.py --env .env.local
```

Add `--only S8,D1` to re-run single probes (E1/E2 always run). Results are saved after every
probe; repeated calls run in parallel. Results: §6.

## 1. Probes

All in one stdlib script, `mk0/probe.py` (Mukul-owned path). It reads `.env`, prints a result
table, and writes `mk0/results/mk0-<UTC>.json` with raw response samples. Budget: about 40 LLM
calls and under an hour.

### A. Environment and access

| ID | Probe | Pass | If it fails |
|---|---|---|---|
| E1 | Python ≥ 3.10; TLS handshake to `openrouter.ai` and `inference.baseten.co` | both OK | Install certificates / use Homebrew Python. Record in README |
| E2 | All `LLM_PRIMARY_*` / `LLM_FALLBACK_*` vars present (names per `rama-m0:.env.example`); values never printed | all present | Get keys from Rama / provision own. Blocks S-probes |
| E3 | `GET openrouter.ai/api/v1/key`: record limit, usage and rate-limit fields | readable | If the free-tier cap is low, budget rehearsal runs or add credits before demo day |

### B. Catalog drift (re-run on demo morning)

| ID | Probe | Pass | If it fails |
|---|---|---|---|
| D1 | Primary still listed; record `supported_parameters` (F2/F3) | listed | Swap `LLM_PRIMARY_MODEL` to the F4 route; no code change |
| D2 | `GET {LLM_FALLBACK_BASE_URL}/models` lists `deepseek-ai/DeepSeek-V4.1-Flash` exactly | exact id present | Correct the env value |
| D3 | One structured call on the F4 backup route | validates | Note there is no same-key backup |

### C. Real contract conformance (the probes Rama's M0 didn't run)

Use the **actual** MK5 response schema: root object `{"hypotheses": [...]}`, each item
`hypothesis`, `supporting_case_ids`, `refutation_conditions`, `recommended_next_step`, with
`additionalProperties: false` and all properties required. `relevance_state` is **not** asked
of the model (it is derived in code, per Q3). The prompt uses realistic case texts in the shape
of `rama-m0` seeds (synthetic).

| ID | Probe | Pass | If it fails |
|---|---|---|---|
| S1 | Schema acceptance: send the schema in strict mode to each route; record 400s naming unsupported keywords | no schema rejection on the fallback; primary accepted as a hint | Drop the offending keywords (`minItems`, `pattern`, …) from the sent schema and keep them in local validation only |
| S2 | Conformance: 5 runs per route with 2 candidates → local validation | ≥ 4/5 validate per route | Raise the validation retry budget, or route primary → fallback on invalid output |
| S3 | **Citation integrity:** prompt has 2 candidate ids + 1 excluded id; count cited ids not in the candidate set, across all S2 runs; ids in the real form (16-hex keys **and** `seed-00N`) | 0 invented or mangled ids | Show the model short aliases (`C1`, `C2`) and map them back in code; the T3 check stays as the gate |
| S4 | Abstention prompt (no candidates): 3 runs per route | 0 citations in every run | Code forces `supporting_case_ids = []` when abstained (already the plan); record that the model can't be trusted here |
| S5 | Response-shape quirks across all runs: content as string vs list, ```json fences, prose before the JSON, separate `reasoning` field, `finish_reason = length`, empty content with HTTP 200, `error` object with HTTP 200 | every variant seen is recorded with a raw sample | Each variant seen gets a parser branch + a fake-transport test in MK4. Stripping a fence is allowed; accepting unvalidated text is not |
| S6 | Latency with the realistic prompt (3 case texts ≈ 1.5k tokens): 5 runs per route, p50/max; primary also with `reasoning_effort: low` | primary timeout + fallback fits ≈ 15 s | Set `LLM_TIMEOUT_S` from the max; use low reasoning effort if it cuts latency without hurting S2/S3 |
| S7 | Hint-first vs strict-first on the primary (F3): time both orders | — (measurement) | Pick a per-provider structured mode (`primary=hint`, `fallback=strict`) and drop the wasted round trip |

### D. Error surface

| ID | Probe | Pass | If it fails |
|---|---|---|---|
| X1 | Bad model id and bad key on **each** route (Rama's RT-3/RT-8 covered the primary only) | 400/404 → config class, 401/403 → auth class, no failover | Fix the status map for that provider |
| X2 | Small burst (5 back-to-back calls) on the free primary | no 429, or 429 recorded with `Retry-After` | Rehearsal must pace calls; 429 stays failover-eligible |
| X3 | Unreachable primary base URL → fallback serves (RT-6 repeated with the real schema) | `fallback_used=true` | Blocks MK4 |

## 2. Out of scope

Hindsight probes (Rama's M0 + her live suite) · Memory Defense · `update()`/`invalidate()` ·
any quality judgement of the hypotheses (the demo claims behaviour, not accuracy).

## 3. Rules while probing

- Synthetic case text only; no secrets in prompts, results or logs. The script redacts keys.
- Results files are committed; `.env` never is.
- A probe that can't run is recorded as `BLOCKED` with the reason, never silently skipped.

## 4. Exit criterion

Every probe is PASS, or FAIL/BLOCKED **with a recorded decision** from §5. Then MK1 starts, and
MK4/MK5 are written against the recorded shapes rather than against the docs.

## 5. Decisions MK0 feeds

| Decision | Set by | Lands in |
|---|---|---|
| `LLM_TIMEOUT_S`, retry budget | S6 | env / `llm/router.py` defaults |
| Per-provider structured mode (hint vs strict) | F3, S1, S7 | `llm/router.py` |
| Keywords sent to the provider vs checked locally only | S1 | `pipeline/hypothesize.py` schema |
| Case-id aliases in the prompt (yes/no) | S3 | `pipeline/hypothesize.py` |
| Parser tolerance (fences, list content, `reasoning` field) | S5 | `llm/router.py` + fake-transport tests |
| Classification of 200-with-`error` / empty content | S5 | `llm/router.py` (treated as failover-eligible) |
| Emergency primary swap | D1, D3 | runbook line in README |
| `hindsight-client` pin | F5 | Rama, `pyproject.toml` (message only; not edited here) |
| Rehearsal pacing / credits | E3, X2 | demo runbook |

## 6. Results

Runs: `mk0/results/mk0-20260928T060033Z.json` (full, 12 PASS / 2 FAIL) and
`mk0-20260928T061029Z.json` (S8 follow-up). Keyless run `055521Z` is superseded.

### Root cause of the two FAILs (S2, S6)

**Both models reason until they hit `max_tokens`.** Every invalid output had `finish=length`,
3000 completion tokens and empty or truncated content. **Every call that finished (`stop`) was
schema-valid.** It's a token-budget problem, not a schema-compliance problem. Rama's M0 missed it
because a toy prompt needs little reasoning.

| Route | Default | With reasoning control |
|---|---|---|
| Primary (Space Bunny, hint mode) | 2/5 valid, p50 44 s, max 47 s | `reasoning: {effort: low}`: 3/3 valid, 8–11.5 s, ~550 tokens |
| Fallback (Baseten DeepSeek, strict) | 0/5 valid, p50 16 s | `chat_template_kwargs: {thinking: false}`: 3/3 valid, 2–4 s, ~550 tokens, 0 invented ids |
| Fallback, `reasoning_effort: low` | ignored: 0/3 valid, still 3000 tokens | — |
| Fallback, `reasoning: {effort: low}` | 2/3 valid, 15 s | — |

### Everything else

| Probe | Observed |
|---|---|
| E3 | OpenRouter key: free tier, `limit=100`, usage 0 |
| D1/D2/D3 | Both models listed; backup `deepseek/deepseek-v4.1-flash` valid on the OpenRouter key (19 s with default reasoning) |
| S1 | Primary strict → **404** "no endpoints can handle the requested parameters" (in 0.1 s); hint → 200 valid. Fallback strict accepted (200). Extra keywords (`minItems`, `maxItems`, `minLength`) did not cause a rejection on the fallback |
| S3 | 22 citations across 17 calls, **0 invented or mangled** (16-hex and `seed-00N` both safe) |
| S4 | Abstained prompt: 0 citations on both routes |
| S5 | `reasoning_field` on nearly every call; `finish_length` 11; `empty_content` 10; primary pads truncated content with whitespace lines; fallback puts reasoning in `reasoning_content` with `content: null` |
| S7 | Strict-first on primary costs only ~0.1 s (fast 404), but is pointless: always hint |
| X1 | Primary: bad model → 400, bad key → 401. Fallback: bad model → **404**, bad key → **403**. All classified config/auth, none fail over |
| X2 | 5 back-to-back calls: no 429 |
| X3 | Unreachable primary → fallback served, valid, `fallback_used=true` |

### Decisions for MK4 / MK5 (§5 filled in)

| Decision | Value |
|---|---|
| Per-provider request options | primary: hint mode (no `require_parameters`) + `reasoning: {effort: low}`; fallback: strict + `chat_template_kwargs: {thinking: false}`. Provider options live in `llm/` only |
| `max_tokens` | 1500: about 3× the observed ~550; a `finish=length` is treated as invalid output, not parsed |
| `LLM_TIMEOUT_S` | superseded by the provider swap below: primary 10 s, fallback 17 s |
| Keywords sent to provider | full schema, incl. `minItems` etc. (accepted); local validation still the gate |
| Case-id aliases | not needed (S3) |
| Parser | read `content` only (ignore `reasoning*`); `content: null` or whitespace-only → invalid; `finish_reason=length` → invalid; strip ```json fences |
| Error map | 400/404 → config, 401/403 → auth (never fail over); timeout/429/5xx/200-with-error → failover |
| Emergency primary swap | `LLM_PRIMARY_MODEL=deepseek/deepseek-v4.1-flash` on the same key (D3); send it the `effort: low` option too |
| Demo budget | 5 calls in a burst were fine; free-tier cap still applies to rehearsals |

### Provider order swapped (decided 2026-09-28)

Primary is now **Baseten DeepSeek V4.1 Flash** (strict, thinking off); fallback is **OpenRouter
Space Bunny Alpha** (hint, reasoning effort low). This is a config-only change in `.env.live` /
`.env.local`. `.env.example` stays byte-identical to `rama-m0` until MK9, where the swap is
applied to the shared template together with Rama.

### Final confirmation run — `mk0-20260928T061541Z.json` (final config): 14 PASS, 0 FAIL

| Probe | Observed |
|---|---|
| S1 | primary strict 200 valid (extra keywords accepted); fallback strict 404, hint 200 valid |
| S2 | **5/5 valid on both routes** |
| S3 | 34 citations across 14 calls, 0 invented |
| S4 | 0 citations when abstained, both routes |
| S5 | only `reasoning_field` (fallback); no truncation, no empty content |
| S6 | primary p50 **3.2 s**, max 6.1 s · fallback p50 9.8 s, max 10.8 s · worst failover ≈ 16.9 s |
| X1 | primary (Baseten) 404 / 403 · fallback (OpenRouter) 400 / 401 · no failover |
| X2, X3 | no 429 · unreachable primary → fallback served, `fallback_used=true` |

**Final timeouts:** primary 10 s, fallback 17 s (1.5× max observed). Typical interaction ≈ 3 s;
worst case through failover ≈ 27 s at the timeout ceiling, ≈ 17 s as observed.

**MK0 status: complete.** Re-run `python3 mk0/probe.py --only D1,D2,S2,X3` on demo morning.
