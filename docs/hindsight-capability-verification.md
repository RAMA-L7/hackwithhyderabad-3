# Hindsight Capability Verification

**Status:** Evidence review completed 2026-09-27 — no implementation started
**Sources reviewed (official only):**
- https://hindsight.vectorize.io/ (Overview, version 0.10 docs)
- https://hindsight.vectorize.io/developer/api/recall (Recall Memories)
- https://hindsight.vectorize.io/developer/api/memories (Memories: list, fetch, curate)
- https://github.com/vectorize-io/hindsight (README: Quick Start, Core Concepts, Running in Production)

**How to read this document:** each capability is classified as exactly one of
VERIFIED / PARTIALLY VERIFIED / NOT VERIFIED / NOT RELEVANT, based only on the pages above.
"Not verified in the reviewed official documentation" means evidence was insufficient —
not that Hindsight lacks the capability. Application-level proposals from our planning docs
are called out explicitly so verified facts are never confused with our own design choices.

---

## Capability Matrix

| Capability | Official Evidence | Status | Relevant Ideas | Design Impact |
|------------|------------------|--------|----------------|---------------|
| Retain / write memory (`retain(bank_id, content, ...)`) | Overview ("Your agent stores information via `retain()`"); GitHub README retain example with `content`, `context`, `timestamp`; recall page confirms retained facts carry text, type, context, metadata, tags, entities, dates | VERIFIED | All (1–5) | Core write path for every candidate exists. Every "retain case" step in our architectures maps to a real API call. |
| LLM extraction on retain (facts, entities, temporal data, relationships, normalization) | GitHub README ("retain uses an LLM to extract key facts, temporal data, entities, and relationships… normalization process") | VERIFIED | All | We do NOT need to build our own extraction pipeline. Seed cases can be retained as structured text; Hindsight derives searchable representations. |
| Recall / search memory (`recall(bank_id, query)`) | Recall API page (full reference, Python/Node/CLI/Go examples); response = structured facts, not raw documents | VERIFIED | All | Core read path for every candidate exists. Query is the only required field. |
| Semantic retrieval (vector similarity) | Overview (TEMPR: "Semantic — by meaning"); recall page (semantic arm, vector cosine `scores.semantic`) | VERIFIED | All, esp. 2, 4, 5 | Fuzzy matching (similar responses, adjacent sectors, similar deals) is natively supported. No embedding infrastructure needed. |
| Keyword retrieval (BM25 exact match) | Overview + recall page ("Keyword — exact words (BM25)"); `scores.keyword` | VERIFIED | 1, 3 (IDs, paths, error strings) | Exact identifiers (path names, incident IDs, version strings) are searchable. Hybrid semantic+keyword runs automatically. |
| Graph retrieval (entity/temporal/causal links) | Overview + recall page ("Graph — via entities"); entity JOIN in responses | VERIFIED | 3, 4, 5 (services, people, companies as entities) | Cross-case links (same library, same founder, same competitor) can leverage entity graph without custom graph code. |
| Temporal retrieval + timestamps | Recall page (`occurred_start/end` extracted, `mentioned_at`, `query_timestamp`, `temporal_window`); memories page (`time_field` filtering) | VERIFIED | All (recency), esp. 5 (staleness) | Recency scoring and "X months ago" queries are native. Note: recall's `temporal_window` **ranks, does not filter** — exhaustive time-bounded reads use the memories list endpoint instead. |
| RRF fusion + cross-encoder reranking + score outputs | Recall page (RRF Σ 1/(60+rank), cross-encoder, `scores.final/reranker/semantic/keyword`, `min_scores` floors) | VERIFIED | All | Relevance ordering is built in. `min_scores.reranker/final` can implement recall abstention ("no confident match → standard workflow"). Scores are per-query relative, not cross-query calibrated — thresholds must be tuned empirically, not assumed. |
| Fact-type filtering (`world` / `experience` / `observation`) | Recall page (`types` parameter, per-type pipelines) | VERIFIED | All | Useful separation: e.g. raw incident facts (`experience`) vs consolidated patterns (`observation`). |
| Tag scoping on recall (`tags`, `tags_match`, `tag_groups`) | Recall page (modes `any`/`any_strict`/`all`/`all_strict`/`exact`, compound `tag_groups`, fuzzy resolution) | VERIFIED | All | Domain filtering (service, severity, sector, stage, rubric) is native. Our "structured filter first" recall stage maps to real parameters. |
| Custom metadata (`dict[str,str]`) + context label on retain | Recall page response fields (`metadata`, `context` set during retain); per-user-memory pattern in GitHub README | VERIFIED | All | Environment/version/config fields ride on metadata + context. **But this is flat key-value metadata, not a rigid enforced schema** (see "rigid domain schema" below). |
| Observations (auto-consolidated, evidence-tracked beliefs) | Overview (dedup, exact-quote evidence, proof count, refined-not-overwritten, freshness awareness); recall page (`prefer_observations`, `include_source_facts`) | VERIFIED | 2, 4, 5 (patterns, calibration, theses) | Recurring failure patterns / red-flag patterns / theses can consolidate automatically with provenance. `prefer_observations` avoids raw+consolidated duplication. |
| Observation history + source facts | Memories page (`GET …/memories/{id}/history`, `source_facts` with `source_fact_ids`) | VERIFIED | 4, 5 (decision→outcome, thesis→accuracy audit trails) | The "was the thesis right" and "how did this pattern resolve" demo beats have native provenance support. |
| Memory curation: edit / invalidate / restore (PATCH) | Memories page (edit text/context/dates/type/entities with auto re-consolidation; invalidate = reversible soft-retire excluded from recall; restore) | VERIFIED | All (stale memories), esp. 4, 5 (outcome updates) | Outdated-memory handling is native: invalidate stale cases, PATCH outcomes onto existing cases. Invalidated rows stay auditable. Observations regenerate from sources (PATCH on observations returns 400 — curate underlying facts). |
| Contradiction reconciliation via consolidation | Memories page ("Superseded by a newer fact… Just retain the new fact. Consolidation already reconciles in-stream contradictions into a single observation.") | VERIFIED | All (conflicting memories) | Our "conflicting memories" design can lean on native consolidation instead of custom merge logic. Human-in-the-loop rule ("if only you know, curate it") matches our verification-gate principle. |
| Memory banks (isolated stores) + strict no-cross-bank-leakage | Overview ("A bank is an isolated memory store… Isolation is strict: no cross-bank leakage"); GitHub README | VERIFIED | All (persona/project separation) | One bank per project/persona gives hard isolation. Bank templates exist for declarative setup. |
| Bank mission / directives / disposition shaping `reflect` | Overview (mission, directives, disposition 1–5; affect `reflect` only, not `recall`) | VERIFIED | 2 (calibration rules as directives), all | Team calibration rules ("unfounded medical claims → 1–2") could be bank directives. Note: affects `reflect`, not `recall`. |
| `reflect()` agentic reasoning over memory | Overview + GitHub README (`reflect(bank_id, query)`, use-case examples) | VERIFIED | 2, 4, 5 (judgment-heavy workflows) | Deeper reasoning step available beyond lookup; disposition-aware. Optional, not required for MVP. |
| Mental models + Knowledge Pages | Overview (standing questions, background rewrites, DB-read serving, markdown projection) | VERIFIED | 2 (calibration summaries), 5 (sector pages) | Pre-computed summaries could speed demos; optional nice-to-have, not MVP-critical. |
| Python SDK | GitHub README (`pip install hindsight-client`), recall/memories pages (Python examples) | VERIFIED | All | Primary implementation language available. |
| TypeScript / Node.js SDK | GitHub README (`npm install @vectorize-io/hindsight-client`), recall/memories pages (Node examples) | VERIFIED | All (esp. 2, 4 — web UIs) | Web evaluation/deal UIs can use the native TS client. |
| Go SDK + CLI | GitHub README (`go get …`, CLI install), recall/memories pages (Go + CLI examples) | VERIFIED | 1, 3 (CLI/chatops workflows) | Terminal-first demos can script against the CLI directly. |
| MCP server (per-bank retain/recall/reflect tools) | GitHub README (`http://localhost:8888/mcp/{bank_id}/`, enabled by default) | VERIFIED | All | Alternative integration path: expose memory as agent tools with zero client code. |
| Framework integrations (LangGraph, CrewAI, Vercel AI SDK, OpenAI Agents, Google ADK, …) | GitHub README integrations list; docs integrations hub | VERIFIED | All | If the team picks an agent framework, native integration likely exists. |
| LLM wrapper (auto retain/recall per LLM call) | GitHub README (`hindsight-litellm`, `wrap_openai`/`wrap_anthropic`, 100+ models via LiteLLM) | VERIFIED | 2 (eval pipelines), all | Fastest path to a working loop: wrap the agent's LLM client and get retain/recall automatically. Explicit retain/recall still preferred for structured demo control. |
| Local deployment (Docker, K8s Helm, pip, embedded, Windows) | GitHub README (all options + platform matrix); docs installation | VERIFIED | All | No cloud dependency for the hackathon build. Embedded `hindsight-all` (Python) / `hindsight-all-npm` (Node) could collapse server+app into one process — to be spike-tested. |
| Cloud hosted option | GitHub README (Hindsight Cloud, usage-based, signup URL) | VERIFIED | All | Fallback if local setup proves costly in hackathon time. Requires account + API key; needs an LLM API key either way. |
| Storage backends (PostgreSQL+pgvector, Oracle) | GitHub README (Running in Production → Storage) | VERIFIED | NOT RELEVANT (MVP) | Relevant only if self-hosting beyond Docker defaults. Not an MVP concern. |
| Webhooks (retain/consolidation/refresh lifecycle events) | GitHub README (Events → webhooks API page) | VERIFIED | 4, 5 (outcome/refresh notifications) | Could notify on consolidation/refresh; optional. Existence verified, payload details not reviewed. |
| Monitoring (Prometheus metrics, dashboards) | GitHub README (Monitoring) | VERIFIED | NOT RELEVANT (MVP) | Production concern, not demo concern. |
| Memory Defense (opt-in secret/PII redaction, 45 patterns) | Overview + GitHub README (per-bank policy, redact-or-block) | VERIFIED | 4, 5 (confidentiality), all | Directly mitigates the VC confidentiality risk and the "zero secrets in memory" rule. Opt-in per bank — must be explicitly enabled. |
| Multilingual memory | Overview + GitHub README (language detected and preserved) | VERIFIED | NOT RELEVANT | None of the five ideas require it. |
| Rigid custom domain schema enforced by Hindsight | Retain takes free `content` + flat `metadata`/`tags`/`context`; no custom-schema DDL found in reviewed pages | NOT VERIFIED | All | **Key correction:** our "structured memory schema" must be an application-level convention (metadata/tags/context + app-side validation), not a Hindsight-enforced schema. This simplifies the design — no dependency on an unverified capability. |
| Native TTL / expiry on memories | No TTL/expiry construct found in reviewed pages; freshness handled via consolidation staleness + invalidate API | NOT VERIFIED | All (esp. 5) | Staleness must be application-level (dates in metadata + invalidate + staleness warnings). Do not assume native expiry. |
| Server-side authentication model | Extension points mention auth; Cloud uses API key; self-hosted auth details not found in reviewed pages | NOT VERIFIED | All | For local hackathon demo, likely a non-issue; for any shared deployment, verify before exposing. Do not claim a specific auth model. |
| Rate limits / quotas (self-hosted) | No numeric limits found in reviewed pages (Cloud is usage-based) | NOT VERIFIED | All | Load-test before claiming latency/throughput targets. Our " < 500ms p99" style targets remain proposed, not verified. |
| Retain batching / idempotency | Not found in reviewed pages | NOT VERIFIED | All | Seed-data ingestion should be written idempotently at the application level (e.g. bank-per-seed-run or dedupe keys) until verified. |
| List pagination parameters | List endpoint verified; pagination params not confirmed in reviewed excerpt | PARTIALLY VERIFIED | 4, 5 (case libraries) | Endpoint exists with rich filters; assume pagination must be confirmed during implementation spike. |
| Export / backup API | Cloud mentions backups/dashboard; no explicit export endpoint reviewed | PARTIALLY VERIFIED | NOT RELEVANT (MVP) | Demo does not need export. Revisit only for portfolio hardening. |
| General audit logging | Curation audit trail verified (invalidated rows kept, `edited_at`, `reason`); server-wide audit log not reviewed | PARTIALLY VERIFIED | All | Decision audit can rely on retained verification notes + curation reasons. Do not claim full audit logging. |
| Tenant model details | Config hierarchy global → tenant → bank mentioned; tenant semantics not reviewed | PARTIALLY VERIFIED | NOT RELEVANT (MVP) | Banks + tags suffice for MVP isolation. |
| Benchmark/accuracy claims about Hindsight itself | LongMemEval SOTA claims on official pages (vendor-reported, partially third-party reproduced) | NOT RELEVANT | None | We must not cite vendor benchmarks as evidence our project improves anything. Our docs already avoid this. |

---

## Verified Fact vs Application Proposal vs Unknown

- **Verified fact:** retain/recall/reflect exist with the parameters above; TEMPR 4-arm retrieval with RRF + cross-encoder; observations with provenance; edit/invalidate/restore curation; banks with strict isolation; tags/metadata/context filtering; Python/TS/Go/CLI/MCP clients; Docker/K8s/pip/embedded/Cloud deployment; webhooks; Memory Defense redaction.
- **Application-level proposal (ours, not Hindsight's):** domain memory schemas as metadata/tag conventions; application-side ranking by outcome/env match; verification gates; TTL/staleness policy via dates + invalidate; abstention thresholds on `min_scores` (tunable, not preset); abstention and conflict UI.
- **Unknown / unverified:** rigid custom schemas; native TTL; server auth model; numeric rate limits; retain idempotency/batching; list pagination details; export API; full audit logging.

## Implications for the Five Ideas

1. Every candidate's core loop (retain structured case → recall by query + tags → human verifies → retain outcome) maps to verified APIs. **No idea is blocked on an unverified Hindsight capability**, provided schemas stay application-level.
2. The heaviest "Hindsight magic" assumptions in our docs (custom schema enforcement, TTL tags, version-aware recall) must be re-read as application responsibilities — which is good news: it removes dependency risk rather than adding it.
3. Ideas 4 and 5 gain the most from newly verified specifics: PATCH-based outcome updates, observation history/source facts for audit trails, and Memory Defense for confidentiality.
4. The main remaining technical unknowns are operational (hosting choice, LLM key/cost, latency under demo load), identical across all five ideas — resolve once via a 1–2 hour spike, not per idea.
