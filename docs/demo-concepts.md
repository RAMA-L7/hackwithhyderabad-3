# Demo Concepts — All Three Ideas

**Status:** Demo narratives for evaluation — not final demo plan  
**Reference:** [Project Ideas](project-ideas.md) | [System Design Notes](system-design-notes.md) | [Idea Comparison](idea-comparison.md)

---

## Demo Principles (All Ideas)

| Principle | Application |
|-----------|-------------|
| **Show, don't tell** | Live agent interaction, not slides |
| **Visible memory** | Show Hindsight UI or memory representation on screen |
| **Before/after contrast** | Same user, similar task, dramatically different outcome |
| **Verification gate visible** | Show agent saying "Check current env vs memory" |
| **Realistic data** | No "foo/bar" — real timing reports, real prompts, real alerts |
| **60-second hook** | Judge understands problem + solution in first minute |
| **No cherry-picking** | Demo path should be reproducible |

---

## Idea 1: Engineering Debugging Memory Agent

### Demo Narrative (90 seconds)

| Time | Scene | What's Visible |
|------|-------|----------------|
| 0–10s | **Hook** | Terminal shows timing violation: `Slack: -80ps (VIOLATION) on path clk_div2/u1→u2 @ SS 0.72V`<br>Text overlay: "Every VLSI engineer knows this feeling." |
| 10–40s | **First Debug (No Memory)** | Engineer types: `agent debug "setup -80ps on clk_div2/u1→u2, SS 0.72V, PrimeTime 2023.09"`<br>Agent: "No similar cases in memory. Standard investigation:"<br>1. Check clock skew<br>2. Check OCV derates<br>3. Check path grouping<br>4. Check hold violations<br>Engineer investigates → finds hold violation on adjacent path → fixes hold → setup passes<br>Agent: "Saving case to memory..." |
| 40–50s | **Memory Created** | Hindsight UI panel slides in:<br>```
Case: TIMING-2024-001
Problem: Setup -80ps clk_div2/u1→u2 @ SS 0.72V
Root Cause: Hold violation on clk_div2/u3→u4 causing pessimism
Fix: Buffer insertion on hold path
Evidence: before/after reports (links)
Failed: Margining setup directly → made hold worse
Tags: [setup, hold-pessimism, buffer-insertion, 7nm]
``` |
| 50–80s | **Second Debug (With Memory)** | Different terminal (or same, later): `agent debug "setup -65ps on clk_div2/u3→u4, SS 0.72V"`<br>Agent: "**Recalled 1 similar case** (TIMING-2024-001, 92% env match):<br>• Same corner, same clock domain, adjacent path<br>• Root cause: Hold violation caused setup pessimism<br>• Fix: Buffer on hold path → +120ps setup<br>• **Suggestion: Check hold on clk_div2/u3→u4 first**"<br>Engineer verifies hold violation → applies fix → setup passes<br>Investigation completed in minutes vs hours without memory |
| 80–90s | **Closing** | Split screen: **First debug: extended investigation** | **Second debug: focused investigation with memory guidance**<br>Text: "Memory didn't just remember. It changed *how* we debugged."<br>Hindsight logo + "Persistent memory for engineering agents" |

### Strongest "Before vs After" Moment
**The agent's first response:** Generic 4-step checklist vs. Targeted 1-step hypothesis with evidence link.

### What Should Be Visible On Screen
- Realistic timing violation data (or realistic synthetic)
- Agent CLI interaction (not chat bubbles)
- Hindsight memory entry with structured fields
- Engineer verification step
- Split-screen workflow comparison (with/without memory)

### What Should NOT Be Shown
- Agent writing code for the engineer
- "Magic" fix without verification step
- Memory retrieval without environment match check
- Unrealistic time claims (show actual timer)

---

## Idea 2: AI Evaluation Memory Agent

### Demo Narrative (90 seconds)

| Time | Scene | What's Visible |
|------|-------|----------------|
| 0–10s | **Hook** | Side-by-side: Two evaluators, same response, different scores (2 vs 4).<br>Text: "Evaluation inconsistency costs millions in bad deployments." |
| 10–40s | **First Evaluation (No Memory)** | Web UI: Prompt + Response + Rubric panel<br>Response: Medical summary claiming "Drug X reduces mortality by 50%" (source: 5%)<br>Evaluator reads, scores **2/5**, writes reasoning:<br>"Unfounded efficacy claim. Source says 5%."<br>Tags added: `[exaggerated-efficacy-claim, missing-risk-disclosure]`<br>Agent: "Saved to memory. Calibration updated." |
| 40–50s | **Memory Created** | Hindsight panel:<br>```
Eval: EVAL-2024-0451
Prompt: Medical summarization for patient
Response: Claims 50% mortality reduction (source: 5%)
Score: 2/5
Reasoning: {hallucination: "exaggerated efficacy", severity: "high"}
Tags: [exaggerated-efficacy-claim]
Calibration: Team v3.2: "Unfounded medical claims → 1-2"
Similar: 0 prior cases
``` |
| 50–80s | **Second Evaluation (With Memory)** | New response: "Treatment Y improves survival 40%" (source: 4%)<br>Evaluator opens → **Recall panel auto-opens**:<br>```
⚡ 3 Similar Cases Found
• EVAL-2024-0451: "50% mortality reduction" → Score 2/5
• EVAL-2024-0382: "70% symptom relief" → Score 1/5  
• EVAL-2024-0291: "90% cure rate" → Score 1/5

Pattern: [exaggerated-efficacy-claim] → Scores 1-2 (100% consistency)
Calibration Note: "Auto 1-2 for unfounded medical claims"
```<br>Evaluator: "Same pattern." → Scores **2/5** with consistent reasoning<br>Agent: "Inter-annotator agreement on this pattern: 1.0 (3/3 evaluators)" |
| 80–90s | **Closing** | Dashboard: **Pattern consistency comparison**<br>Chart: Inter-annotator agreement trend with and without memory-assisted calibration<br>Text: "Memory doesn't evaluate for you. It helps you evaluate *consistently*." |

### Strongest "Before vs After" Moment
**The recall panel appearing automatically** with pattern tag, score distribution, and calibration note — before evaluator even starts reasoning.

### What Should Be Visible On Screen
- Real prompt/response pair (medical domain)
- Side-by-side evaluator UI with recall panel
- Hindsight memory with structured reasoning + tags
- Consistency metric visualization (κ over time)
- Calibration note prominently displayed

### What Should NOT Be Shown
- Agent assigning score automatically
- "AI evaluates better than human" claim
- Generic "similar responses" without failure pattern taxonomy
- Toy prompts ("write a poem")

---

## Idea 3: Engineering Incident Memory Agent

### Demo Narrative (90 seconds)

| Time | Scene | What's Visible |
|------|-------|----------------|
| 0–10s | **Hook** | PagerDuty alert on phone: `SEV-2: payments-api 500 rate 8%`<br>Text: "3 AM. You're on-call. You have 5 minutes." |
| 10–40s | **First Incident (No Memory)** | Terminal: `agent incident "payments-api 500s 8%, DB pool exhausted, deploy v2.3.1"`<br>Agent: "No similar incidents. Standard runbook: DB Connection Exhaustion"<br>1. Check pool metrics<br>2. Check recent deploys<br>3. Scale replicas<br>4. Restart pods<br>Engineer runs checks → finds connection leak in new retry logic (commit abc123)<br>Rollback v2.3.1 → v2.3.0 → **Resolved after extended investigation**<br>Agent: "Saving incident to memory..." |
| 40–50s | **Memory Created** | Hindsight panel:<br>```
Incident: INC-2024-0451
Service: payments-api | SEV-2
Symptoms: [500s 8%, DB pool exhausted, p99 15s]
Trigger: Deploy v2.3.1 (commit abc123)
Root Cause: Connection leak in retry logic (new in v2.3.1)
Failed Fixes: Scale replicas (no effect), Increase pool size (OOM)
Successful Fix: Rollback to v2.3.0 (23 min to resolve)
Lessons: Retry logic needs circuit breaker; add pool metrics alert
Tags: [db-connection-pool, retry-loop, rollback, circuit-breaker]
``` |
| 50–80s | **Second Incident (With Memory)** | Different engineer, Slack: `/incident orders-api 500s 6%, DB pool exhausted, deploy v1.8.0`<br>Agent (in Slack): "**Recalled similar incident** (INC-2024-0451, payments-api):<br>• Same symptoms: DB pool exhaustion post-deploy<br>• Root cause: Connection leak in retry logic (same library `retry-lib v2.1`)<br>• Fix: Rollback worked (23 min)<br>• **Check: Does orders-api use retry-lib v2.1?**"<br>Engineer: `grep retry-lib package.json` → **Yes, v2.1**<br>Engineer: `/rollback orders-api v1.7.0` → **Resolved in minutes** |
| 80–90s | **Closing** | Timeline: **First incident: extended investigation (senior engineer)** | **Second incident: rapid resolution (junior engineer)**<br>Text: "Institutional memory transferred. No 3 AM learning curve." |

### Strongest "Before vs After" Moment
**Different engineer resolves rapidly** using memory from extended investigation — shows knowledge transfer across team.

### What Should Be Visible On Screen
- Real alert format (PagerDuty/Slack)
- Agent in Slack/CLI (chatops style)
- Hindsight incident with full timeline + root cause + failed fixes
- Cross-service pattern match (payments → orders)
- Time comparison with different personas

### What Should NOT Be Shown
- Agent executing rollback automatically
- "AI fixes production" narrative
- Memory recall without "Check current version" verification
- Fake Slack messages — use real Slack or realistic terminal

---

## Cross-Idea Demo Requirements

| Requirement | Debugging | Evaluation | Incident | Deal Intelligence | Competitive Intelligence |
|-------------|-----------|------------|----------|-------------------|--------------------------|
| **Realistic data** | Structured timing violation cases | Medical prompts/responses | PagerDuty alerts + logs | Structured deal cases (sector, red flags, outcomes) | Structured landscape cases (competitors, thesis, resolution) |
| **Memory UI** | Hindsight panel with case | Hindsight panel with eval + pattern | Hindsight panel with incident timeline | Hindsight panel with deal case + red flags | Hindsight panel with landscape + thesis accuracy |
| **Verification visible** | Env diff check | Evaluator agrees/disagrees | Version check before rollback | Analyst verifies deal data (domain co-founder) | Analyst verifies market data (execution signals) |
| **Metric shown** | Investigation workflow comparison (with/without memory) | Consistency comparison (with/without memory) | Resolution workflow comparison (with/without memory) | Diligence question quality comparison (with/without memory) | Thesis-accuracy comparison (with/without memory) |
| **Hindsight branding** | Memory panel shows Hindsight | Memory panel shows Hindsight | Memory panel shows Hindsight | Memory panel shows Hindsight | Memory panel shows Hindsight |

---

## Demo Production Checklist

- [ ] Record in one take (or minimal cuts)
- [ ] Show real terminal/web UI, not simulated typing
- [ ] Timer visible for time comparisons
- [ ] Hindsight memory panel clearly legible (zoom if needed)
- [ ] No sensitive data visible
- [ ] Audio narration explaining *why* memory changed behavior
- [ ] 2–5 minute final cut (per hackathon requirement)
- [ ] Live demo rehearsed separately from video

---

## Demo Risks by Idea

| Idea | Risk | Mitigation |
|------|------|------------|
| Debugging | No live EDA tool in demo | Pre-seeded structured cases; agent uses mocked tool interaction |
| Evaluation | Consistency metric needs multiple evaluators | Pre-compute κ from seed data; show trend |
| Incident | Cross-service demo complexity | Keep to two services; pre-verify library match |
| Deal Intelligence | Confidentiality perception with real data | Synthetic seed data for demo; note access controls needed for real deployment |
| Competitive Intelligence | "Just search Crunchbase" mentality | Demonstrate thesis-accuracy loop no data provider offers |

---

## Judge's 60-Second Takeaway (Per Idea)

| Idea | What Judge Should Understand |
|------|------------------------------|
| Debugging | "This agent learns from every debug session and makes the next engineer faster. Hindsight is the brain." |
| Evaluation | "This agent makes evaluation consistent across people and time. Hindsight stores the team's calibration." |
| Incident | "This agent gives every on-call engineer the team's collective incident memory. Hindsight is the institutional brain." |
| Deal Intelligence | "This agent transfers partner pattern-recognition to analysts. Hindsight closes the decision-to-outcome loop." |
| Competitive Intelligence | "This agent turns forgotten predictions into calibration. Hindsight tracks which signals actually predicted the winner." |

---

## Idea 4: Deal Intelligence Agent

### Demo Narrative (90 seconds)

| Time | Scene | What's Visible |
|------|-------|----------------|
| 0–10s | **Hook** | Pitch deck slide: vertical SaaS logistics, India, top 3 customers = 55% revenue, founder ex-consulting no domain background<br>Text: "Every VC has seen this pattern and forgotten what happened last time." |
| 10–40s | **First Deal (No Memory)** | Analyst types: `agent diligence "vertical SaaS logistics India seed $500K 55% concentration ex-consulting founder"`<br>Agent: "No similar deals in memory. Standard diligence checklist:"<br>1. Unit economics by lane<br>2. Customer concentration risk<br>3. Founder-market fit<br>4. Competitive moat<br>Analyst runs diligence, finds concentration + weak founder fit, decision: PASS<br>Agent: "Saving deal case to memory..." |
| 40–50s | **Memory Created** | Hindsight panel:<br>```
Deal: DEAL-2024-001
Signature: vertical SaaS logistics India seed
Red Flags: [customer-concentration 55%, weak-founder-fit]
Decision: PASS
Rationale: Concentration risk outweighed thesis fit
Evidence: data room excerpts, memo
Outcome: UNKNOWN (pending)
``` |
| 50–80s | **Second Deal (With Memory)** | New deal: `agent diligence "cold-chain logistics SaaS India seed $600K 60% concentration ex-consulting founder"`<br>Agent: "**Recalled similar deal** (DEAL-2024-001, 85% signature match):<br>• Same red flags: concentration + non-domain founder<br>• Previous decision: PASS on this combination<br>• **Key question missed last time: Does founder have domain co-founder/advisor?**"<br>Analyst checks — founder has strong domain co-founder this time<br>Decision: PROCEED to term sheet, notes distinguishing factor in memo |
| 80–90s | **Closing** | Side-by-side: **First deal: generic diligence, generic pass** | **Second deal: recalled pattern, sharpened question, different outcome with clear rationale**<br>Text: "Memory didn't just find a similar deal. It sharpened the diligence question."<br>Hindsight logo + "Persistent memory for investment decisions" |

### Strongest "Before vs After" Moment
**The recalled pattern surfaces the exact distinguishing question** (domain co-founder) that was missed in the first diligence — changing the decision with explicit rationale.

### What Should Be Visible On Screen
- Realistic pitch deck slide / deal brief
- Agent CLI or web interaction
- Hindsight memory entry with structured deal signature, red flags, decision, rationale
- Analyst verification step (checking for domain co-founder)
- Side-by-side workflow comparison (with/without memory)

### What Should NOT Be Shown
- Agent making the investment decision
- "AI invests better than human" claim
- Real confidential deal data (use synthetic)
- Memory recall without verification step

---

## Idea 5: Competitive Intelligence Agent

### Demo Narrative (90 seconds)

| Time | Scene | What's Visible |
|------|-------|----------------|
| 0–10s | **Hook** | Landscape slide from 14 months ago: "CompanyA wins on network effects + funding lead" (confidence: medium)<br>Text: "We build these every deal. We never check if we were right." |
| 10–40s | **First Research (No Memory)** | Analyst asks: `agent landscape "vertical SaaS trucking marketplaces India"`<br>Agent: "No prior landscape in memory. Building from scratch..."<br>Analyst researches: names CompanyA, CompanyB, CompanyC<br>Thesis: CompanyA wins on network effects + funding lead (confidence: medium)<br>Agent: "Saving landscape case to memory..." |
| 40–50s | **Memory Created** | Hindsight panel:<br>```
Landscape: LAND-2024-001
Sector: vertical SaaS trucking marketplaces India
Competitors: [CompanyA (Series B), CompanyB (Seed), CompanyC (bootstrapped)]
Thesis: CompanyA wins — network effects + funding lead
Confidence: medium
Sources: Crunchbase, Tracxn, product pages
Date: 2025-03-10
Resolution: PENDING
``` |
| 50–80s | **Second Research (With Memory)** | Analyst (14 months later): `agent landscape "cold-chain logistics SaaS India"`<br>Agent: "**Recalled adjacent landscape** (LAND-2024-001, 14 months old):<br>• Prior thesis: CompanyA wins on network effects + funding<br>• **Actual outcome: CompanyB won on price + local ops execution**<br>• Thesis accuracy: INCORRECT — underweighted execution speed<br>• **For this segment, consider execution-speed signals, not just funding/network effects**"<br>Analyst weights execution-speed and local-ops signals more heavily, avoids same overweighting mistake |
| 80–90s | **Closing** | Side-by-side: **First research: fresh thesis, no calibration** | **Second research: resolved prior thesis, corrected blind spot, sharper analysis**<br>Text: "Memory turned a forgotten prediction into a calibration tool."<br>Hindsight logo + "Persistent memory for competitive research" |

### Strongest "Before vs After" Moment
**The resolved prior thesis (now known incorrect) surfaces the specific blind spot** (underweighted execution speed) to correct in the new analysis — changing the research weighting, not just speeding it up.

### What Should Be Visible On Screen
- Realistic landscape slide / competitive map
- Agent CLI or web interaction
- Hindsight memory entry with competitors, thesis, confidence, sources, resolution/accuracy
- Analyst verification step (updating signal weights)
- Side-by-side workflow comparison (with/without memory)

### What Should NOT Be Shown
- Agent replacing current research
- "AI knows the market better" claim
- Real-time Crunchbase API (use pre-seeded synthetic data)
- Memory recall without staleness warning

---

## Demo Differentiation Checklist

| Element | Generic Chatbot | Our Demo |
|---------|----------------|----------|
| Memory structure | Chat history | Structured schema with causal fields |
| Recall trigger | "Remember X?" | Automatic on problem input |
| Recall output | "You said X" | Ranked hypotheses + evidence + failed approaches |
| Verification | None | Explicit env diff + human confirm |
| Behavior change | "I remember you like X" | "Skip step 3 — failed last time; do step 2 first" |
| Retention | Auto-save chat | Structured case + outcome + verification |
| Demo metric | "Felt faster" | Proposed workflow comparison (time / consistency / transfer) |