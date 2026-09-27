# System Design Notes — Cross-Cutting Principles

**Status:** Applies to ALL candidate ideas  
**Purpose:** Shared architecture foundation for whichever idea is selected  
**Reference:** [Project Ideas](project-ideas.md) | [Idea Comparison](idea-comparison.md) | [Demo Concepts](demo-concepts.md)

---

## Core Design Question

**"What changes because the agent remembers?"**

This is the central evaluation question for all candidate ideas. A strong memory workflow should resemble:

```
Interaction 1
    → experience retained
    → later/new case
    → relevant memory recalled
    → agent behavior changes
    → current evidence checked
    → outcome
    → new experience retained
```

Contrast with a weak pattern:
```
User question
    → retrieve old conversation
    → generate answer
```

**Simple conversation-history retrieval is insufficient for demonstrating meaningful persistent memory.** The agent must show: recall → changed reasoning/action → verification against current conditions → outcome → new retention.

---

## 1. Core Flow: User → Agent → Memory → Tools → Result

```
┌─────────────┐
│    User     │  (Engineer / Evaluator / On-call)
└──────┬──────┘
       │ Problem + Context
       ▼
┌─────────────┐
│    Agent    │  (LLM + Reasoning + Tool Access)
└──────┬──────┘
       │ Recall relevant memories
       ▼
┌─────────────┐
 │  Hindsight  │  (Persistent Memory Layer)
 │  (Recall)   │  → Query API (capabilities to verify)
 └──────┬──────┘
       │ Recalled cases (ranked)
       ▼
┌─────────────┐
│   Reason    │  (Agent synthesizes: hypotheses, priorities, warnings)
└──────┬──────┘
       │ Proposed action / investigation
       ▼
┌─────────────┐
│   Tools /   │  (Run reports, query logs, diff code,
│   Evidence  │   execute runbooks, fetch current state)
└──────┬──────┘
       │ Current evidence
       ▼
┌─────────────┐
│  Response   │  (Agent: "Here's what memory suggests, here's
│  / Action   │   current evidence, here's my recommendation")
└──────┬──────┘
       │ Outcome + User verification
       ▼
┌─────────────┐
 │  Hindsight  │  (Persistent Memory Layer)
 │  (Retain)   │  → Write API (capabilities to verify)
 └─────────────┘
```

**Key Principle:** Memory informs. Evidence verifies. Agent proposes. Human decides.

---

## 2. What Should Be Retained

| Category | Examples | Rationale |
|----------|----------|-----------|
| **Structured problem signature** | Timing path + corner, response pattern + rubric, symptom cluster + severity | Enables precise recall |
| **Environment/context** | Tool versions, PDK, deploy version, config, region | Critical for relevance verification |
| **Investigation/troubleshooting trace** | Ordered steps, tools used, intermediate findings | Shows reasoning path, not just outcome |
| **Failed approaches + rationale** | "Tried X → made Y worse because Z" | Prevents repeat failures |
| **Successful resolution + evidence** | Fix action, before/after metrics, commit/link | Verifiable success record |
| **Root cause / failure pattern** | Structured classification with tags | Enables pattern-based recall |
| **Lessons learned** | Actionable insights for future | Institutional knowledge capture |
| **Human verification** | "Confirmed relevant" / "Different because..." | Ground truth for memory quality |
| **Outcome classification** | RESOLVED / WORKAROUND / ESCALATED / FALSE_ALARM | Enables success-rate analytics |

---

## 3. What Should NOT Be Retained

| Category | Reason |
|----------|--------|
| Raw chat history / full conversation | Noise; not structured for recall |
| Unverified hypotheses | Pollutes memory with speculation |
| Sensitive data (secrets, PII, proprietary code) | Security/compliance; sanitize before retain |
| Duplicate/near-duplicate cases without enrichment | Memory bloat; merge or reference instead |
| Stale memories without version/context tags | Leads to false confidence |
| Low-confidence recall results | Only retain after human verification |
| Intermediate tool outputs (full logs, reports) | Store references/links, not blobs |

---

## 4. How Memories Influence Future Decisions

| Mechanism | Description | Layer |
|-----------|-------------|-------|
| **Ranked hypothesis generation** | Recall → top-k similar cases → extract root causes/fixes → rank by success rate × environment match | Application logic (requires Hindsight recall, to verify) |
| **Failed-approach blocking** | If recall shows approach failed in similar context → agent warns "Previously failed: [reason]" | Application logic |
| **Calibration anchoring** | Recall shows team calibration decisions → agent surfaces "Team agreed: pattern X = score Y" | Application logic |
| **Investigation prioritization** | Recall shows which steps yielded signal → agent orders checklist by historical yield | Application logic |
| **Context diff highlighting** | Agent compares current env vs recalled env → flags differences that may invalidate recall | Application logic |

---

## 5. Preventing Memory from Becoming False Certainty

| Risk | Mitigation | Dependency |
|------|------------|------------|
| **Memory treated as ground truth** | UI/agent always: "Memory suggests X. Current evidence shows Y. You decide." | Application-level UX |
| **Context mismatch ignored** | Mandatory environment diff check before applying recalled fix | Application-level verification |
| **Stale memory applied** | Memory TTL tags; version-aware recall; "Last verified: [date]" | Proposed application-level behavior (requires Hindsight timestamp/version support, to verify) |
| **Confirmation bias** | Agent shows both supporting AND contradicting memories | Application-level logic |
| **Single-memory overreliance** | Require minimum N similar cases for high-confidence recommendation | Application-level logic |
| **Hallucinated recall** | Structured schema validation; evidence links required for high-stakes suggestions | Application-level validation |

---

## 6. Verifying Current Conditions

| Check | Implementation | Dependency |
|-------|----------------|------------|
| **Environment match** | Structured diff: current {tool, version, config} vs recalled {tool, version, config} | Application-level (requires structured env in memory) |
| **Symptom equivalence** | Signature similarity score + human confirmation | Application-level |
| **Dependency version** | Check if library/service version changed since recalled case | Application-level (requires version in memory) |
| **Config drift** | Compare current config vs config at time of recalled case | Application-level (requires config snapshot in memory) |
| **Data freshness** | Timestamp on memory; warn if > threshold (configurable) | Proposed application-level (requires Hindsight timestamp, to verify) |
| **Human-in-the-loop** | Explicit "Verify relevance" step before agent acts on memory | Application-level UX |

---

## 7. Demonstrating Learning

| Dimension | How to Show |
|-----------|-------------|
| **Speed improvement** | Compare time-to-resolution: first case vs subsequent similar cases (proposed evaluation) |
| **Quality improvement** | Fewer failed approaches attempted; higher first-fix success rate (proposed evaluation) |
| **Consistency improvement** | Evaluation: inter-annotator agreement over time (proposed evaluation) |
| **Knowledge transfer** | Different user solves similar problem faster using memory (proposed evaluation) |
| **Pattern recognition** | Agent identifies failure pattern before human does (demo observation) |
| **Calibration drift prevention** | Evaluation scores stay aligned with team standard (proposed evaluation) |

**Demo requirement:** Side-by-side comparison (before memory / after memory) with proposed evaluation metric.

---

## 8. Evaluating Whether Memory Actually Helps

| Metric | Measurement |
|--------|-------------|
| **Recall precision** | % of recalled cases user marks "relevant" (proposed evaluation) |
| **Recall utility** | % of relevant recalls that changed user's action (proposed evaluation) |
| **Time saved** | Baseline (no memory) vs assisted time for same task class (proposed evaluation) |
| **Error reduction** | Failed approaches avoided / incorrect diagnoses prevented (proposed evaluation) |
| **Consistency gain** | Evaluation: variance reduction across evaluators/time (proposed evaluation) |
| **Memory growth value** | Marginal utility of Nth case (diminishing returns curve) (proposed evaluation) |
| **False positive rate** | Recalls user marks "not relevant" / "misleading" (proposed evaluation) |

**Experimental design for hackathon:** A/B within demo — same user, similar tasks, with/without memory (proposed evaluation approach).

---

## 9. Preventing Irrelevant Memory Retrieval

| Technique | Description | Dependency |
|-----------|-------------|------------|
| **Structured filtering first** | Filter by problem type, environment, severity before semantic search | Requires Hindsight structured filter support (to verify) |
| **Multi-stage recall** | Stage 1: Exact signature match. Stage 2: Relaxed match. Stage 3: Semantic. | Requires Hindsight multi-stage query support (to verify) |
| **Relevance scoring** | Weighted score: signature_match × env_match × recency × success_rate | Application-level ranking logic |
| **Minimum threshold** | Only return memories above relevance threshold | Application-level filtering |
| **Diversity injection** | Ensure recalled set covers different root causes, not just clones | Application-level logic |
| **User feedback loop** | "Not relevant" → downweight similar future recalls | Application-level learning (requires Hindsight update support, to verify) |

---

## 10. Handling Conflicting Memories

| Scenario | Resolution |
|----------|------------|
| **Same symptoms, different root causes** | Present both with evidence; highlight environment differences |
| **Same root cause, different fixes** | Show success rates; prefer fix with higher success in current env |
| **Memory A says "fix X works", Memory B says "fix X failed"** | Surface conflict explicitly; show context diff; let human decide |
| **Calibration drift** | Weight recent calibrations higher; show trend |

**Agent behavior:** "I found conflicting memories. Case A (env X): fix worked. Case B (env Y): fix failed. Key difference: [highlight]. Recommend: verify [specific condition] first."

---

## 11. Handling Outdated Memories

| Strategy | Implementation | Dependency |
|----------|----------------|------------|
| **Version tagging** | Every memory tagged with: tool versions, library versions, config hash | Application-level (requires structured fields in memory) |
| **TTL / freshness score** | Configurable decay; memories older than threshold flagged | Proposed application-level (requires Hindsight timestamp/query support, to verify) |
| **Supersession links** | New memory can mark previous as "superseded by [ID] because [reason]" | Proposed application-level (requires Hindsight update/link support, to verify) |
| **Periodic review** | Scheduled re-verification of high-impact memories | Application-level process |
| **Deprecation workflow** | Human marks memory deprecated → excluded from recall (but kept for audit) | Proposed application-level (requires Hindsight filter/update support, to verify) |

---

## 12. When No Relevant Memory Exists

| Agent Behavior | Description |
|----------------|-------------|
| **Explicit "no memory" signal** | "No similar cases found. Proceeding with standard methodology." |
| **Fallback to general knowledge** | Agent uses base LLM knowledge + domain best practices |
| **Learning opportunity** | "This will be the first case of its kind. Full trace will be retained." |
| **Structured first-case capture** | Ensure first case captures maximum structured fields for future value |
| **No fake recall** | Never hallucinate similar cases |

---

## 13. Generic Architecture Diagram (Mermaid)

```mermaid
flowchart TD
    User[User\n(Engineer/Evaluator/On-call)] -->|Problem + Context| Agent
    
    subgraph AgentCore[Agent Core]
        Agent[Agent\n(LLM + Reasoning + Tools)]
        Recall[Recall Engine\n(Query Builder + Ranker)]
        Reason[Reasoning Engine\n(Hypothesis Gen + Prioritization)]
        Verify[Verification Gate\n(Env Diff + Human Confirm)]
        Retain[Retention Engine\n(Schema Validation + Enrichment)]
    end
    
    Agent -->|Structured Query| HindsightRecall[Hindsight\n(Recall)]
    HindsightRecall -->|Recalled Memories| Recall
    Recall -->|Candidates| Reason
    Reason -->|Prioritized Actions| Verify
    Verify -->|Verified Plan| Tools[Tools / Evidence\n(Reports, Logs, Diffs, Runbooks)]
    Tools -->|Current Evidence| Agent
    Agent -->|Response + Recommendation| User
    User -->|Outcome + Verification| Retain
    Retain -->|Structured Case| HindsightRetain[Hindsight\n(Retain)]
    
    style AgentCore fill:#f0f4f8,stroke:#3498db
    style HindsightRecall fill:#e8f5e9,stroke:#27ae60
    style HindsightRetain fill:#e8f5e9,stroke:#27ae60
    style Verify fill:#fff3e0,stroke:#f39c12
```

---

## 14. Memory Schema Design Principles (All Ideas)

| Principle | Application |
|-----------|-------------|
| **Fixed schema per domain** | Enables structured queries; not free-form JSON |
| **Required fields enforced** | Problem signature, environment, outcome — never optional |
| **Versioned schema** | Schema version in every memory; migration path defined |
| **Evidence as references** | Store URIs/hashes, not blobs (reports, logs, commits) |
| **Tags as controlled vocabulary** | Prevents tag drift; managed taxonomy |
| **Human verification field** | Every memory: `verified_by`, `verified_at`, `relevance_confirmed` |
| **Lineage tracking** | `supersedes`, `related_to`, `derived_from` links |

---

## 15. Hindsight Integration Checklist (To Verify)

> **To verify against current Hindsight documentation:**

- [ ] Authentication / authorization model
- [ ] Memory write API: schema, batching, idempotency
- [ ] Memory read/recall API: query syntax, filters, pagination
- [ ] Semantic search capabilities: embedding generation, vector index
- [ ] Structured filter capabilities: exact match, range, tags
- [ ] Memory update / supersession support
- [ ] Namespace / project isolation
- [ ] Rate limits, quotas, pricing
- [ ] SDK availability (Python, TypeScript, Go)
- [ ] Webhook / event notifications on memory write
- [ ] Export / backup capabilities
- [ ] Audit logging

---

## 16. Implementation Priority Order (If Building)

1. **Hindsight integration layer** — Write/read/recall with schema validation
2. **Memory schema** — Domain-specific structured types
3. **Recall engine** — Structured filter + semantic hybrid + ranking
4. **Verification gate** — Env diff + human confirmation UI
5. **Agent reasoning** — Hypothesis generation from recall
6. **Tool adapters** — Report parser / log ingest / rubric engine
7. **User interface** — CLI / Slack / Web (per idea)
8. **Seed data pipeline** — Synthetic case generation
9. **Demo instrumentation** — Metrics capture for before/after
10. **Polish** — Error handling, logging, documentation

---

## 17. Proposed Non-Functional Targets (To Validate)

| Requirement | Proposed Target | Validation Method |
|-------------|-----------------|-------------------|
| Recall latency | < 500ms p99 | Load testing with realistic memory corpus |
| Retention latency | < 200ms (async OK) | Load testing |
| Memory accuracy | > 90% user-marked relevance | User study during demo |
| False positive rate | < 10% of recalls | User study during demo |
| Schema validation | 100% on write | Automated testing |
| Data privacy | Zero secrets in memory | Security review |
| Auditability | Full write/read/decision log | Log inspection |

> **Note:** These are proposed targets for the hackathon demo. Actual performance will depend on Hindsight API capabilities, implementation choices, and hardware. Validate against real measurements during implementation.