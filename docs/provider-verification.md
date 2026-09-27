# LLM Provider Verification

**Status:** Documentation-level verification completed 2026-09-27. **No API calls were made** (no
keys provisioned, no dependencies installed) — all runtime behavior remains a runtime-test item.
**Scope:** only the two providers the team selected. Method and classification follow
`docs/hindsight-capability-verification.md` (VERIFIED / PARTIALLY VERIFIED / NOT VERIFIED /
NOT RELEVANT). "Not verified" means evidence was insufficient, not that support is absent.
**Sources (official only):**
- `https://openrouter.ai/stealth/space-bunny-alpha` (model page, canonical `…/llms.txt`)
- `https://openrouter.ai/docs/features/structured-outputs`
- `https://www.baseten.co/resources/changelog/deepseek-v41-flash-available-on-baseten/`
- `https://docs.baseten.co/inference/model-apis/overview`

## Verified Facts

| # | Fact | Official evidence | Status | Design impact |
|---|------|-------------------|--------|---------------|
| V1 | OpenRouter chat endpoint is `POST https://openrouter.ai/api/v1/chat/completions`; auth is `Authorization: Bearer $OPENROUTER_API_KEY` | OpenRouter model page | VERIFIED | Config keys `LLM_PRIMARY_BASE_URL` / `LLM_PRIMARY_API_KEY` |
| V2 | Model route is `stealth/space-bunny-alpha`; request accepts `messages`, `stream`, `max_tokens`, `response_format`, `reasoning`, `reasoning_effort`, `tools`, `tool_choice`, `temperature`, `top_p` | OpenRouter model page | VERIFIED | Adapter can send structured output and tool schemas; reasoning-effort exposed as optional config |
| V3 | Response shape: `choices[0].message.content` + `usage{prompt_tokens, completion_tokens, total_tokens, cost}` | OpenRouter model page | VERIFIED | `LLMResult.usage` can log cost/tokens — useful demo telemetry |
| V4 | OpenRouter errors: 400 malformed/unsupported param, 401 bad key, 402 insufficient credits, 403 spend limit/key disabled, 404 unknown model/no provider, 429 rate limited (retry with backoff), 502 upstream failure (not billed) | OpenRouter model page | VERIFIED | Direct mapping to normalized error taxonomy (Section 2 of `llm-provider-architecture.md`) |
| V5 | OpenRouter structured outputs use `response_format: {type: "json_schema", json_schema: {name, strict, schema}}` | OpenRouter structured-outputs docs | VERIFIED | `complete_structured()` request shape |
| V6 | Structured-output support is **per endpoint, not per model**; to guarantee routing to capable endpoints set `require_parameters: true` in provider preferences and include `response_format` in required parameters | OpenRouter structured-outputs docs | VERIFIED | Adapter must send `require_parameters` + `response_format`; this is the enforcement mechanism |
| V7 | Exact schema compliance is **not guaranteed on every endpoint** — some providers guarantee it, others translate the schema or treat it as a strong hint; strict mode may restrict JSON Schema features | OpenRouter structured-outputs docs | VERIFIED | **Local schema validation is mandatory; provider-side strict mode is not trusted** (this is the "no silent downgrade" mechanism) |
| V8 | Structured-output failures surface as errors (model lacks support, or invalid schema) | OpenRouter structured-outputs docs | VERIFIED | Validation/auth errors are non-retryable → fail fast, then fail over |
| V9 | Space Bunny Alpha is a real, currently listed OpenRouter route (anonymous stealth preview) with a 1M-token context window and free pricing as shown on OpenRouter's model/compare pages | OpenRouter model page + OpenRouter models listings | PARTIALLY VERIFIED (context/free read from listing snippets; max-output limit not read) | Long-context, zero-cost primary is viable; max output unknown → keep response budgets conservative |
| V10 | Baseten OpenAI-compatible endpoint: base URL `https://inference.baseten.co/v1`, chat at `/v1/chat/completions`, key from the Baseten console (`BASETEN_API_KEY`) | Baseten changelog + Model APIs docs | VERIFIED | Config keys `LLM_FALLBACK_BASE_URL` / `LLM_FALLBACK_API_KEY` |
| V11 | Fallback model slug is `deepseek-ai/DeepSeek-V4.1-Flash`; `reasoning_effort` is accepted | Baseten changelog | VERIFIED | `LLM_FALLBACK_MODEL` value |
| V12 | All Baseten Model APIs support tool calling, structured outputs, and JSON mode | Baseten Model APIs docs | VERIFIED | Fallback satisfies the same structured-output contract as primary |
| V13 | DeepSeek V4.1 Flash on Model APIs: reasoning enabled by default, vision supported, sampling `temperature`/`top_p`/`stop`, context 1048k, **max output 32k** | Baseten Model APIs docs (feature + supported-models tables) | VERIFIED | 32k output is ample for our schemas; reasoning-on-by-default must be accounted for in latency budget |
| V14 | Baseten errors: 400 invalid request, 401 bad key, 402 payment required, 404 model not found, 429 rate limit, 500 internal, 529 overloaded (retry with backoff) | Baseten Model APIs docs | VERIFIED | Maps to the same normalized taxonomy; 529 is a failover trigger |
| V15 | Baseten context/output limits reflect **live serving configuration** and can change (may be extended after validation); `/v1/models` exposes the current catalog, pricing, context, and features | Baseten Model APIs docs (note + endpoint list) | VERIFIED | Treat limits as runtime-discoverable, not hardcoded; catalog endpoint is the source of truth at run time |
| V16 | Baseten supports `x-session-affinity` for cached-input reuse and recommends ≲ ~60 requests/min per session | Baseten Model APIs docs | VERIFIED | Demo load is far below this; no rate-limit work needed for Phase 1 |
| V17 | Baseten also exposes an Anthropic-compatible `/v1/messages` endpoint, in beta | Baseten Model APIs docs | NOT RELEVANT | Not used; noted to prevent accidental coupling |

## Not Verified (open)

| # | Item | Why it matters | Status |
|---|------|----------------|--------|
| U1 | Space Bunny Alpha **endpoint-level** structured-output support for the current serving provider | Determines whether primary can serve `complete_structured` natively or needs the validate-and-repair path | NOT VERIFIED |
| U2 | Space Bunny Alpha current availability/stability and max-output limit | The model is a stealth preview that was reportedly pulled offline days after launch | NOT VERIFIED |
| U3 | Baseten pricing/quota for `deepseek-ai/DeepSeek-V4.1-Flash` and key provisioning for the team | Budget planning for Phase 1 | NOT VERIFIED |
| U4 | OpenRouter optional attribution headers (`HTTP-Referer` / `X-Title`) | Cosmetic/accounting only; not required by the model page | NOT VERIFIED (low impact) |
| U5 | Observed latency, effective timeouts, and real rate-limit behavior on both routes | Sets `LLM_TIMEOUT_S` and retry budgets for demo latency | NOT VERIFIED (runtime) |
| U6 | Practical schema conformance on both routes (does output actually validate?) | Drives the local-validation retry budget | NOT VERIFIED (runtime) |
| U7 | Data-retention/privacy posture of both routes | Informational; demo uses synthetic data regardless | NOT VERIFIED (low impact) |

## Runtime-Test Items (executed during Milestone M0, before any pipeline code)

Each item is a pass/fail gate; results are logged in `docs/decision-log.md` and, if any fail, the
affected design knob is adjusted **in configuration**, not in workflow code.

| ID | Runtime test | Pass criterion | Failure response |
|----|--------------|----------------|------------------|
| RT-1 | Primary availability: one minimal chat call to `stealth/space-bunny-alpha` | 200 with non-empty content | If unavailable: keep primary configured, mark demo runbook to start on fallback, log loudly; consider model swap via config only |
| RT-2 | Primary structured output: request the hypothesis schema with `response_format` + `require_parameters` | Response parses **and** validates against the schema | Increase validation retries; if still failing, route proposal paths to fallback and log |
| RT-3 | Primary error surface: deliberately send an invalid model ID and a bad key | Observe 404/401 classification as documented (V4) | Adjust error mapping in the adapter only |
| RT-4 | Fallback availability: one minimal chat call to `deepseek-ai/DeepSeek-V4.1-Flash` | 200 with non-empty content | Escalate: fallback is the safety net; do not proceed to M1 without it |
| RT-5 | Fallback structured output: same schema, `response_format` | Response parses and validates | If unsupported, raise `StructuredOutputUnsupported` (never degrade) and mark proposal paths primary-only |
| RT-6 | Failover drill: point the router at an unreachable base URL (or force timeout) and issue a structured request | Fallback serves the request; log shows `fallback_used=true` with both attempts' provider/model/latency | Fix router before proceeding — failover is a demo-day requirement |
| RT-7 | Latency calibration: measure p50/p95 for a representative structured call on both routes | Primary timeout chosen so primary+fallback fits the demo budget | Lower `LLM_TIMEOUT_S` / retries in config |
| RT-8 | Auth-error drill: bad API key on the primary | `LLMAuthError` raised, **no** failover attempted | Fix error classification before proceeding |

**Runtime-test dependencies (cannot be done by the agent):** provisioned OpenRouter key,
provisioned Baseten key, installed HTTP client, and network egress. These are the only items
blocking the start of M0.

## Runtime Results — run 2026-09-27T18:08:42Z (`m0/results/m0-results-20260927T180842Z.json`)

**Outcome: 0 PASS · 0 FAIL · 16 BLOCKED.** No test executed against a live provider or a live
Hindsight instance, because the required credentials and service are absent. Nothing is inferred
from these rows; no mock or assumed result is recorded. Re-run with `python -m m0.run_all` once the
blockers below are cleared. Runner exit code was `2` (blocked), as designed.

| Test | Capability | Result | Blocking reason |
|------|-----------|--------|-----------------|
| RT-1 | Primary availability | NOT_RUN_BLOCKED | `LLM_PRIMARY_API_KEY`, `LLM_PRIMARY_BASE_URL`, `LLM_PRIMARY_MODEL` unset |
| RT-2 | Primary structured output + local validation | NOT_RUN_BLOCKED | same as RT-1 |
| RT-3 | Primary error classification | NOT_RUN_BLOCKED | same as RT-1 |
| RT-4 | Fallback availability | NOT_RUN_BLOCKED | `LLM_FALLBACK_API_KEY`, `LLM_FALLBACK_BASE_URL`, `LLM_FALLBACK_MODEL` unset |
| RT-5 | Fallback structured output + local validation | NOT_RUN_BLOCKED | same as RT-4 |
| RT-6 | Primary → fallback routing drill | NOT_RUN_BLOCKED | fallback unrunnable (same as RT-4) |
| RT-7 | Latency calibration | NOT_RUN_BLOCKED | no provider configured |
| RT-8 | Auth error must not fail over | NOT_RUN_BLOCKED | primary unrunnable (same as RT-1) |
| H-1 | Hindsight connectivity | NOT_RUN_BLOCKED | no instance (`HINDSIGHT_URL` unset) **and Docker not installed on the dev machine** |
| H-2 | Hindsight retain | NOT_RUN_BLOCKED | requires H-1 + `hindsight-client` |
| H-3 | Hindsight recall | NOT_RUN_BLOCKED | requires H-2 |
| H-4A…H-4E | A–E recall/abstention probes | NOT_RUN_BLOCKED | requires H-2/H-3 and a seeded bank |

### Environment inspection (same run, `D:\hackwithhyderabad-3`)

| Item | Finding |
|------|---------|
| Python | 3.10.11 (Microsoft Store build), pip 26.0.1; no `py` launcher |
| Virtual environments | none present in repo or `D:\` |
| Docker | **not installed** — CLI absent, daemon not responding |
| API-key env vars | none of `OPENROUTER_API_KEY`, `BASETEN_API_KEY`, `HINDSIGHT_API_KEY`, `HINDSIGHT_API_LLM_API_KEY`, `HINDSIGHT_URL` present |
| `.env` files | none present |
| Dependency-file conflicts | none — no `pyproject.toml` / `requirements*.txt` / `Pipfile` / `uv.lock` / `package.json` exists, so the proposed `pyproject.toml` has nothing to conflict with |
| Packages installed by this stage | **none** (scaffolding is stdlib-only; nothing installed) |

### Harness self-check (offline, no keys — validates tooling, not providers)

Executed on branch `rama-m0`, Python 3.10.11; `python -m compileall m0` clean; self-check reported
`SELFCHECK_FAILURES = 0`.

| Check | Result |
|-------|--------|
| Valid hypothesis object → validator errors | `[]` (no false rejection) |
| Invalid `relevance_state` enum | caught |
| Missing required properties | caught (3 reported) |
| Disallowed additional property | caught |
| Wrong type (`string` where `array` expected) | caught |
| Empty string violating `minLength` | caught |
| Status → class mapping | 400 `BAD_REQUEST`, 401/403 `AUTH`, 402 `BILLING`, 404 `MODEL_NOT_FOUND`, 429 `RATE_LIMITED`, 500/502/529 `UNAVAILABLE` |
| Failover eligibility | only 429 / 5xx / 529 eligible — **AUTH excluded** (401 and 403 asserted), satisfying the team rule before any live test |
| Secret redaction | `<unset>` / `<set:redacted>` / `<set:redacted:len=24>` — no value ever printed |
| Config load with no environment | all three subsystems report `configured=False` and tests block rather than run |
| A–E probe definitions | all five present with expectations |

### What these results do and do not change

- **No decision changed**, so `docs/decision-log.md` is intentionally untouched by this stage.
- One environment constraint surfaced that affects a *proposal*, not a decision: the two-phase plan's
  default "Hindsight hosting = local Docker" is **not executable on this machine** (Docker absent).
  Options for the team: install Docker, use the pip/embedded server, or use Hindsight Cloud. This is a
  decision for the team, recorded here and in `docs/change-log.md`, not decided by this review.
- U1–U7 remain unverified; RT-1…RT-8 and H-1…H-4E remain the gates for Milestone M0 completion.

## Classification summary

- Verified from official provider docs: 17 facts (V1–V17).
- Partially verified: 1 (V9 — context/free from listings; max output unknown).
- Not verified: 7 (U1–U7), of which 5 are runtime-only and 2 are informational.
- No capability claim in `docs/llm-provider-architecture.md` or the Phase 1 plan depends on an
  unverified item without a stated fallback or runtime gate.
