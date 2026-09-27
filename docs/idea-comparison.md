# Idea Comparison Matrix

**Status:** Neutral evaluation — no winner declared  
**Purpose:** Support team discussion and decision  
**Reference:** [Project Ideas](project-ideas.md) | [System Design Notes](system-design-notes.md) | [Demo Concepts](demo-concepts.md)

---

## Decision Matrix

| Criterion | Idea 1: Engineering Debugging | Idea 2: AI Evaluation | Idea 3: Engineering Incident | Idea 4: Deal Intelligence | Idea 5: Competitive Intelligence | Reasoning |
|-----------|------------------------------|----------------------|-----------------------------|---------------------------|----------------------------------|-----------|
| **Real-world problem clarity** | High | High | High | High | High | All five address documented, painful professional workflows with clear stakeholders |
| **Target-user clarity** | High | High | High | High | High | Each has a specific, identifiable persona with distinct workflow |
| **Hindsight centrality** | High | High | High | High | High | Memory is the core differentiator in all five; without it, agent = generic assistant |
| **Memory-driven behavior change** | High | High | High | High | High | All show: recall → changed investigation priority/decision → verification → retention |
| **Demo clarity** | High | Medium | High | High | High | Debugging/Incident/Deal/Competitive have clear workflow demos; Evaluation needs metric visualization |
| **Learning-curve visibility** | High | Medium | High | High | High | Debugging/Incident/Deal/Competitive: side-by-side workflow comparison visible |
| **Technical feasibility** | Medium | Medium-High | Medium | Medium | Medium | All feasible; Debugging needs report parsing; Evaluation needs similarity matching; Incident needs log ingestion; Deal/Competitive need synthetic data |
| **Implementation complexity** | Medium | Medium | Medium | Medium | Medium | Similar complexity; each has domain-specific data parsing/structuring needs |
| **Data availability** | Medium | Medium | High | Medium | Medium | Incident: public datasets. Debugging/Evaluation: Team 1 background. Deal/Competitive: synthetic-but-realistic needed |
| **UI complexity** | Low-Medium | Medium | Low-Medium | Medium | Low-Medium | Debugging/Incident/Competitive: CLI/Slack. Evaluation/Deal: side-by-side UI for recall panel |
| **Portfolio value** | High | High | High | High | High | All portfolio-worthy across different domains |
| **Innovation potential** | High | Medium-High | Medium | High | High | Debugging: novel hardware timing. Evaluation: calibration memory. Deal/Competitive: decision-to-outcome loop novel in VC |
| **Risk** | Medium | Medium | Medium | Medium | Medium | Debugging: proprietary data. Evaluation: subjectivity. Incident: high-stakes trust. Deal/Competitive: confidentiality perception |
| **Scope control** | High | High | High | High | High | All naturally scoped to one domain, one workflow |
| **Compelling 2–5 min demo** | High | Medium | High | High | High | Debugging/Incident/Deal/Competitive: clear before/after workflow. Evaluation: needs metric design |

---

## Detailed Reasoning by Criterion

### Real-World Problem Clarity — All High
- **Debugging:** Engineers waste hours re-investigating known failure modes. Well-documented in semiconductor, embedded, backend.
- **Evaluation:** Inconsistent evaluation leads to bad model deployments. Known pain point for LLM application teams.
- **Incident:** On-call engineers lack context at 3 AM. Repeated incidents cost downtime. Universal SRE problem.
- **Deal Intelligence:** VCs lose institutional knowledge across deal flow; analysts re-derive diligence from scratch; outcomes rarely linked back to original decisions.
- **Competitive Intelligence:** Competitive landscapes rebuilt from scratch every time; past theses never checked for accuracy; same blind spots repeat across deals.

### Target-User Clarity — All High
- **Debugging:** VLSI/timing engineer, backend debugger, firmware engineer.
- **Evaluation:** AI evaluator, RLHF annotator, LLM app developer.
- **Incident:** SRE, DevOps, platform engineer, on-call.
- **Deal Intelligence:** VC associates/analysts screening deals, Partners preparing IC memos, venture studios.
- **Competitive Intelligence:** VC associates researching sectors, Partners doing portfolio check-ins, platform teams tracking competitive sets.

### Hindsight Centrality — All High
Each idea fails without memory:
- Debugging agent without memory = generic checklist generator
- Evaluation agent without memory = rubric display tool
- Incident agent without memory = runbook search tool
- Deal Intelligence agent without memory = generic diligence checklist
- Competitive Intelligence agent without memory = web search summarizer

### Memory-Driven Behavior Change — All High
| Idea | Without Memory | With Memory | Behavior Change |
|------|----------------|-------------|-----------------|
| Debugging | Generic investigation order | Prioritized by past success | Skip failed approaches; jump to verified root cause |
| Evaluation | Isolated scoring | See past scores + reasoning + calibration | Align with team consistency; recall failure patterns |
| Incident | Generic runbook | Ranked hypotheses + failed fixes | Skip known-failed fixes; apply verified resolution faster |
| Deal Intelligence | Generic diligence checklist | Diligence prioritized by what caught similar deals | Red flags weighted by historical outcome; partner memory democratized |
| Competitive Intelligence | Rebuild landscape from zero | Start from last map, verify what changed | See which signals predicted winners; correct recurring blind spots |

### Demo Clarity — Debugging/Incident/Deal/Competitive High, Evaluation Medium
- **Debugging:** Side-by-side investigation workflow comparison is visceral. Visual: timing reports side-by-side.
- **Incident:** Side-by-side resolution workflow comparison. Visual: alert timeline, different engineer resolves faster.
- **Deal Intelligence:** Side-by-side diligence workflow comparison. Visual: red-flag pattern recalled, distinguishing question surfaced.
- **Competitive Intelligence:** Side-by-side research workflow comparison. Visual: prior thesis accuracy revealed, blind spot corrected.
- **Evaluation:** Needs to show consistency metric comparison (Cohen's κ, agreement %) not just time. Harder to visualize in 2 min.

### Learning-Curve Visibility — Debugging/Incident/Deal/Competitive High, Evaluation Medium
- **Debugging/Incident/Deal/Competitive:** Same problem class, different user/time, visibly different investigation/diligence/research path.
- **Evaluation:** Requires showing multiple evaluators or time-lapse calibration. More setup needed.

### Technical Feasibility — All Medium
- **Debugging:** Parsing PrimeTime/Innovus reports is non-trivial but structured. Team 1 has domain knowledge.
- **Evaluation:** Response similarity + rubric matching is known problem (embedding + structured). Team 1 has eval background.
- **Incident:** Log normalization is solved problem (OpenTelemetry, Loki). Public incident datasets exist.
- **Deal Intelligence:** Deal similarity fuzzier than engineering signatures; combine structured tags + semantic embedding. Synthetic data curation needed.
- **Competitive Intelligence:** Sector adjacency matching fuzzier; combine structured tags + semantic embedding. Synthetic data curation needed.

### Implementation Complexity — All Medium
| Component | Debugging | Evaluation | Incident | Deal Intelligence | Competitive Intelligence |
|-----------|-----------|------------|----------|-------------------|--------------------------|
| Memory schema | Domain-specific, structured | Domain-specific, structured | Domain-specific, structured | Domain-specific, structured | Domain-specific, structured |
| Recall logic | Signature + env match | Response embedding + rubric | Symptom signature + env | Deal signature + thesis + red-flag tags | Sector signature + adjacency match |
| Verification gate | Env diff check | Evaluator agree/disagree | Root cause verification | Analyst verifies deal data | Analyst verifies market data |
| Tool integration | Report parser | Rubric engine | Log/alert ingestion | None (pre-seeded) | None (pre-seeded) |
| Demo data | 10–15 seed cases | 20–30 seed evals | 8–12 seed incidents | 10–15 seed deals | 10–15 seed landscapes |

### Data Availability — Incident High, Debugging/Evaluation/Deal/Competitive Medium
- **Incident:** Public datasets (GitHub incident reports, sample Kubernetes logs, synthetic incident generators). No proprietary data needed.
- **Debugging:** Team 1 has VLSI background → can create realistic synthetic timing reports. Real reports may be proprietary.
- **Evaluation:** Team 1 has eval background → can create realistic prompt/response/rubric sets. Need diverse failure patterns.
- **Deal Intelligence:** Requires synthetic-but-realistic deal cases with clear red-flag patterns and outcomes. No live CRM integration for MVP.
- **Competitive Intelligence:** Requires synthetic-but-realistic landscape cases with resolved/unresolved outcomes. No live Crunchbase/Tracxn API for MVP.

### UI Complexity — Debugging/Incident/Competitive Low-Medium, Evaluation/Deal Medium
- **Debugging/Incident/Competitive:** Terminal-first workflow fits CLI/Slack. Low UI investment.
- **Evaluation/Deal Intelligence:** Requires side-by-side: response/deal + recall panel. More UI work for demo quality.

### Portfolio Value — All High
- **Debugging:** Shows domain expertise + agent architecture + structured memory design.
- **Evaluation:** Shows ML engineering + evaluation methodology + calibration systems.
- **Incident:** Shows SRE practices + observability + incident management.
- **Deal Intelligence:** Shows VC workflow understanding + decision-to-outcome memory design + calibration loop.
- **Competitive Intelligence:** Shows market research methodology + thesis-accuracy tracking + calibration loop.

### Innovation Potential — Debugging/Deal/Competitive High, Evaluation Medium-High, Incident Medium
- **Debugging:** Applying persistent memory to hardware timing debug is novel. Most prior art is software debugging.
- **Evaluation:** Calibration memory + failure pattern taxonomy is novel for LLM eval.
- **Incident:** Incident memory exists (incident.io, Rootly) but Hindsight-based agent with structured recall is differentiated.
- **Deal Intelligence:** Decision-to-outcome feedback loop is novel for VC; most tools stop at decision.
- **Competitive Intelligence:** Thesis-accuracy calibration loop is novel; data providers don't offer this.

### Risk — All Medium
| Idea | Top Risk | Mitigation |
|------|----------|------------|
| Debugging | Proprietary data / tool access | Synthetic-but-realistic seed data; one tool format |
| Evaluation | Subjectivity / evaluator bias | Explicit override tracking; measure agreement not accuracy |
| Incident | Trust in high-stakes domain | "Suggests, human verifies" UX; evidence links |
| Deal Intelligence | Confidentiality perception with real data | Synthetic seed data for demo; note access controls needed for real deployment |
| Competitive Intelligence | "Just search Crunchbase" mentality | Demonstrate thesis-accuracy loop no data provider offers |

### Scope Control — All High
Each idea naturally constrains to:
- One domain (timing / medical eval / DB exhaustion / vertical SaaS seed / Indian VC verticals)
- One workflow (debug / evaluate / respond / diligence / competitive research)
- One persona

### Compelling Demo — Debugging/Incident/Deal/Competitive High, Evaluation Medium
- **Debugging/Incident/Deal/Competitive:** Clear protagonist, clear antagonist (time/uncertainty), clear demonstration (memory changed the investigation/diligence/research path).
- **Evaluation:** Protagonist is evaluator, antagonist is inconsistency. Victory is consistency demonstration — less cinematic.

---

## Overlap Between Engineering Debugging and Incident Response

Ideas 1 (Engineering Debugging) and 3 (Engineering Incident) share architectural similarities but address distinct problems:

| Dimension | Engineering Debugging (Idea 1) | Engineering Incident (Idea 3) |
|-----------|-------------------------------|-------------------------------|
| **Target User** | Design/verification engineer (VLSI, embedded, firmware) | On-call engineer / SRE / DevOps |
| **Workflow** | Planned debugging session (hours/days) | Reactive incident response (minutes, high pressure) |
| **Problem Data** | Timing violations, signal integrity, functional failures | Service alerts, log anomalies, latency spikes |
| **Memory Unit** | Debug case (problem → investigation → fix) | Incident case (symptoms → root cause → resolution) |
| **Key Decision** | Investigation priority / approach selection | Root cause hypothesis / runbook selection |
| **Verification** | Environment match (tool, PDK, corner) | Version/config match (deploy, library, config) |
| **Demo Story** | Focused investigation with memory guidance vs extended investigation without | Subsequent engineer resolves using team memory vs first engineer without |
| **Time Pressure** | Low-medium (project timeline) | High (SEV-1/2, customer impact) |
| **Data Source** | EDA reports, simulation logs, code diffs | Monitoring alerts, application logs, deploy metadata |

**Shared Architecture:**
- Structured memory schema with problem signature, environment, failed/successful approaches
- Hindsight recall → application-ranked hypotheses → verification gate → retention loop
  (ranking is proposed application-level behavior; Hindsight recall capabilities to be verified)
- Cross-case learning (adjacent paths / cross-service patterns)

**Recommendation:** Keep them separate at the planning stage. The target user, workflow, data, and demo story are sufficiently different. The team should choose based on which persona and problem space they want to serve. The shared architecture means implementation patterns can transfer if the team pivots.

---

## Overlap Between Deal Intelligence and Competitive Intelligence

Ideas 4 (Deal Intelligence) and 5 (Competitive Intelligence) share a domain (venture capital) and persona
(analyst/partner) but address distinct workflows:

| Dimension | Deal Intelligence (Idea 4) | Competitive Intelligence (Idea 5) |
|-----------|---------------------------|-----------------------------------|
| **Workflow** | Deal diligence on a specific opportunity | Sector research across companies |
| **Problem Data** | Deal signature, red flags, reference notes | Competitor sets, moat theses, sources |
| **Memory Unit** | Deal case (diligence → decision → outcome) | Landscape case (thesis → resolution/accuracy) |
| **Key Decision** | Diligence question priority / invest-pass-watch | Research priority / thesis weighting |
| **Feedback Loop** | Decision resolves to outcome (raise, shutdown, exit) | Thesis resolves to accuracy (correct / incorrect + rationale) |
| **Verification** | Current deal data (metrics, references) | Current market data (funding news, launches) |
| **Staleness Concern** | Low (each deal is unique) | High (explicit staleness warning tied to sector clock-speed) |
| **Demo Story** | Recalled pattern sharpens the diligence question | Resolved prior thesis corrects a blind spot |

**Shared Architecture:**
- Structured memory schema with signature, evidence references, and a resolution/outcome field
- Hindsight recall → application-ranked candidates → verification gate → retention loop
- Outcome/resolution updates arrive after the initial retention (closing the loop for future recall)

**Recommendation:** Keep them separate at the planning stage. Diligence (Idea 4) and research
(Idea 5) are different workflows with different memory units and verification gates, even though
both close a decision-to-outcome loop. If the team selects the VC domain, both could plausibly
share one memory backend later — but that is an implementation decision, not a planning one.

---

## Questions for Team Discussion

**Strategic Alignment**
1. Which domain excites us more: hardware/software debugging, AI evaluation, incident response, VC deal diligence, or competitive research?
2. Which team member's background do we want to leverage more (Team 1 has VLSI + eval; Mukul contributed Ideas 4–5, preference TBD)?
3. Do we want a project that showcases Team 1's unique VLSI background (differentiator) or a more generalizable domain?

**Technical Execution**
4. What is Team 2's (Mukul's) technical background and preference?
5. Which data acquisition path is easiest: synthetic VLSI reports, curated eval sets, public incident logs, or synthetic deal/landscape cases?
6. Which Hindsight API capabilities are we most confident using? (Need to verify)
7. What's our realistic implementation capacity in hackathon timeframe?

**Demo & Portfolio**
8. Which demo story is most compelling to *us*?
9. Which project makes the strongest portfolio piece for each team member?
10. Can we produce a live demo (not just video) for the chosen idea?

**Hindsight Integration**
11. How will we structure memories in Hindsight for the chosen domain? (Schema design)
12. What retrieval strategy: semantic, structured filter, hybrid?
13. How will we handle memory versioning / staleness in the demo?

**Content & Submission**
14. Which idea yields the most interesting technical article for each member?
15. Are there any organizer constraints we haven't accounted for? (Check latest rules)

**Decision Process**
16. What's our decision deadline?
17. If we disagree, what's the tiebreaker?
18. When do we start implementation after decision?

---

## Recommendation: How to Use This Matrix

1. **Score each criterion for your team** — Weight criteria by your priorities
2. **Answer the discussion questions** — Record in [decision-log.md](decision-log.md)
3. **Identify deal-breakers** — Any "Low" on a must-have criterion?
4. **Prototype the riskiest part** — Before final decision, verify Hindsight API for top choice
5. **Decide and commit** — Record final decision in decision log with rationale

> **Reminder:** No winner declared here. This matrix enables informed discussion.