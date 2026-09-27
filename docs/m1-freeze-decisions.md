# Phase 0 → M1 Freeze Checklist (Decisions C1–C5)

**Status:** Proposals only. **None of these is decided.** Each requires explicit joint agreement by
Rama and Mukul in the 30-minute freeze session.
**Date:** 2026-09-27. **Reference:** `docs/m1-contract.md` §5, `docs/phase1-team-interface.md` §5.

---

## C1 — Ownership of `llm/` (LLM adapter + router)

| | |
|---|---|
| **Decision** | Who owns `debugagent/llm/` (adapter ABC, router, provider adapters, local validation) |
| **Current proposal** | **Mukul** — the split lists "LLM adapter/router, primary → fallback behavior" under Mukul; he is also the only consumer (hypothesis generation) |
| **Why it matters** | It is shared infrastructure. If Rama also touches it, the failover/auth rules can be changed from two places and silently diverge from the M0-verified behaviour |
| **Recommended default** | Mukul owns `llm/` exclusively; Rama consumes it only through the `LLMAdapter` Protocol and never imports provider modules |
| **If the alternative is chosen** | If Rama owns `llm/`, Mukul must code hypothesis generation against the `LLMAdapter` Protocol stub **before** Rama implements it, and the M0 behaviours (failover on 429/5xx/timeout only; auth never fails over; local validation fail-closed) become a contract test owned by Rama. Slower start, extra interface-contract cost |

## C2 — Initial `min_final_score`

| | |
|---|---|
| **Decision** | The abstention floor used by `classify_candidates` |
| **Current proposal** | `0.05`, with 0.05–0.6 = `weak-reference`, ≥0.6 = `relevant` candidate |
| **Why it matters** | Directly controls when the demo abstains vs recalls. Too high → the "with memory" act looks empty; too low → irrelevant cases leak into the proposal |
| **Recommended default** | `0.05`, stored in config (not code), and **re-calibrated** from recorded scores after the first seed set lands (M0 bands: relevant 0.97–1.09, vague 0.35–0.44, unrelated 0.002–0.006) |
| **If the alternative is chosen** | A stricter floor (e.g. 0.6) makes abstention conservative and safer but risks abstaining on genuinely useful partial matches; a looser floor (e.g. 0.001) surfaces noise and undermines the irrelevance demonstration. Either way it must remain a config value, never a constant |

## C3 — Initial seed domain family

| | |
|---|---|
| **Decision** | Which single engineering domain the seed cases and the demo cover |
| **Current proposal** | Service/API runtime failures (connection resets, oversized payloads, upstream errors) — the M0 probe family |
| **Why it matters** | Rama authors seeds; Mukul's normalizer and CLI must speak the same vocabulary. Two vocabularies = silent recall misses on demo day |
| **Recommended default** | Reuse the M0 probe family — it is already proven retrievable, distinguishable, and abstention-testable against a real bank |
| **If the alternative is chosen** | Another domain is permissible (e.g. hardware timing, batch/data pipelines) but costs: new taxonomy, new seed authoring, a fresh abstention calibration, and loss of the M0 evidence. It also risks becoming "a second domain", which is out of Phase 1 scope |

## C4 — CLI command names and section labels

| | |
|---|---|
| **Decision** | Command verbs and the exact rendering of the four trust sections |
| **Current proposal** | Commands `debug`, `verify`, `resolve`, `inspect`; sections labelled **MEMORY**, **EVIDENCE**, **PROPOSAL**, **DECISION** |
| **Why it matters** | The four-section separation *is* the demo's core trust surface. Labels are the contract between the architecture and what the judge sees |
| **Recommended default** | Keep the four labels exactly as above (already referenced by the demo script and DoD); command names are free to change since no integration depends on them |
| **If the alternative is chosen** | Any relabelling must be applied to `docs/phase1-demo-scenario.md` and the DoD in the same commit, or the docs and the demo drift apart |

## C5 — `observed_evidence` representation

| | |
|---|---|
| **Decision** | Whether the memory field stores URI/reference only, or short text plus URI |
| **Current proposal** | References/URIs only (no raw report or log blobs) |
| **Why it matters** | Affects seed size, render width in the CLI, and the "no secrets on screen" rule. Blobs would also bloat the bank and slow recall |
| **Recommended default** | URI/reference only; a short human label is acceptable **if** it carries no sensitive content (e.g. `prime-time-report://2026-09-27/run-114`) |
| **If the alternative is chosen** | Storing text snippets is convenient for the demo but introduces a secrets/PII surface and larger `max_tokens` consumption; it must then be explicitly sanitised and length-capped |

---

## Additional item raised by this review (not previously numbered)

### C6 — `min_scores` dependency (technical, low-controversy)

`min_scores` is documented by both providers but was **not exercised in M0**. The contract now
requires abstention to be computed in `classify_candidates` from returned scores, with `min_scores`
treated as unverified. **Recommendation: no decision needed — adopt the contract as written.** Raise it
only if someone wants provider-side filtering as well.

### C7 — Memory Defense verification (technical, low-controversy)

M0 confirmed the configuration call returns without error but did **not** verify that redaction is
active. **Recommendation:** do not claim it protects secrets; rely on the "no secrets in seed data"
rule, and verify redaction only if time allows after the MVP.
