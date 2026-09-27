# LLM Provider Architecture

**Status:** Design only — no code written, no dependencies installed, no keys provisioned.
**Date:** 2026-09-27.
**Team decision recorded:** primary LLM = OpenRouter Space Bunny Alpha; fallback LLM = DeepSeek
V4.1 Flash via Baseten; implementation language = Python. (See `docs/decision-log.md`.)
**Reference:** `docs/domain-neutral-system-design.md`, `docs/architecture-review.md`,
`docs/hindsight-capability-verification.md`.

## 1. Primary / fallback decision

| Role | Provider | Model / route | Why (team rationale, not a benchmark claim) |
|------|----------|---------------|----------------------------------------------|
| Primary | OpenRouter | `stealth/space-bunny-alpha` ("Space Bunny Alpha") | Free tier at time of review; 1M context; reported tool calling + structured output |
| Fallback | Baseten Model APIs | `deepseek-ai/DeepSeek-V4.1-Flash` | OpenAI-compatible endpoint; built-in reasoning + tool calling; independent provider/stack from OpenRouter |

Both endpoints are OpenAI-compatible (OpenRouter) / OpenAI-compatible (Baseten), so one
client protocol serves both providers and only base URL + key + model ID differ.

## 2. Existence and stability check (web-verified 2026-09-27, re-verify at build time)

- **Space Bunny Alpha — EXISTS but fragile.** Listed at `openrouter.ai/stealth/space-bunny-alpha`
  (anonymous stealth preview, free, 1M context). Third-party reports (Sept 23–26, 2026) confirm
  tool calling, structured output, and mandatory reasoning with effort levels — but also report
  OpenRouter temporarily took it offline for a provider-side issue days after launch. It can
  vanish or change without notice, as stealth previews do. **This fragility is itself a reason
  the fallback path is mandatory, not optional.**
- **DeepSeek V4.1 Flash on Baseten — EXISTS.** Baseten changelog/blog (Sept 10–11, 2026):
  model ID `deepseek-ai/DeepSeek-V4.1-Flash`, OpenAI-compatible endpoint
  `https://inference.baseten.co/v1/chat/completions` with `BASETEN_API_KEY`, `reasoning_effort`
  parameter, tool calling built in. DeepSeek's own news confirms the V4.1-Flash release.
- **Not yet verified (must confirm during the spike):** exact structured-output mode support
  (JSON schema / strict mode) on each route; function-calling schema compatibility; timeout,
  rate-limit, and error-code behavior; current availability and pricing of Space Bunny Alpha;
  Baseten key provisioning and quota for the team.

## 3. Adapter design (proposed interface, not implemented)

The investigation agent talks only to `LLMAdapter`. All provider specifics live behind it.
Switching providers changes configuration + adapter selection, never memory, recall, evidence,
verification, or workflow code.

```
Investigation Agent
      │  uses only LLMAdapter
      ▼
┌──────────────┐
│  LLMAdapter  │  (abstract: complete(), complete_structured(), capabilities())
└──────┬───────┘
       ├── OpenRouterAdapter   (base URL, key, model from config)
       ├── BasetenAdapter      (base URL, key, model from config)
       └── <FutureProvider>Adapter (added later without touching the agent)
```

Proposed `LLMAdapter` surface (naming to be frozen at implementation; semantics fixed here):

- `complete(prompt, *, budget) -> LLMResult` — plain completion for hypothesis text and
  explanations. `LLMResult = {text, provider, model, latency_ms, usage, fallback_used}`.
- `complete_structured(prompt, *, schema) -> StructuredResult` — hypothesis objects, match
  reports, case drafts conforming to an application schema. Must either return
  schema-validated output or raise `StructuredOutputUnsupported` — never silently return
  unvalidated text where a schema was required.
- `capabilities() -> ProviderCaps` — `{tool_calling: bool, structured_output: bool|mode,
  max_context: int|unknown, reasoning_control: bool}` as *verified at runtime*, used by the
  orchestrator to degrade gracefully (e.g. no tool calls → prompt-based alternatives).
- Errors are normalized: `LLMTimeout`, `LLMRateLimited`, `LLMAuthError`, `LLMUnavailable`,
  `StructuredOutputUnsupported`, each carrying provider + model + retryability. Provider HTTP
  codes never leak past the adapter.

Fallback policy (explicit, observable): on `LLMTimeout` / `LLMUnavailable` / `LLMRateLimited`
(no retry budget left) from primary, the router calls the fallback adapter with the same
request, marks `fallback_used=true`, and logs provider, model, error class, and latency of both
attempts. `LLMAuthError` never triggers fallback (it triggers a configuration error instead —
retrying with another key would hide a setup bug). **If the fallback lacks a capability the
request required (e.g. strict structured output), the router raises instead of returning a
weaker result** — degraded output must never silently pose as equivalent.

Provider differences accounted for in the design: tool/function-calling dialects (normalize to
one internal call format; unsupported → prompt-based fallback path); structured-output modes
(strict schema vs guided JSON vs none → capability flag + validation gate); error taxonomies
(mapped to the five normalized errors); timeouts (per-request budget, shorter for primary so
fallback still fits demo latency); rate limits (retry with backoff on primary within budget,
then fail over; every retry logged).

## 4. Configuration model (env/config only, never hardcoded)

| Variable | Meaning | Example shape (no real values) |
|----------|---------|-------------------------------|
| `LLM_PRIMARY_PROVIDER` | `openrouter` | literal |
| `LLM_PRIMARY_MODEL` | OpenRouter model route | `stealth/space-bunny-alpha` |
| `LLM_PRIMARY_BASE_URL` | OpenRouter endpoint | `https://openrouter.ai/api/v1` |
| `LLM_PRIMARY_API_KEY` | secret, env only | set in environment, never committed |
| `LLM_FALLBACK_PROVIDER` | `baseten` | literal |
| `LLM_FALLBACK_MODEL` | Baseten model ID | `deepseek-ai/DeepSeek-V4.1-Flash` |
| `LLM_FALLBACK_BASE_URL` | Baseten endpoint | `https://inference.baseten.co/v1` |
| `LLM_FALLBACK_API_KEY` | secret, env only | set in environment, never committed |
| `LLM_TIMEOUT_S` | per-attempt timeout | e.g. `30` |
| `LLM_MAX_RETRIES_PRIMARY` | retries before failover | e.g. `1` |
| `LLM_REQUIRE_STRUCTURED` | fail rather than degrade | `true` for proposal paths |

Rules: keys exist only in the environment (documented in README setup at implementation time;
`.env` already git-ignored); model IDs, URLs, timeouts, and retry budgets are configuration,
read once at startup and logged (redacted) so the active provider/model is always auditable;
adding a third provider means a new adapter class + config keys, zero agent changes.

## 5. Structured-output preservation

The investigation loop's hypothesis object, match report, and case draft schemas are defined by
the application (see `docs/architecture-review.md` Section 6), not by either provider. The
adapter validates every structured response against the requested schema before returning it;
validation failure is an error (retry once → fail over → raise), never a silent downgrade.
`LLM_REQUIRE_STRUCTURED=true` on proposal paths enforces the "raise, don't degrade" rule.

## 6. Verification status

Documentation-level verification is complete — see `docs/provider-verification.md` for the full
matrix (17 verified facts, 1 partial, 7 unverified) and the 8 runtime-test items (RT-1…RT-8) that
must pass before pipeline code is written. Two verification results change this document's design:

1. **Structured-output enforcement is not trustworthy at the provider level.** OpenRouter states
   support is *per endpoint* (routing to a capable endpoint requires `require_parameters: true`
   plus `response_format` in required parameters) and that exact compliance is *not guaranteed*
   on every endpoint. Therefore the adapter validates every structured response locally and
   fails closed — this is the concrete mechanism behind "no silent downgrade".
2. **Fallback capability parity is documented.** Baseten states all Model APIs support tool
   calling, structured outputs, and JSON mode, so the fallback satisfies the same contract; the
   capability flag still exists because parity is documented, not guaranteed per route.

Remaining unknown that matters most: endpoint-level structured-output support and current
stability of the primary route (a stealth preview that was reportedly pulled offline shortly after
launch). Both are runtime-test items, not design unknowns.
