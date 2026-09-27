# M0 Runtime Verification (throwaway scaffolding)

This directory exists **only** to verify runtime assumptions before any pipeline code is written.
It is not the application. Nothing here is imported by `src/debugagent/` (which does not exist yet).

## What it verifies

| ID | Capability |
|----|-----------|
| RT-1 | Primary (OpenRouter Space Bunny Alpha) availability |
| RT-2 | Primary structured output (`response_format` json_schema) + local schema validation |
| RT-3 | Primary error classification (unknown model → `MODEL_NOT_FOUND`, bad key → `AUTH`) |
| RT-4 | Fallback (Baseten DeepSeek V4.1 Flash) availability |
| RT-5 | Fallback structured output + local schema validation |
| RT-6 | Primary → fallback routing when the primary base URL is unreachable |
| RT-7 | Structured-call latency calibration (informs `LLM_TIMEOUT_S`) |
| RT-8 | Auth error must **not** trigger failover |
| H-1 | Hindsight service connectivity |
| H-2 | Hindsight retain (write probe cases) |
| H-3 | Hindsight recall (query + `types` + `max_tokens`) |
| H-4A…H-4E | A–E recall/abstention probes (relevant / different-root-cause / irrelevant / different-environment / insufficient-evidence) |

## Requirements

- Python 3.10+ (stdlib only for the LLM probes — no install needed).
- `hindsight-client` installed **only** for the H-1…H-4x probes, plus a reachable Hindsight instance.
- Environment variables set (see `../.env.example`). Real values go in `.env`, which is git-ignored.

## How to run

```powershell
Copy-Item .env.example .env      # then fill in real values locally
python -m m0.run_all
```

Exit codes: `0` all passed · `1` at least one failure · `2` at least one blocked (missing key/dependency).

Results are written to `m0/results/` as JSON + Markdown with per-test: capability, tool,
pass/fail/blocked, observation, latency, error class, affected design decision, UTC timestamp,
and how to reproduce. No secret values are ever written or printed — the startup banner prints
only key presence and length.

## Provenance checklist (confirm during RT-1/RT-2, do not assume)

- Exact request-body shape for OpenRouter provider routing `require_parameters: true`
  (concept verified in provider docs; body shape to confirm live).
- Whether `space-bunny-alpha`'s current serving endpoint honors structured outputs.
- Space Bunny Alpha current availability, price, and max output tokens.
- Baseten latency/quota for `deepseek-ai/DeepSeek-V4.1-Flash`; `reasoning_effort` value to use.
- Whether `hindsight-client` is the intended client and its exact import name/version.

## Harness self-check (no keys required)

Validator and error classification can be exercised offline:

```powershell
python -c "from m0.schema_validate import validate; from m0.llm_probe import HYPOTHESIS_SCHEMA, classify_status, FAILOVER_ELIGIBLE; print(validate({'hypothesis':'x','supporting_case_ids':[],'relevance_state':'abstained','refutation_conditions':['y']}, HYPOTHESIS_SCHEMA)); print([(c, classify_status(c), classify_status(c) in FAILOVER_ELIGIBLE) for c in (400,401,402,403,404,429,500,502,529)])"
```

Expected: `[]` for the valid instance, and only 429/5xx/529 marked failover-eligible.
