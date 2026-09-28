# Phase 1 Demo Scenario (single domain)

**Status:** Frozen demo script for the 29 Sept MVP. One domain only.
**Date:** 2026-09-27.
**Domain choice:** service/API runtime failures (connection resets, oversized payloads) — the same
family as the M0 probe cases, so the seeds are already validated as retrievable and distinguishable.
This is a single domain for the MVP, not a second domain; nothing here implies multi-domain support.

## Cast

- **Engineer** — the persona using the CLI.
- **Bank** — pre-seeded historical cases (the M0 probe family, cleaned up).

## Act 1 — Without useful memory (the "before")

1. Engineer starts a debug session for a **new** issue with no matching history:
   *"payments-service returns 502 only for payloads above 2 MB; smaller payloads are fine."*
2. Agent recalls candidates; the abstention/relevance logic marks them `partial` or `irrelevant`
   (the historical cases are about `envoy` and `nginx` request-size limits, not upstream 502s).
3. **PROPOSAL** shows generic hypotheses with `relevance_state: generic` and **empty**
   `supporting_case_ids` — visibly not memory-informed.
4. Engineer verifies against **EVIDENCE** (logs pasted now), decides, and resolves.
5. The case — *including the failed approach tried* — is retained.

## Act 2 — With useful memory (the "after")

6. Engineer starts a second, similar issue:
   *"media-uploader resets connections on uploads over 2 MB behind nginx."*
7. Agent recalls the historical nginx/`client_max_body_size` case:
   - **MEMORY** section shows the case id, its **original environment**
     (`service=media-uploader`, `proxy=nginx-1.25`) and the evidence reference.
   - **Comparison** states the match (same request-size-limit class) **and the differences**
     (different service, different proxy, different runtime).
8. Agent also surfaces the Act 1 case as a *different* root cause, labelled `conditional`, with the
   condition that would have to hold for it to apply — never merged into the first case.
9. **PROPOSAL** ranks "raise the reverse-proxy body limit, then re-test uploads" first, cites the
   recalled case id, warns that raising the client timeout previously had no effect, and lists the
   refutation condition.
10. **EVIDENCE** shows only current-case facts. Engineer confirms relevance explicitly.
11. **DECISION** records accept/modify/reject + engineer note.
12. Outcome retained; the engineer sees the retention acknowledgement (fact id) and the failed
    approach preserved.

## What the judge should see in 60–90 seconds

1. First issue: generic proposal, **no citations** — memory absent or unhelpful.
2. Retention is visible (a real Hindsight write, fact id shown).
3. Second issue: relevant historical case recalled **with its original conditions**.
4. Similarities *and* differences are stated; a different-root-cause case is kept separate.
5. Proposal is reprioritised and cites memory; a previously failed approach is flagged.
6. Engineer verification is an explicit, logged step; the CLI never presents a proposal as verified.
7. Abstention is demonstrated as a first-class behaviour (a third, unrelated query, if time allows).

## Demo data

6–10 seed cases in the same family as the M0 probes (proxy limits, upstream 502, index-related
latency), each with at least one failed approach, a confirmed root cause, an outcome class, and
verification notes — so the "warn against retry" and "propose first" behaviours are both real.

## Hard rules for the demo

- No invented performance numbers, timings, or improvement claims — the claim is behavioural:
  *the second investigation is informed by the first*.
- No proprietary data, no secrets on screen.
- If the primary LLM fails during the recording, let the fallback serve it and keep the log line
  showing `fallback_used=True` — a visible resilience story, not a hidden one.
- The recording must be reproducible from the README; keep a pre-recorded backup.
