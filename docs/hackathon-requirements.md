# Hackathon Requirements — HackwithHyderabad 3.0

**Source:** Official hackathon communications, Hindsight documentation, organizer emphasis  
**Status:** Extracted and organized — *verify against latest official sources*

---

## Required Technology

| Requirement | Details |
|-------------|---------|
| **Core Technology** | Hindsight by Vectorize (persistent memory layer for AI agents) |
| **Official Site** | https://hindsight.vectorize.io/ |
| **GitHub** | https://github.com/vectorize-io/hindsight |
| **Integration** | Hindsight must be the persistent memory layer — central to the workflow |

> **To verify against current Hindsight documentation:** Exact API surface, memory schema options, retrieval mechanisms, authentication, rate limits, supported languages/SDKs.

---

## Judging Criteria (Official Weights)

| Criterion | Weight | Organizer Emphasis |
|-----------|--------|-------------------|
| Innovation | 30% | Novel application of persistent memory; not a generic chatbot |
| Use of Hindsight Memory | 25% | Memory is central to the solution; visible learning curve |
| Technical Implementation | 20% | Code quality, architecture, proper Hindsight integration |
| User Experience | 15% | Clear workflow, intuitive for target persona |
| Real-world Impact | 10% | Solves a genuine professional problem |

---

## Project Expectations (From Organizers)

| Expectation | Description |
|-------------|-------------|
| **Real business/professional problem** | Not a toy demo; targets a specific professional workflow |
| **Hindsight centrality** | Memory drives behavior change, not just personalization |
| **Visible learning curve** | Demonstrate before/after: agent improves with experience |
| **Before/after experience** | Show same/similar task handled differently after memory retention |
| **Tight scope** | One workflow, one persona, one clear value proposition |
| **Realistic data** | No synthetic toy data; use realistic scenarios |
| **Portfolio-worthy** | Professional quality suitable for public showcase |

---

## Demo Expectations

| Requirement | Details |
|-------------|---------|
| **Format** | 2–5 minute demo video |
| **Content** | Live demonstration of the agent workflow |
| **Key moment** | Visible "before memory vs after memory" comparison |
| **Narrative** | Problem → Investigation → Memory retention → Recall → Changed behavior → Verification → Outcome |

---

## Submission Requirements

| Deliverable | Required |
|-------------|----------|
| GitHub repository | Yes |
| Demo video (2–5 min) | Yes |
| Live demo (deployed or runnable) | Yes |
| Technical article (per team member) | Yes |
| Social media post (per team member) | Yes |

---

## Content Requirements

| Item | Quantity | Focus |
|------|----------|-------|
| Technical article | 1 per member (2 total) | Actual technical project + Hindsight usage |
| Social media post | 1 per member (2 total) | Project highlights, not generic hackathon posturing |
| Demo video | 1 per team | 2–5 minutes, shows memory-driven behavior change |

> **Note:** Content should present the project as a genuine technical accomplishment, not a "hackathon project."

---

## Important Constraints

| Constraint | Implication |
|------------|-------------|
| Hindsight must be central | Cannot treat memory as optional add-on |
| One workflow, one persona | Avoid feature creep; focus on single value prop |
| Memory ≠ proof | Agent must verify current conditions against recalled memory |
| No EGER dependency | EGER is personal research inspiration only |
| No false claims | Don't claim memory automatically improves accuracy without evidence |

---

## Deadline Information

> **To verify:** Exact submission deadline, demo video deadline, live demo requirements, timezone, submission platform.

---

## Hindsight-Related Requirements (Our Interpretation)

| Requirement | Rationale | Status |
|-------------|-----------|--------|
| Use Hindsight as the persistent memory layer | Core hackathon requirement | Verified requirement |
| Store structured memories (not just chat history) | Enables precise retrieval and reasoning | Proposed application-level behavior |
| Demonstrate retrieval → reasoning → verification → retention loop | Shows memory driving behavior | Proposed application-level behavior |
| Handle memory lifecycle (relevance, conflicts, staleness) | Production-grade memory management | Proposed application-level behavior |
| Show memory schema design for the domain | Domain-specific memory structure | Proposed application-level behavior |

---

## Separation: Facts vs. Interpretation

### FACTS FROM ORGANIZER
- Hackathon name: HackwithHyderabad 3.0
- Required technology: Hindsight by Vectorize
- Judging weights: Innovation 30%, Hindsight 25%, Technical 20%, UX 15%, Impact 10%
- Submission: GitHub repo, demo video, live demo, 2 articles, 2 social posts
- Organizer emphasis: real problem, Hindsight central, visible learning, before/after, tight scope, realistic data, portfolio quality

### OUR INTERPRETATION
- "Hindsight central" = memory retrieval must visibly change agent reasoning/action
- "Visible learning curve" = demo must show same task handled differently after memory exists
- "Realistic data" = scenarios drawn from actual engineering/evaluation/incident workflows
- "Portfolio-worthy" = code quality, documentation, and demo at professional standard
- "Memory ≠ proof" = architecture must separate recalled experience from current verification
- Hindsight API details: *to verify against current documentation*
- EGER (Evidence-Grounded Engineering Reasoning) is personal research by Team Member 1. It serves only as background inspiration for the architectural principle "Memory informs. Evidence verifies. Agent proposes." The hackathon project does not validate EGER, does not depend on EGER, and EGER is not an established framework.

---

## Verification Required Before Implementation

The following items must be verified against official Hindsight documentation and hackathon organizer communications before implementation begins.

### Hindsight API Capabilities (Verify against https://github.com/vectorize-io/hindsight)

- [ ] Authentication / authorization model
- [ ] Memory write API: schema support, batching, idempotency
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

### Hackathon Submission Rules (Verify with organizers)

- [ ] Exact submission deadline and timezone
- [ ] Live demo requirements: deployed URL, local runnable, or both
- [ ] Demo video format requirements (resolution, hosting platform, duration)
- [ ] Judging process: live demo + video, or video only
- [ ] Pre-seeded memory allowed vs. live-learning-only requirement
- [ ] Restrictions on additional technologies (LLM providers, frameworks, databases)
- [ ] Team size limits
- [ ] Hindsight team technical support availability during hackathon
- [ ] Content requirements detail (article length, social format, language)

### Technical Architecture Decisions (Team to decide after verification)

- [ ] Memory schema design for chosen domain
- [ ] Retrieval strategy: semantic, structured filter, hybrid
- [ ] Memory versioning / staleness handling approach
- [ ] LLM provider selection
- [ ] Deployment target (local, cloud, edge)

> **Note:** Do not proceed with implementation until Hindsight API capabilities are verified. The architecture depends on what the API actually supports.

---

## Open Questions for Organizers

1. Exact submission deadline and timezone?
2. Live demo requirements: deployed URL, local runnable, or both?
3. Any restrictions on additional technologies (LLM providers, frameworks, databases)?
4. Team size limits? (We are 2)
5. Demo video format requirements (resolution, hosting platform)?
6. Will Hindsight team provide technical support during hackathon?
7. Are there example projects from previous years?
8. Judging process: live demo + video, or video only?