# Project Ideas — Detailed Breakdown

**Status:** Five candidates under evaluation — no final selection  
**Reference:** [Idea Comparison Matrix](idea-comparison.md) | [System Design Notes](system-design-notes.md) | [Demo Concepts](demo-concepts.md)

---

## Idea 1: Engineering Debugging Memory Agent

### 1. Name
Engineering Debugging Memory Agent

### 2. One-Sentence Concept
An engineering assistant that remembers previous technical problems, investigation steps, attempted fixes, successful fixes, evidence, and outcomes to accelerate future debugging of similar issues.

### 3. Target User
- Hardware/software engineers (VLSI, embedded, firmware, backend)
- Debugging engineers in semiconductor, systems, or infrastructure teams
- Junior engineers learning from institutional knowledge

### 4. Real-World Problem
Engineers repeatedly encounter similar failure modes (timing violations, signal integrity issues, configuration mismatches, race conditions) but institutional knowledge lives in ticketing systems, wikis, or tribal memory. Each debug session starts from scratch or relies on imperfect keyword search.

### 5. Current Workflow Without Agent
1. Problem manifests (test failure, silicon bug, production issue)
2. Engineer searches Jira/Confluence/Slack for similar issues
3. Reads through potentially irrelevant past tickets
4. Forms hypothesis, runs experiments, iterates
5. Finds root cause and fix
6. Documents in ticket (often incompletely)
7. Knowledge remains siloed in unstructured text

### 6. Proposed Workflow With Agent
1. Problem manifests → engineer describes symptoms + context to agent
2. Agent recalls relevant past cases from Hindsight (structured memory)
3. Agent presents: similar problems, what was tried, what worked, evidence, conditions
4. Engineer verifies relevance against current environment
5. Agent proposes investigation priority based on past success patterns
6. Engineer executes, agent observes
7. On resolution: full case (problem, context, investigation, fix, evidence, lessons) retained in Hindsight

### 7. Why Persistent Memory Matters
- Debugging is pattern-recognition over high-dimensional context (tool versions, config, env, history)
- Each case adds to a growing case library that compounds in value
- Without persistence: every session is isolated; with persistence: institutional knowledge becomes queryable and actionable

### 8. What Hindsight Should Remember
| Memory Field | Type | Example |
|--------------|------|---------|
| Problem signature | Structured | "Setup time violation on path X, corner SS, 0.9V" |
| Symptoms | List | ["Timing report: -50ps slack", "Functional test fails at 1GHz"] |
| Environment/context | Structured | {tool: "PrimeTime", version: "2023.09", PDK: "TSMC 7nm", corner: "SS"} |
| Investigation steps | Ordered list | ["Checked clock tree skew", "Analyzed OCV derates", "Ran path-based analysis"] |
| Failed approaches | List with rationale | ["Increased drive strength → made hold worse", "Relaxed clock uncertainty → DRC violation"] |
| Successful solution | Structured | {action: "Inserted buffer on net Y", evidence: "Slack +120ps", side_effects: "Area +0.2%"} |
| Evidence | References | Links to reports, logs, waveforms, commit hashes |
| Outcome | Enum | RESOLVED / WORKAROUND / ESCALATED / DEFERRED |
| Lessons learned | Free text | "OCV derates were over-pessimistic; path-based analysis sufficient" |
| Tags | List | ["timing", "setup", "buffer-insertion", "7nm", "PrimeTime"] |

### 9. What the Agent Recalls
- Cases matching problem signature + environment similarity
- Ranked by: outcome success, environment match, recency
- Returns: summary + key evidence + investigation priority

### 10. How Recalled Memory Changes Behavior
| Without Memory | With Memory |
|----------------|-------------|
| Start with generic checklist | Start with prioritized investigation based on past success |
| Try approaches that failed before | Skip known-failed approaches for this context |
| Rediscover known root causes | Jump to verified root cause patterns |
| Re-verify known fixes | Apply verified fix with current-condition check |

### 11. What Gets Retained After Interaction
- New case (if novel) or enriched existing case (if similar)
- Updated success/failure statistics for approaches
- New tags, environment variants, evidence links
- Engineer's verification notes (confirmed/denied relevance)

### 12. Example First Interaction
**Engineer:** "PrimeTime reporting -80ps setup slack on reg2reg path clk_div2/u1/q → clk_div2/u2/d at SS 0.72V 125C. Using TSMC 7nm, PrimeTime 2023.09."

**Agent:** No matching memory. Proposes standard investigation checklist. Engineer investigates, finds hold violation on same path causing setup pessimism. Fixes hold → setup passes.

**Retained:** Case with problem signature, failed approach (margining setup), successful fix (hold buffer), evidence (before/after reports).

### 13. Example Later Interaction
**Engineer:** "PrimeTime -65ps setup on clk_div2/u3/q → clk_div2/u4/d at SS 0.72V 125C. Same flow."

**Agent:** Recalls previous case. "Similar path, same corner. Previous case: hold violation on adjacent path caused setup pessimism. Suggest checking hold on clk_div2/u3 first. Evidence: [link to previous report]."

**Engineer:** Checks hold → finds violation → fixes hold → setup passes. Investigation time reduced compared to first case without memory.

### 14. 60–120 Second Demo Story
1. **Opening (10s):** Show real timing violation report — "Every engineer knows this pain"
2. **First debug (30s):** Agent has no memory. Engineer describes problem. Agent gives generic checklist. Engineer works through it, finds root cause (hold causing setup pessimism). Save to memory.
3. **Memory created (10s):** Show structured memory entry in Hindsight UI
4. **Second debug (30s):** Similar violation on different path. Agent recalls previous case. Highlights: "Check hold first — worked last time." Engineer verifies, fixes in minutes.
5. **Closing (10s):** Side-by-side comparison: investigation workflow with and without relevant memory. Memory drove the behavioral change.

### 15. Realistic Data Requirements
- Structured problem data: problem signature, symptoms, environment/context, investigation steps, failed approaches, successful solution, evidence references, outcome, lessons learned
- Synthetic-but-realistic timing violation cases (path structures, corner names, voltage/temperature corners, tool versions, PDK identifiers)
- No live EDA tool integration required for MVP — structured case data can be pre-seeded
- Minimum: 10–15 seed cases for demo viability
- Optional extension: PrimeTime/Innovus/Tempus report parsing for production use

### 16. Possible UI
- CLI agent (engineer terminal workflow)
- VS Code extension (inline with reports)
- Web dashboard (case library browsing)
- Slack/Teams bot (chatops integration)

### 17. Possible Backend/Agent Architecture
```
User Input → Agent (LLM + Tools)
    ↓
Hindsight Recall (semantic + structured filter)
    ↓
Agent Reasoning (propose investigation priority)
    ↓
Current Evidence Check (engineer verifies against live environment)
    ↓
Verification (engineer confirms/denies relevance)
    ↓
Hindsight Retain (structured case entry)
```

### 18. MVP Scope
- Single problem domain (timing violations)
- **No live EDA tool integration required** — structured case data pre-seeded in Hindsight
- CLI interface
- 10 seed cases
- Recall → propose → verify → retain loop
- Basic similarity matching (problem signature + environment)

### 19. Nice-to-Have Features
- Multi-tool support (Innovus, Tempus, SpyGlass)
- Cross-project learning (anonymized)
- Automatic report parsing and signature extraction
- Team collaboration (shared memory namespace)
- Integration with Jira/GitHub for evidence linking

### 20. Main Technical Risks
| Risk | Mitigation |
|------|------------|
| Hindsight API limitations for structured recall | Verify API early; design fallback |
| Semantic similarity on technical signatures | Use structured filters + embeddings |
| Seed data quality and representativeness | Curate high-quality synthetic-but-realistic cases |
| Memory pollution from low-quality cases | Require engineer verification before retain |

### 21. Main Product Risks
| Risk | Mitigation |
|------|------------|
| Engineers don't trust agent suggestions | Transparent reasoning + evidence links |
| "Just use grep/wiki" mentality | Demonstrate investigation workflow comparison (proposed evaluation) |
| Seed data scarcity | Curate high-quality synthetic-but-realistic cases |

### 22. How the Idea Demonstrates Hindsight
- Memory is the **core differentiator**: without it, agent = generic checklist bot
- Visible learning: same problem class, different outcome after memory exists
- Structured memory schema shows domain-aware design
- Retention loop is explicit and auditable

### 23. What Would Make It Look Generic
- Chat interface that just "remembers conversation history"
- No structured memory schema (just blobs of text)
- No verification step (agent assumes memory = truth)
- Demo shows only personalization, not behavior change

### 24. How to Avoid Becoming a Generic Chatbot
- **Structured memory schema** with domain-specific fields
- **Explicit verification gate** before applying recalled fix
- **Investigation priority** output, not just answers
- **Evidence-linked memories** (reports, logs, commits)
- **Demo shows failed approach avoidance**, not just faster answer

---

## Idea 2: AI Evaluation Memory Agent

### 1. Name
AI Evaluation Memory Agent

### 2. One-Sentence Concept
An agent that remembers previous LLM evaluation cases, evaluation reasoning, failure patterns, corrections, and decisions to improve consistency and context-awareness of future evaluations.

### 3. Target User
- AI evaluators (human annotators)
- LLM application developers (building eval pipelines)
- AI QA teams (regression testing model outputs)
- RLHF/RLAIF teams

### 4. Real-World Problem
Evaluation is inconsistent: same evaluator rates similar responses differently over time; different evaluators disagree; subtle failure modes (hallucination types, reasoning gaps, tone drift) are forgotten. No institutional memory of "why this got score X."

### 5. Current Workflow Without Agent
1. Evaluator receives (prompt, response, rubric)
2. Reads response, applies rubric mentally
3. Assigns score, writes brief justification
4. Moves to next item
5. Calibration sessions happen occasionally
6. Disagreements resolved ad-hoc
7. Patterns across evaluations not systematically captured

### 6. Proposed Workflow With Agent
1. Evaluator presents (prompt, response, rubric) to agent
2. Agent recalls similar past evaluations from Hindsight
3. Agent shows: similar responses, scores given, reasoning, failure patterns noted, calibrations
4. Evaluator reviews, agrees/disagrees with recalled reasoning
5. Evaluator makes decision; agent records full trace
6. On completion: evaluation case (prompt, response, rubric, score, reasoning, recalled context, decision) retained

### 7. Why Persistent Memory Matters
- Evaluation consistency requires remembering past decisions and reasoning
- Failure patterns are subtle and recurring (e.g., "plausible but uncited claim in medical domain")
- Calibration memory prevents drift over time
- Each evaluation enriches the case library for future calibrations

### 8. What Hindsight Should Remember
| Memory Field | Type | Example |
|--------------|------|---------|
| Prompt | Text | "Summarize this medical abstract for a patient" |
| Response | Text | "The study shows drug X cures cancer..." |
| Rubric version | String | "v3.2-medical-summarization" |
| Score | Numeric | 2/5 |
| Reasoning | Structured | {hallucination: "claims cure not in source", tone: "alarmist", completeness: "misses key risks"} |
| Failure pattern tags | List | ["unfounded-medical-claim", "missing-risk-disclosure"] |
| Similar past cases | References | Links to 3 most similar evaluations |
| Calibration notes | Text | "Team agreed: unfounded medical claims = auto 1-2" |
| Evaluator ID | String | "eval-042" |
| Timestamp | ISO8601 | "2024-01-15T10:30:00Z" |
| Outcome | Enum | ACCEPTED / REJECTED / NEEDS_REVIEW |

### 9. What the Agent Recalls
- Evaluations with similar (prompt type, response pattern, rubric)
- Ranked by: response similarity, rubric match, recency
- Returns: score distribution, reasoning patterns, calibration decisions

### 10. How Recalled Memory Changes Behavior
| Without Memory | With Memory |
|----------------|-------------|
| Evaluate in isolation | See how similar responses were scored |
| Forget subtle failure modes | Recall tagged failure patterns |
| Drift from calibration | See team calibration notes inline |
| Inconsistent reasoning | Align reasoning with past consistent cases |

### 11. What Gets Retained After Interaction
- New evaluation case with full trace
- Updated failure pattern frequency
- Evaluator agreement/disagreement with recalled cases
- New calibration insights (if any)

### 12. Example First Interaction
**Evaluator:** Evaluates response claiming "Drug X reduces mortality by 50%" — source says "5% reduction." Scores 2/5. Tags: "exaggerated-efficacy-claim." Reasoning recorded.

### 13. Example Later Interaction
**Evaluator:** Evaluates response claiming "Treatment Y improves survival 40%" — source says "4%." Agent recalls: "Similar exaggerated-efficacy-claim pattern. Previous 3 cases: scores 1-2. Team calibration: auto 1-2 for unfounded medical claims."

**Evaluator:** Confirms pattern, scores 2/5 with consistent reasoning.

### 14. 60–120 Second Demo Story
1. **Opening (10s):** "Evaluation inconsistency costs companies millions in bad model deployments"
2. **First eval (30s):** Evaluator scores hallucinated medical claim. Agent records reasoning, tags failure pattern.
3. **Memory created (10s):** Show Hindsight entry with structured reasoning + tags
4. **Second eval (30s):** Different response, same failure pattern. Agent surfaces: "3 prior cases with 'exaggerated-efficacy-claim' — all scored 1-2. Calibration note: auto 1-2." Evaluator aligns.
4. **Closing (10s):** Show consistency comparison: evaluation workflow with and without memory-assisted calibration.

### 15. Realistic Data Requirements
- Real prompts from target domains (medical, legal, coding, creative)
- Real model responses (with known issues)
- Rubric definitions (multi-dimensional)
- Calibration guidelines
- Minimum: 20–30 seed evaluations across 3–4 failure patterns

### 16. Possible UI
- Side-by-side evaluation interface (response + agent recall panel)
- Batch evaluation queue with recall summaries
- Calibration dashboard (pattern frequency, agreement trends)
- API for integration into existing eval pipelines

### 17. Possible Backend/Agent Architecture
```
Evaluation Item → Agent
    ↓
Hindsight Recall (response embedding + rubric match)
    ↓
Agent presents: similar cases, score dist, patterns, calibrations
    ↓
Evaluator decides (accept/modify/reject recall)
    ↓
Hindsight Retain (full evaluation trace + evaluator decision)
```

### 18. MVP Scope
- Single domain (e.g., medical summarization)
- One rubric version
- Web evaluation interface
- 20 seed evaluations
- Recall panel showing similar cases + patterns
- Retain with evaluator confirmation

### 19. Nice-to-Have Features
- Multi-rubric support
- Automatic failure pattern detection (LLM-assisted tagging)
- Inter-annotator agreement tracking
- Drift detection over time
- Export to standard eval formats (JSONL, CSV)

### 20. Main Technical Risks
| Risk | Mitigation |
|------|------------|
| Response similarity matching quality | Hybrid: embeddings + structured rubric match |
| Evaluator bias toward agent suggestions | Explicit "disagree" workflow; track override rate |
| Rubric evolution breaking recall | Version rubrics; migrate memories on version change |

### 21. Main Product Risks
| Risk | Mitigation |
|------|------------|
| "Just use a spreadsheet" | Demonstrate consistency comparison (proposed evaluation) |
| Evaluators feel monitored | Frame as "calibration assistant," not surveillance |
| Domain too narrow for portfolio | Pick domain with visible complexity (medical/legal) |

### 22. How the Idea Demonstrates Hindsight
- Memory stores **evaluation reasoning**, not just scores
- Recall directly informs **current judgment**
- Calibration memory prevents **drift**
- Each evaluation **enriches** the case library

### 23. What Would Make It Look Generic
- Simple "show me past scores" without reasoning
- No failure pattern taxonomy
- No calibration memory
- Demo shows only speed, not consistency improvement

### 24. How to Avoid Becoming a Generic Chatbot
- **Structured reasoning trace** in memory (not just score)
- **Failure pattern taxonomy** maintained across evaluations
- **Calibration memory** as first-class concept
- **Evaluator override tracking** (agent learns from disagreements)
- **Demo shows consistency metric**, not just "it remembered"

---

## Idea 3: Engineering Incident Memory Agent

### 1. Name
Engineering Incident Memory Agent

### 2. One-Sentence Concept
An incident-response assistant that remembers previous incidents, symptoms, logs, root causes, troubleshooting steps, attempted fixes, successful resolutions, and post-incident lessons to accelerate future incident response.

### 3. Target User
- DevOps engineers
- SREs
- IT operations
- Platform engineers
- On-call engineers

### 4. Real-World Problem
Incidents repeat or rhyme. On-call engineers at 3 AM lack context: "Have we seen this error pattern? What did we try last time? Did it work?" Postmortems exist but are hard to query under pressure. Runbooks go stale.

### 5. Current Workflow Without Agent
1. Alert fires → on-call wakes up
2. Checks dashboards, logs, traces
3. Searches past incidents (PagerDuty, incident.io, docs)
4. Reads postmortems, tries to map to current symptoms
5. Forms hypothesis, runs runbooks, iterates
6. Resolves → writes postmortem (often delayed/incomplete)
7. Knowledge stays in unstructured docs

### 6. Proposed Workflow With Agent
1. Alert fires → engineer describes symptoms + context to agent
2. Agent recalls relevant past incidents from Hindsight
3. Agent presents: matching incidents, root causes, what was tried, what worked, runbooks used, postmortem links
4. Engineer verifies relevance (environment, config, version)
5. Agent proposes investigation priority + runbook steps
6. Engineer executes, agent observes
7. On resolution: full incident record retained (symptoms, logs, root cause, timeline, fix, lessons)

### 7. Why Persistent Memory Matters
- Incidents are high-stakes, time-sensitive, pattern-driven
- Institutional memory degrades; on-call rotation loses context
- Runbooks are static; memory is dynamic and case-specific
- Each incident enriches the knowledge base for the next on-call

### 8. What Hindsight Should Remember
| Memory Field | Type | Example |
|--------------|------|---------|
| Incident ID | String | "INC-2024-0451" |
| Symptoms | List | ["API 500 rate > 5%", "DB connection pool exhausted", "Latency p99 > 10s"] |
| Severity | Enum | SEV-1 / SEV-2 / SEV-3 |
| Environment/context | Structured | {service: "payments-api", version: "v2.3.1", region: "us-east-1", k8s: "1.28"} |
| Triggering change | Reference | Deploy ID, config change, traffic spike |
| Investigation timeline | Ordered events | ["00:05 checked logs", "00:12 found connection leak", "00:18 restarted pods"] |
| Root cause | Structured | {component: "payments-db-pool", cause: "connection leak in retry logic", commit: "abc123"} |
| Attempted fixes | List | ["Scaled replicas → no effect", "Increased pool size → OOM", "Rolled back deploy → resolved"] |
| Successful resolution | Structured | {action: "Rollback v2.3.1 → v2.3.0", time_to_resolve: "23 min"} |
| Runbooks used | References | ["runbook-db-connection-exhaustion", "runbook-rollback"] |
| Postmortem link | URL | "https://incident.io/incidents/INC-2024-0451/postmortem" |
| Lessons learned | Text | "Retry logic needs circuit breaker; add pool metrics alert" |
| Tags | List | ["database", "connection-pool", "retry-loop", "rollback"] |

### 9. What the Agent Recalls
- Incidents matching symptom signature + environment similarity
- Ranked by: resolution success, environment match, recency, severity
- Returns: root cause candidates, failed approaches, successful resolution, runbook links

### 10. How Recalled Memory Changes Behavior
| Without Memory | With Memory |
|----------------|-------------|
| Start from generic runbooks | Start with ranked root cause hypotheses |
| Try fixes that failed before | Skip known-failed fixes for this context |
| Miss similar past root cause | Jump to verified cause pattern |
| Re-learn same lessons | Apply lessons proactively |

### 11. What Gets Retained After Interaction
- New incident record (if novel) or enriched existing (if similar)
- Updated fix success/failure statistics
- New symptom → root cause mappings
- Engineer's verification notes
- Updated runbook references

### 12. Example First Interaction
**On-call:** "payments-api 500s spiked to 8% after deploy v2.3.1. DB connection pool exhausted. Latency p99 15s."

**Agent:** No matching memory. Proposes standard DB exhaustion runbook. Engineer investigates, finds connection leak in new retry logic. Rollback resolves.

**Retained:** Full incident with root cause (retry logic leak), failed fix (scale replicas), successful fix (rollback), lessons (circuit breaker needed).

### 13. Example Later Interaction
**On-call (different engineer):** "orders-api 500s at 6% after deploy v1.8.0. DB pool exhausted. Latency p99 12s."

**Agent:** Recalls payments-api incident. "Similar symptom: DB pool exhaustion after deploy. Root cause: connection leak in retry logic (commit abc123). Fix: rollback worked. Check: does orders-api use same retry library?"

**Engineer:** Checks → yes, same library. Rollback → resolved significantly faster than first incident.

### 14. 60–120 Second Demo Story
1. **Opening (10s):** "3 AM on-call. Alert fires. You have 5 minutes to understand."
2. **First incident (30s):** Alert on payments-api. Agent has no memory. Engineer follows runbook, finds root cause (retry leak), rolls back. Save to memory.
3. **Memory created (10s):** Show structured incident in Hindsight with timeline, root cause, lessons.
4. **Second incident (30s):** Different service, same pattern. Agent recalls: "Same retry library, same leak pattern. Rollback worked last time. Check version." Engineer verifies, rolls back in minutes.
5. **Closing (10s):** Comparison: incident resolution workflow with and without relevant memory. Different engineer, same resolution. Memory transferred institutional knowledge.

### 15. Realistic Data Requirements
- Realistic alert payloads (Prometheus, Datadog, PagerDuty formats)
- Log samples (structured JSON logs)
- Deploy metadata (version, commit, config)
- Runbook references
- Postmortem templates
- Minimum: 8–12 seed incidents across 3–4 failure patterns

### 16. Possible UI
- CLI (on-call terminal workflow)
- Slack/Teams bot (chatops)
- Web dashboard (incident library, pattern analysis)
- PagerDuty/incident.io integration (auto-create memory from incident)

### 17. Possible Backend/Agent Architecture
```
Alert/Input → Agent
    ↓
Hindsight Recall (symptom signature + environment)
    ↓
Agent: ranked hypotheses + runbook steps + failed approaches
    ↓
Engineer executes + verifies
    ↓
Hindsight Retain (full incident record)
```

### 18. MVP Scope
- Single failure domain (DB connection exhaustion)
- One log format / alert source
- CLI or Slack interface
- 8 seed incidents
- Recall → hypothesis → verify → retain loop
- Basic runbook linking

### 19. Nice-to-Have Features
- Auto-ingest from PagerDuty/incident.io
- Timeline reconstruction from logs
- Cross-service pattern detection
- Runbook execution tracking
- On-call handoff summaries

### 20. Main Technical Risks
| Risk | Mitigation |
|------|------------|
| Log/alert format variability | Start with one stack (e.g., k8s + Prometheus) |
| Symptom signature design | Structured fields + embedding hybrid |
| High-stakes domain → liability | Clear "agent suggests, human verifies" UX |

### 21. Main Product Risks
| Risk | Mitigation |
|------|------------|
| On-call engineers won't trust it | Transparent reasoning; show evidence links |
| "Runbooks already exist" | Show runbooks are static; memory is case-specific |
| Alert fatigue → low adoption | Integrate into existing chatops flow |

### 22. How the Idea Demonstrates Hindsight
- Memory captures **full incident anatomy** (symptoms → root cause → fix → lessons)
- Recall drives **hypothesis prioritization** under time pressure
- Cross-service learning (payments → orders) shows **transfer**
- Lessons learned become **proactive patterns**

### 23. What Would Make It Look Generic
- Just "search past incidents" with keyword matching
- No structured root cause / fix tracking
- No verification gate
- Demo shows only "found a similar incident" not "changed my action"

### 24. How to Avoid Becoming a Generic Chatbot
- **Structured incident schema** with causal fields
- **Failed approach tracking** (what didn't work)
- **Verification required** before applying recalled fix
- **Runbook integration** (memory enhances, doesn't replace)
- **Demo shows incident resolution workflow comparison** with and without memory

---

## Idea 4: Deal Intelligence Agent

### 1. Name
Deal Intelligence Agent

### 2. One-Sentence Concept
A VC investment assistant that remembers past deal evaluations — thesis fit, diligence findings, red flags, reference-check outcomes, decisions, and eventual results — to sharpen diligence and decision quality on new deals.

### 3. Target User
- VC investment associates / analysts screening and diligencing deals
- Partners preparing investment committee (IC) memos
- Venture studios / angel syndicates doing repeat deal flow

### 4. Real-World Problem
VCs evaluate hundreds of pitches per partner per year. Institutional memory of "we saw this exact pattern before and it didn't work" or "this red flag always turns out fine" lives in individual partners' heads, scattered memos, and old Notion pages. Analysts re-derive diligence from scratch on every deal, junior team members lack access to the firm's pattern-recognition, and post-mortems on passed/invested deals rarely feed back into the next evaluation.

### 5. Current Workflow Without Agent
1. Deck/data room arrives → analyst reads and summarizes
2. Analyst does market research, builds comps, drafts open questions
3. Analyst asks a partner "have we seen something like this?" — answer depends on partner's memory and availability
4. Reference calls happen, notes go into a doc that's rarely revisited
5. IC memo written, decision made
6. Outcome (follow-on, write-off, exit) is almost never linked back to the original diligence memo
7. Pattern knowledge stays tribal, decays as team turns over

### 6. Proposed Workflow With Agent
1. Analyst enters new deal context (sector, stage, model, metrics, founder background, thesis) to agent
2. Agent recalls similar past deals from Hindsight: comparable sector/stage/thesis, diligence questions that mattered, red flags found, decision + rationale, and known outcome if resolved
3. Agent presents a prioritized diligence checklist and named risks based on what mattered in similar cases
4. Analyst verifies against this deal's actual data (current metrics, reference calls, market conditions)
5. Agent flags contradictions ("last 3 comparable deals had this red flag and it correlated with churn — check retention cohort")
6. On decision: full case (deal signature, diligence trace, red flags, decision, rationale) retained
7. On later outcome (raises next round, shuts down, exits): case updated with outcome, closing the loop for future recall

### 7. Why Persistent Memory Matters
- Deal pattern-recognition is exactly what senior partners have and junior analysts don't — memory is a way to transfer it
- Outcomes resolve months to years after the decision; without persistence, the diligence-to-outcome link is lost
- Each closed-loop case (decision → outcome) compounds the firm's ability to calibrate future decisions
- Without memory: every deal looks novel. With memory: recurring red-flag and thesis patterns become visible and queryable

### 8. What Hindsight Should Remember
| Memory Field | Type | Example |
|--------------|------|---------|
| Deal signature | Structured | {sector: "vertical SaaS logistics", stage: "Seed", geography: "India", check_size: "$500K"} |
| Thesis fit | Structured | {thesis: "India B2B SaaS", fit_score: "strong", notes: "matches prior logistics-SaaS thesis"} |
| Diligence questions asked | Ordered list | ["Unit economics by lane", "Customer concentration", "Founder-market fit"] |
| Red flags found | List with rationale | ["Top 3 customers = 60% revenue", "Founder pivoted twice in 18mo"] |
| Reference check outcomes | Structured | {reference: "former co-founder", signal: "strong exec, weak on sales hiring"} |
| Decision | Enum | INVEST / PASS / WATCH |
| Decision rationale | Text | "Passed: customer concentration risk outweighed thesis fit" |
| Evidence | References | Links to data room excerpts, memo, model, call notes |
| Outcome (when known) | Enum + text | RAISED_NEXT_ROUND / FLAT / DOWN_ROUND / SHUT_DOWN / EXIT — "Raised Series A 14mo later at 2x" |
| Tags | List | ["vertical-saas", "logistics", "customer-concentration", "india-seed"] |

### 9. What the Agent Recalls
- Past deals matching sector + stage + thesis + key metrics signature
- Ranked by: outcome-informed relevance (did this pattern resolve well or badly), thesis match, recency
- Returns: comparable deals, red flags that mattered, reference-check patterns, and known outcomes

### 10. How Recalled Memory Changes Behavior
| Without Memory | With Memory |
|----------------|-------------|
| Generic diligence checklist | Diligence prioritized by what caught similar deals before |
| Red flag treated in isolation | Red flag weighted by how it played out in comparable past deals |
| Partner memory is the only pattern source | Analyst can query the pattern directly |
| Decision rationale forgotten after IC | Rationale + eventual outcome linked for calibration |

### 11. What Gets Retained After Interaction
- New deal case (diligence trace, red flags, decision, rationale) or enriched existing thesis pattern
- Updated outcome on a prior case, when new information arrives (raise, shutdown, exit)
- Analyst's verification notes (confirmed/denied relevance of recalled comparisons)
- New tags and thesis-pattern links

### 12. Example First Interaction
**Analyst:** "Seed-stage vertical SaaS for trucking/logistics in India, $500K ask, 3 customers = 55% of revenue, founder ex-McKinsey no logistics background."

**Agent:** No matching memory. Proposes standard diligence checklist (unit economics, customer concentration, founder-market fit, competitive moat). Analyst runs diligence, decision: PASS due to concentration + weak founder-market fit. Recorded.

**Retained:** Case with deal signature, red flags (concentration, founder fit), decision (PASS), rationale.

### 13. Example Later Interaction
**Analyst:** "Seed-stage vertical SaaS for cold-chain logistics, India, $600K ask, top 2 customers = 60% of revenue, founder ex-consulting no domain background."

**Agent:** Recalls the earlier case. "Similar signature: customer concentration + non-domain founder. Previous case passed on this exact combination. Check: does this founder have a domain co-founder or advisor to offset it? That was the deciding factor we didn't check thoroughly last time."

**Analyst:** Checks — founder has a strong domain co-founder this time. Proceeds to term sheet, notes the distinguishing factor explicitly in the memo.

### 14. 60–120 Second Demo Story
1. **Opening (10s):** Show a real-looking pitch deck slide with concentrated customer base — "Every VC has seen this pattern and forgotten what happened last time."
2. **First deal (30s):** Agent has no memory. Analyst runs generic diligence, finds concentration + weak founder fit, passes. Save to memory.
3. **Memory created (10s):** Show structured deal case in Hindsight (signature, red flags, decision, rationale).
4. **Second deal (30s):** New deal, same red-flag pattern. Agent recalls prior case, flags the exact distinguishing question to check (domain co-founder). Analyst verifies, decision differs with clear rationale.
5. **Closing (10s):** Side-by-side: diligence workflow with and without the recalled pattern-check. Memory sharpened the question, not just the answer.

### 15. Realistic Data Requirements
- Structured deal data: sector, stage, metrics, founder background, red flags, reference notes, decision, rationale, outcome
- Synthetic-but-realistic deal cases modeled on common VC patterns (customer concentration, founder-market fit, churn, moat questions)
- No live data-room/CRM integration required for MVP — structured case data can be pre-seeded
- Minimum: 10–15 seed deal cases spanning 3–4 recurring red-flag patterns
- Optional extension: ingest real (anonymized) IC memo templates

### 16. Possible UI
- Web dashboard (deal case library + recall panel alongside a deal brief)
- CLI/API for quick "similar deals" queries during a partner meeting
- Slack bot for async "has anyone seen this pattern" queries

### 17. Possible Backend/Agent Architecture
```
Deal Brief Input → Agent (LLM + Tools)
    ↓
Hindsight Recall (deal signature + thesis + red-flag match)
    ↓
Agent Reasoning (prioritized diligence questions + risk flags)
    ↓
Current Evidence Check (analyst verifies against actual deal data)
    ↓
Verification (analyst confirms/denies relevance)
    ↓
Hindsight Retain (deal case; later: outcome update)
```

### 18. MVP Scope
- Single sector focus (e.g., vertical SaaS / India seed stage)
- No live CRM/data-room integration — structured case data pre-seeded in Hindsight
- Web or CLI interface
- 10 seed deal cases
- Recall → propose → verify → retain loop
- Basic similarity matching (sector + stage + red-flag tags)

### 19. Nice-to-Have Features
- Outcome auto-tracking via periodic web lookups (funding announcements)
- Cross-partner shared memory namespace with attribution
- CRM integration (Affinity, Airtable) for auto-ingest of deal metadata
- Thesis-drift detection (are we passing on deals that match our stated thesis?)
- IC memo drafting assist grounded in recalled comparables

### 20. Main Technical Risks
| Risk | Mitigation |
|------|------------|
| Hindsight API limitations for structured recall | Verify API early; design fallback |
| Deal similarity is fuzzier than engineering signatures | Combine structured tags + semantic embedding on thesis/notes text |
| Seed data quality and representativeness | Curate realistic synthetic deal cases with clear patterns |
| Outcome data usually arrives late/incomplete | Support partial/unknown outcome state explicitly |

### 21. Main Product Risks
| Risk | Mitigation |
|------|------------|
| Partners don't trust an agent's pattern-matching on judgment calls | Transparent reasoning + evidence links; agent proposes, human decides |
| Confidentiality concerns with real deal data | Use synthetic-but-realistic seed data for demo; note real deployment needs access controls |
| "Just ask the partner" mentality | Demonstrate junior analyst reaching senior-level diligence questions unaided |

### 22. How the Idea Demonstrates Hindsight
- Memory is the **core differentiator**: without it, agent = generic diligence checklist
- Visible learning: same red-flag pattern, sharper diligence question on the second pass
- Structured memory schema captures decision *and* eventual outcome — a full feedback loop
- Retention loop is explicit and auditable (decision now, outcome later)

### 23. What Would Make It Look Generic
- Chat interface that just "remembers the conversation"
- No structured red-flag/outcome schema, just free-text notes
- No verification step (agent's pattern match treated as the decision)
- Demo shows only "found similar deal," not a changed diligence question

### 24. How to Avoid Becoming a Generic Chatbot
- **Structured memory schema** with decision + outcome as first-class fields
- **Explicit verification gate** before analyst acts on recalled pattern
- **Prioritized diligence questions** as output, not just "similar deals found"
- **Evidence-linked memories** (data room excerpts, call notes, outcome sources)
- **Demo shows a changed diligence question**, not just a faster answer

---

## Idea 5: Competitive Intelligence Agent

### 1. Name
Competitive Intelligence Agent

### 2. One-Sentence Concept
A VC research assistant that remembers past competitive-landscape maps — who was named as the leader, what moat thesis was used, and how the market actually played out — to sharpen competitive assessments on new deals and portfolio reviews.

### 3. Target User
- VC associates / analysts researching a sector before or during diligence
- Partners doing portfolio-company competitive check-ins
- Platform/portfolio-support teams tracking a portfolio company's competitive set

### 4. Real-World Problem
Competitive landscapes get re-researched from scratch every time a similar sector question comes up ("who's in vertical SaaS for logistics in India"), with no memory of prior research quality, which competitor thesis turned out right or wrong, or how fast the landscape has shifted since the last look. Analysts don't know which of their own or colleagues' past competitive calls held up, so the same mistakes (missing a fast-moving new entrant, over-weighting a well-funded incumbent) recur.

### 5. Current Workflow Without Agent
1. New deal or portfolio review triggers a competitive question
2. Analyst googles, reads Crunchbase/Tracxn, builds a landscape slide from scratch
3. Analyst forms a view on the likely winner/moat, presents it
4. Slide gets archived in a deck, rarely revisited
5. Months later, market shifts (new entrant, incumbent stalls) — nobody checks whether the earlier thesis was right
6. Next similar sector question starts the research over again, from zero

### 6. Proposed Workflow With Agent
1. Analyst asks the agent about a sector/company ("competitive landscape for X")
2. Agent recalls prior competitive maps for that sector or adjacent ones from Hindsight: named competitors, moat thesis, and — if enough time has passed — what actually happened
3. Agent presents: previous landscape, prior thesis, accuracy of that thesis (if resolved), and flags likely staleness ("this map is 14 months old; two of these categories move fast")
4. Analyst verifies against current evidence (recent funding news, product launches, win-rate signals)
5. Agent proposes updated research priorities based on what changed or wasn't checked last time
6. On completion: updated landscape retained as a new case; prior case's thesis is marked resolved/unresolved with an accuracy note

### 7. Why Persistent Memory Matters
- Competitive theses are falsifiable predictions that resolve over time — without persistence, nobody ever finds out if they were right
- Landscape research is highly repeatable across deals in the same sector; redoing it from scratch every time is pure waste
- Tracking thesis accuracy over time is exactly the kind of institutional calibration a VC needs and rarely has
- Each resolved case sharpens the agent's (and team's) judgment about which signals actually predicted the winner

### 8. What Hindsight Should Remember
| Memory Field | Type | Example |
|--------------|------|---------|
| Sector/landscape signature | Structured | {sector: "vertical SaaS logistics", geography: "India", segment: "trucking marketplaces"} |
| Named competitors | List | ["CompanyA (Series B, funded)", "CompanyB (Seed)", "CompanyC (bootstrapped)"] |
| Moat/winner thesis | Structured | {predicted_leader: "CompanyA", basis: "network effects + funding lead", confidence: "medium"} |
| Research date | ISO8601 | "2025-03-10" |
| Sources used | References | Links to Crunchbase/Tracxn pulls, news articles, product pages |
| Source reliability notes | Text | "Tracxn funding data 2 months stale at time of research" |
| Resolution (when known) | Structured | {actual_outcome: "CompanyB won on price + local ops", thesis_accuracy: "incorrect — underweighted execution speed"} |
| Tags | List | ["vertical-saas", "logistics", "india", "network-effects-thesis"] |

### 9. What the Agent Recalls
- Prior landscape maps matching sector + geography + segment signature
- Ranked by: sector match, recency (with explicit staleness warning), and thesis resolution status
- Returns: prior competitor list, prior thesis, resolution/accuracy if known, and what's likely to have changed since

### 10. How Recalled Memory Changes Behavior
| Without Memory | With Memory |
|----------------|-------------|
| Rebuild the landscape from zero every time | Start from the last map, verify what's changed |
| No record of which past thesis was right | See which signals (funding, network effects, execution speed) actually predicted the winner before |
| Treat all sources as equally fresh | Explicit staleness warning on recalled data |
| Same blind spot repeats across deals | Agent flags "last time we underweighted X — check for it again" |

### 11. What Gets Retained After Interaction
- New or updated landscape case (competitor list, thesis, sources)
- Resolution update on a prior case's thesis, when outcome becomes known
- Analyst's verification notes on which recalled competitors are still relevant vs. defunct/acquired
- New tags and thesis-pattern links (e.g., "network-effects-thesis" accuracy record)

### 12. Example First Interaction
**Analyst:** "What's the competitive landscape for vertical SaaS trucking marketplaces in India?"

**Agent:** No matching memory. Analyst researches from scratch: names CompanyA, CompanyB, CompanyC; thesis: CompanyA wins on network effects + funding lead. Recorded with sources and confidence.

**Retained:** Landscape case with competitor list, thesis, sources, date.

### 13. Example Later Interaction
**Analyst (14 months later, new deal in adjacent segment):** "Competitive landscape for cold-chain logistics SaaS in India?"

**Agent:** Recalls the trucking-marketplace case as adjacent. "Related landscape from 14 months ago: thesis was CompanyA (funding + network effects) would win; actual outcome — CompanyB won on price + local ops execution, thesis was rated inaccurate. For this adjacent segment, consider execution-speed signals, not just funding and network effects."

**Analyst:** Weights execution-speed and local-ops signals more heavily in this new landscape, avoiding the same overweighting mistake.

### 14. 60–120 Second Demo Story
1. **Opening (10s):** Show a landscape slide from months ago — "We build these every deal. We never check if we were right."
2. **First research (30s):** Agent has no memory. Analyst builds landscape, names a predicted winner, records thesis + sources.
3. **Memory created (10s):** Show structured landscape case in Hindsight (competitors, thesis, sources, confidence).
4. **Second research (30s):** New but related sector question, later in time. Agent recalls the prior case, surfaces the thesis's actual accuracy, and flags the specific blind spot (underweighted execution speed) to correct this time.
5. **Closing (10s):** Side-by-side: research workflow with and without the resolved prior thesis. Memory turned a forgotten prediction into a calibration tool.

### 15. Realistic Data Requirements
- Structured landscape data: sector signature, competitor list, thesis, confidence, sources, resolution/accuracy
- Synthetic-but-realistic sector cases (Indian VC-relevant verticals: fintech, logistics, SaaS, D2C) with plausible competitor sets and outcomes
- No live Crunchbase/Tracxn API integration required for MVP — structured case data can be pre-seeded
- Minimum: 10–15 seed landscape cases, at least half with a resolved outcome for the "was the thesis right" demo beat
- Optional extension: Tracxn/Crunchbase API pull for live competitor data

### 16. Possible UI
- Web dashboard (landscape case library + recall panel)
- CLI/API for quick "what did we say about sector X" queries
- Slack bot for async competitive-question lookups during deal discussions

### 17. Possible Backend/Agent Architecture
```
Sector/Company Query → Agent (LLM + Tools)
    ↓
Hindsight Recall (sector signature + adjacency match)
    ↓
Agent Reasoning (prior thesis + accuracy + staleness flag)
    ↓
Current Evidence Check (analyst verifies against recent news/data)
    ↓
Verification (analyst confirms/updates relevance)
    ↓
Hindsight Retain (updated landscape case; thesis resolution update)
```

### 18. MVP Scope
- Single geography/vertical cluster (e.g., Indian vertical SaaS)
- No live competitor-data API integration — structured case data pre-seeded in Hindsight
- Web or CLI interface
- 10 seed landscape cases, several with resolved outcomes
- Recall → propose → verify → retain loop
- Basic similarity matching (sector/segment tags + adjacency)

### 19. Nice-to-Have Features
- Automated staleness scoring based on sector "clock speed" (fast-moving vs. slow-moving markets)
- Tracxn/Crunchbase auto-ingest for competitor and funding updates
- Thesis-accuracy leaderboard across analysts/sectors
- Portfolio-company competitive alerting (new entrant detected in a tracked sector)
- Cross-team shared memory namespace with attribution

### 20. Main Technical Risks
| Risk | Mitigation |
|------|------------|
| Hindsight API limitations for structured recall | Verify API early; design fallback |
| Sector adjacency matching is fuzzier than exact signatures | Combine structured tags + semantic embedding on sector description |
| Seed data quality and representativeness | Curate realistic synthetic landscape cases with clear resolved/unresolved split |
| Thesis resolution often subjective | Require explicit accuracy rationale, not just a binary label |

### 21. Main Product Risks
| Risk | Mitigation |
|------|------------|
| Analysts don't trust an old thesis over fresh research | Frame as a calibration input, not a replacement for current research |
| "Just search Crunchbase" mentality | Demonstrate the thesis-accuracy loop that no data provider offers |
| Fast-moving sectors make old maps look irrelevant | Explicit staleness warning tied to sector clock-speed, not just raw age |

### 22. How the Idea Demonstrates Hindsight
- Memory is the **core differentiator**: without it, agent = generic web-search summarizer
- Visible learning: a falsifiable thesis from months ago is resolved and changes the next research pass
- Structured memory schema separates prediction from resolution, enabling real calibration
- Retention loop is explicit and auditable (thesis now, accuracy update later)

### 23. What Would Make It Look Generic
- Chat interface that just "remembers past questions"
- No structured thesis/resolution schema, just free-text summaries
- No staleness or accuracy signal (treats every recalled map as current)
- Demo shows only "found a past landscape," not a corrected blind spot

### 24. How to Avoid Becoming a Generic Chatbot
- **Structured memory schema** with prediction + resolution as first-class fields
- **Explicit staleness warning** tied to sector clock-speed
- **Thesis-accuracy tracking** as a first-class concept, not an afterthought
- **Evidence-linked memories** (sources, dates, resolution basis)
- **Demo shows a corrected blind spot**, not just a faster search

---

## Cross-Idea Observations

| Aspect | Idea 1 (Debugging) | Idea 2 (Evaluation) | Idea 3 (Incident) | Idea 4 (Deal Intelligence) | Idea 5 (Competitive Intelligence) |
|--------|-------------------|---------------------|-------------------|----------------------------|-------------------------------------|
| **Domain** | Hardware/software engineering | AI/ML evaluation | DevOps/SRE | Venture capital deal diligence | Venture capital / market research |
| **Persona** | Engineer | Evaluator | On-call engineer | VC analyst / partner | VC analyst / partner |
| **Memory Unit** | Debug case | Evaluation case | Incident case | Deal case | Landscape case |
| **Key Behavior Change** | Investigation priority | Scoring consistency | Hypothesis ranking | Diligence question priority | Research priority + thesis calibration |
| **Verification Gate** | Current env vs past env | Evaluator agrees/disagrees | Engineer verifies root cause | Analyst verifies against current deal data | Analyst verifies against current market data |
| **Learning Visibility** | Investigation workflow comparison (with/without memory) | Agreement metric (κ) comparison | Time-to-resolve comparison (with/without memory) | Diligence question quality comparison (with/without memory) | Thesis-accuracy comparison (with/without memory) |
| **Data Availability** | Team 1 has VLSI background | Team 1 has eval background | Generalizable, public datasets exist | Requires synthetic-but-realistic deal data | Requires synthetic-but-realistic landscape data |
| **Design Principle Alignment** | High (memory informs, evidence verifies) | Medium (calibration memory, evidence verifies) | Medium (incident memory, evidence verifies) | High (memory informs diligence, evidence verifies, outcome closes the loop) | High (thesis informs research, evidence verifies, resolution closes the loop) |