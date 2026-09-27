# Decision Log

**Status:** Active — update throughout planning and implementation  
**Purpose:** Track decisions, rejected ideas, assumptions, and open questions

---

## Decisions

| Date | Decision | Rationale | Author(s) |
|------|----------|-----------|-----------|
| 2026-09-27 | Removed all unverified performance claims (time savings, κ improvements) from planning docs | Claims presented as measured results without evidence; replaced with "proposed evaluation" and workflow comparison language | Review |
| 2026-09-27 | Separated Hindsight verified capabilities from proposed application-level behavior | Many architecture assumptions were presented as Hindsight features; now explicitly marked "to verify" or "proposed application-level behavior" | Review |
| 2026-09-27 | Clarified EGER as background inspiration only, not a validated framework | EGER references were ambiguous; now explicitly stated that project does not validate or depend on EGER | Review |
| 2026-09-27 | Made Engineering Debugging MVP independent of live EDA tool integration | MVP now uses pre-seeded structured cases; PrimeTime/Innovus parsing moved to optional extension | Review |
| 2026-09-27 | Added overlap analysis between Ideas 1 and 3 in comparison doc | Ideas share architecture but serve different personas/workflows; documented to inform team decision | Review |
| 2026-09-27 | Added core design question "What changes because the agent remembers?" to README and system design notes | Central evaluation criterion for memory centrality; distinguishes strong vs weak memory patterns | Review |
| 2026-09-27 | Added verification section to hackathon-requirements.md | Lists all Hindsight API capabilities and hackathon rules requiring verification before implementation | Review |
| 2026-09-27 | Added Ideas 4 (Deal Intelligence) and 5 (Competitive Intelligence) as candidates | VC domain leverages decision-to-outcome feedback loops; distinct from engineering domains; strong Hindsight fit for calibration/pattern-recognition | Review |
| 2026-09-27 | LLM + language decision: Python; primary OpenRouter Space Bunny Alpha; fallback DeepSeek V4.1 Flash via Baseten; provider-independent adapter required | Python has a verified Hindsight client; primary is free/1M-context (stability uncertain — hence mandatory fallback); fallback is OpenAI-compatible with built-in reasoning/tool calling; agent must talk only to the adapter so provider swaps touch no workflow logic | Rama + Mukul |
| 2026-09-27 | Phase 0 confirmed: LLM behind adapter/router only; all provider config from environment (no hardcoded keys); failover on timeout/unavailable/retry-exhaustion; auth errors must NOT failover; structured-output failures must NOT degrade to unvalidated text; do not over-engineer the provider layer before Sept 29; Hindsight stays the core memory system and is unaffected by LLM-provider choices | Team decision; provider layer is replaceable infrastructure and must not be able to alter `MemoryStore` or the investigation workflow | Rama + Mukul |
| 2026-09-27 | Phase 1 flow confirmed: User Case → Input Normalizer → Investigation Orchestrator → Hindsight MemoryStore (recall+matching) → Evidence Comparison → Hypothesis Generation → Engineer Verification → Resolution Proposal → Selective Memory Retention; LLM calls only via LLM Router → Primary Adapter ↘ Fallback Adapter | Matches `docs/domain-neutral-system-design.md` and `docs/architecture-review.md`; no architectural contradiction found | Rama + Mukul |

---

## Rejected Ideas

| Idea | Date Rejected | Reason |
|------|---------------|--------|
| | | |
| | | |
| | | |

---

## Assumptions

| ID | Assumption | Validation Plan | Status |
|----|------------|-----------------|--------|
| A1 | Hindsight supports structured memory schema with custom fields | Verify against Hindsight docs / API | Unverified |
| A2 | Hindsight supports hybrid recall (structured filter + semantic search) | Verify against Hindsight docs / API | Unverified |
| A3 | Team 2 (Mukul) has background compatible with at least one idea | Discuss with Mukul | Unverified |
| A4 | We can create realistic synthetic seed data for chosen domain | Prototype data generation | Unverified |
| A5 | Hackathon allows pre-seeded memory (not requiring live learning only) | Check rules / ask organizers | Unverified |
| A6 | Demo video can show pre-recorded tool outputs (not live API calls) | Check rules | Unverified |
| A7 | Two-person team can build MVP in hackathon timeframe | Scope to MVP definition in project-ideas.md | Unverified |
| A8 | Hindsight API rate limits sufficient for demo usage | Verify limits | Unverified |
| A9 | No EGER dependency — "Memory informs. Evidence verifies. Agent proposes." is an architectural design principle inspired by personal research, not a validated framework | Confirm with team | Unverified |
| A10 | Organizers evaluate technical article on Hindsight usage, not hackathon narrative | Check content guidelines | Unverified |
| A11 | Deal Intelligence: synthetic deal cases can capture realistic red-flag patterns and outcomes | Curate 10-15 cases with clear patterns | Unverified |
| A12 | Competitive Intelligence: synthetic landscape cases can capture thesis-accuracy patterns | Curate 10-15 cases with resolved/unresolved split | Unverified |
| A13 | VC domain ideas (4, 5) don't require live CRM/data API for MVP | Confirm pre-seeded approach sufficient for demo | Unverified |
| A14 | Confidentiality perception risk for VC ideas can be managed with synthetic data | Demo uses only synthetic cases; real deployment needs access controls | Unverified |

---

## Open Questions

| ID | Question | Owner | Target Date |
|----|----------|-------|-------------|
| Q1 | What is Team 2 (Mukul)'s technical background and domain preference? | Both | ASAP |
| Q2 | What are the exact Hindsight API capabilities (schema, recall, auth)? | Team 1 | Before decision |
| Q3 | What is the exact hackathon submission deadline and requirements? | Both | ASAP |
| Q4 | Which idea has the strongest seed data we can prepare pre-hackathon? | Both | Before decision |
| Q5 | Can we use Team 1's VLSI background for proprietary-adjacent realistic data? | Team 1 | Before decision |
| Q6 | What's the tiebreaker if team disagrees on final idea? | Both | Before decision |
| Q7 | Do we need a live deployed demo or is local runnable sufficient? | Check rules | ASAP |
| Q8 | What are the exact content requirements for technical article? | Check rules | ASAP |
| Q9 | Can we use any LLM provider or must we use specific one? | Check rules | ASAP |
| Q10 | Is there a Hindsight support channel during hackathon? | Check Discord/organizers | ASAP |
| Q11 | What's our target start date for implementation after decision? | Both | At decision |
| Q12 | How do we split implementation work between two people? | Both | At decision |
| Q13 | Which idea produces the strongest technical article for each member? | Both | At decision |
| Q14 | Do we need to demonstrate learning *during* hackathon or can memory be pre-seeded? | Check rules | ASAP |
| Q15 | What's the minimum viable demo for each idea? | Both | Before decision |
| Q16 | Can we source or create realistic synthetic deal/landscape cases for Ideas 4/5? | Team 1 | Before decision |
| Q17 | Which domain (engineering vs VC) produces stronger portfolio pieces for each member? | Both | At decision |
| Q18 | Do Ideas 4/5 have stronger "visible learning curve" demos than Ideas 1-3? | Both | At decision |
| Q19 | How to handle confidentiality concerns if judges ask about real data? | Both | Before demo |

---

## Team Discussion Notes

### Discussion 1: 2026-09-27 — Planning Repository Review
**Attendees:** Team Member 1 (reviewer), Team Member 2 (Mukul - to review)
**Topics Covered:**
- Removed unverified performance claims from all documents
- Audited Hindsight technical assumptions
- Clarified EGER boundary
- Made Engineering Debugging MVP EDA-tool-agnostic
- Added overlap analysis between Ideas 1 and 3
- Strengthened core design question
- Added verification checklist
**Key Points:**
- All five ideas remain candidates — no final decision
- Hindsight API capabilities must be verified before implementation
- Team 2 (Mukul) background and preference still needed
- Decision deadline and tiebreaker process still needed
**Action Items:**
- Team 2 to review all documents and provide background/preference
- Verify Hindsight API against official docs
- Confirm hackathon submission rules with organizers
- Team discussion to select final project

---

### Discussion 2: 2026-09-27 — Added Ideas 4 (Deal Intelligence) and 5 (Competitive Intelligence)
**Attendees:** Team Member 1
**Topics Covered:**
- Added two VC-domain candidates leveraging decision-to-outcome feedback loops
- Deal Intelligence: remembers diligence red flags, decisions, outcomes
- Competitive Intelligence: remembers competitive landscapes, thesis accuracy
- Both use pre-seeded synthetic data (no live CRM/API for MVP)
- Strong Hindsight fit: calibration loops, pattern recognition, outcome tracking
**Key Points:**
- VC domain distinct from engineering domains (Ideas 1-3)
- Ideas 4/5 have high innovation potential (decision-to-outcome loop novel)
- Data availability requires synthetic curation (like Ideas 1/2)
- Portfolio value strong for different skill demonstration
**Action Items:**
- Team 2 to review new ideas and provide preference
- Curate synthetic deal/landscape seed cases if VC domain selected

---

### Discussion 3: [Date]
**Attendees:**
**Topics Covered:**
**Key Points:**
**Action Items:**

---

### Discussion 2: [Date]
**Attendees:**
**Topics Covered:**
**Key Points:**
**Action Items:**

---

### Discussion 3: [Date]
**Attendees:**
**Topics Covered:**
**Key Points:**
**Action Items:**

---

## Final Project Decision

**Date Decided:** 2026-09-27
**Selected Idea:** Idea 1 — Engineering Debugging Agent with persistent memory using Hindsight
**Decision Makers:** Rama Krishna Ketha, Mukul Rai (joint team decision; do not change unless
both members explicitly agree)
**Rationale:**
- Strongest team-domain fit: Rama's VLSI/engineering-debugging background directly supports
  realistic seed-case authorship.
- Clear, narrow workflow (one debugging workflow, one engineer persona) with a visible
  before/after memory demonstration.
- Core loop (retain debug cases → recall by signature + tags → verify against current
  environment → retain outcome) maps to verified Hindsight capabilities; no dependency on
  unverified capabilities once the case schema is treated as an application-level convention.
- MVP requires no live EDA tool integration (pre-seeded structured cases).
- Ideas 2 (AI Evaluation), 3 (Engineering Incident), 4 (Deal Intelligence), and 5 (Competitive
  Intelligence) were considered but not selected; their analyses are preserved in
  `docs/project-selection-analysis.md` and `docs/idea-comparison.md` for reference.
**Hindsight role:** Persistent memory is a core architectural requirement, not an add-on.
**EGER boundary:** EGER is Rama's personal research and remains background design inspiration
only. This project does not validate EGER, does not depend on EGER, and EGER is not a
hackathon requirement.
**MVP Scope Agreed:** See `docs/final-project-definition.md` (MVP Scope) and
`docs/implementation-plan.md`. Single debugging workflow, single persona, single memory loop,
pre-seeded cases, CLI-first interface.
**Implementation Owner Split:** Proposed only — see `docs/team-task-split.md`. To be confirmed
by both members before implementation starts.
**Demo Concept Agreed:** Before/after memory demonstration per `docs/final-project-definition.md`
(Demo Story); no numerical improvement claims.
**Content Plan:** Per hackathon requirements (1 technical article + 1 social post per member,
1 team demo video). Topics to be chosen after MVP scope is built; no content drafted yet.

---

## Post-Decision Tracking

| Milestone | Target Date | Owner | Status |
|-----------|-------------|-------|--------|
| Hindsight API verified | | | |
| Seed data prepared | | | |
| Memory schema finalized | | | |
| Recall engine implemented | | | |
| Verification gate implemented | | | |
| Agent reasoning loop working | | | |
| Tool adapters integrated | | | |
| UI (CLI/Slack/Web) functional | | | |
| End-to-end demo working | | | |
| Demo video recorded | | | |
| Technical articles drafted | | | |
| Social posts drafted | | | |
| GitHub repo polished | | | |
| Submission complete | | | |