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

## Classification summary

- Verified from official provider docs: 17 facts (V1–V17).
- Partially verified: 1 (V9 — context/free from listings; max output unknown).
- Not verified: 7 (U1–U7), of which 5 are runtime-only and 2 are informational.
- No capability claim in `docs/llm-provider-architecture.md` or the Phase 1 plan depends on an
  unverified item without a stated fallback or runtime gate.
