# HackwithHyderabad 3.0 — Pre-Planning Repository

**Status:** Project selected — Engineering Debugging Agent (implementation not started)\
**Hackathon:** HackwithHyderabad 3.0\
**Required Technology:** [Hindsight by Vectorize](https://hindsight.vectorize.io/)\
**Team:** 2 members (see [Team Status](#team-status))\
**Purpose:** Shared planning documentation for team discussion before implementation

***

## Purpose of This Repository

This repository contains structured pre-planning documents to help the team:

* Understand hackathon requirements and constraints

* Evaluate five candidate project ideas objectively

* Compare ideas against judging criteria and technical feasibility

* Align on system-design principles before writing code

* Plan demo narratives for each candidate

* Track decisions, assumptions, and open questions

**Important:** The team has selected Idea 1 — the Engineering Debugging Agent with persistent
memory using Hindsight (decided 2026-09-27 by Rama Krishna Ketha and Mukul Rai; see
[docs/decision-log.md](docs/decision-log.md)). Implementation has not started yet — the
repository is in architecture and implementation-planning phase. Full definition:
[docs/final-project-definition.md](docs/final-project-definition.md). The remaining four ideas
stay documented for reference.

***

## Current Candidate Ideas

| # | Idea                                   | One-Line Concept                                                                                       |
| - | -------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| 1 | **Engineering Debugging Memory Agent** | Remembers past technical problems, investigations, and solutions to accelerate future debugging        |
| 2 | **AI Evaluation Memory Agent**         | Remembers past LLM evaluation cases, reasoning, and failure patterns to improve evaluation consistency |
| 3 | **Engineering Incident Memory Agent**  | Remembers past incidents, root causes, and resolutions to speed up incident response                   |
| 4 | **Deal Intelligence Agent**            | Remembers past VC deal evaluations — thesis fit, diligence findings, red flags, decisions, outcomes — to sharpen future diligence |
| 5 | **Competitive Intelligence Agent**     | Remembers past competitive landscape maps — competitor sets, moat theses, accuracy — to calibrate future research |

Detailed breakdown: [docs/project-ideas.md](docs/project-ideas.md)\
Comparison matrix: [docs/idea-comparison.md](docs/idea-comparison.md)

***

## Team Status

**Team Member 1**\
Background: VLSI Physical Design, Engineering workflows, AI evaluation, RLHF, Prompt engineering, LLMs, RAG, AI agents, Technical research\
Personal research: EGER (Evidence-Grounded Engineering Reasoning) — *background inspiration only, not a validated framework*

**Team Member 2**\
Name: Mukul\
Background: To be documented after discussion

***

## Planned Workflow

1. **Review this documentation** — Both members read all documents
2. **Team discussion** — Answer questions in [docs/decision-log.md](docs/decision-log.md)
3. **Select final project** — Record decision in decision log
4. **Implementation planning** — Create detailed spec, architecture, task breakdown
5. **Build** — Implement with Hindsight as central memory layer
6. **Demo preparation** — Record 2–5 minute demo video
7. **Content creation** — Technical article + social post per member
8. **Submission** — GitHub repo, live demo, video, content deliverables

***

## Official Hindsight Resources

* Website: <https://hindsight.vectorize.io/>

* GitHub: <https://github.com/vectorize-io/hindsight>

* Documentation: Refer to official docs for current API and capabilities

***

## Run the Phase 1 agent

The pipeline is integrated on `integration/phase1`. It is stdlib-only apart from `hindsight-client`.

```bash
# 1. credentials: HINDSIGHT_URL / HINDSIGHT_API_KEY / HINDSIGHT_BANK_ID, and the LLM_* pair
cp .env.example .env          # then fill it in; .env is git-ignored

# 2. seed the bank once (idempotent - a second run inserts nothing).
#    close() releases the backend HTTP session; without it Python prints
#    "Unclosed client session" / "Unclosed connector" when the process exits.
PYTHONPATH=src python -c "from debugagent.config import load_memory_config; \
from debugagent.memory.hindsight_store import HindsightMemoryStore; \
from debugagent.seeds.loader import load_seed_cases; \
store = HindsightMemoryStore(load_memory_config()); \
print(load_seed_cases(store).to_dict()); store.close()"

# 3. investigate one issue; renders MEMORY / EVIDENCE / PROPOSAL / DECISION
PYTHONPATH=src python -m debugagent.cli debug --env .env

# 4. print the trace of the last session
PYTHONPATH=src python -m debugagent.cli inspect
```

`--memory hindsight` is the default. `--memory offline:demo/offline-memory.json` runs the same loop
with no network, for rehearsing the flow before recording.

Tests: `PYTHONPATH=src python -m unittest discover -s tests`. The Hindsight tests skip unless
`HINDSIGHT_URL` is set; with it set, 9 of them run against the real service.

The trust rule the CLI enforces: **MEMORY informs · EVIDENCE verifies · AGENT proposes · ENGINEER
decides.** A recalled case is never evidence and never satisfies a verification requirement.

## Documentation Index

| Document                                                         | Purpose                                               |
| ---------------------------------------------------------------- | ----------------------------------------------------- |
| [docs/hackathon-requirements.md](docs/hackathon-requirements.md) | Organized requirements, judging criteria, constraints |
| [docs/project-ideas.md](docs/project-ideas.md)                   | Detailed breakdown of 5 candidate ideas               |
| [docs/idea-comparison.md](docs/idea-comparison.md)               | Neutral decision matrix with reasoning                |
| [docs/system-design-notes.md](docs/system-design-notes.md)       | Cross-cutting architecture principles                 |
| [docs/demo-concepts.md](docs/demo-concepts.md)                   | Demo narratives for each idea                         |
| [docs/decision-log.md](docs/decision-log.md)                     | Structured decision tracking                          |
| [docs/hindsight-capability-verification.md](docs/hindsight-capability-verification.md) | Verified Hindsight capabilities vs proposals |
| [docs/project-selection-analysis.md](docs/project-selection-analysis.md) | Evidence-based analysis of the 5 candidates      |
| [docs/final-project-definition.md](docs/final-project-definition.md) | Selected project: problem, workflow, memory, MVP |
| [docs/implementation-plan.md](docs/implementation-plan.md)           | Phased build plan (no code yet)                  |
| [docs/team-task-split.md](docs/team-task-split.md)                   | Proposed Rama/Mukul task split                   |
| [docs/implementation-readiness-review.md](docs/implementation-readiness-review.md) | Readiness verdict + unblock list (no code yet) |
| [docs/domain-neutral-system-design.md](docs/domain-neutral-system-design.md) | Domain-neutral architecture (no domain fixed) |
| [docs/architecture-review.md](docs/architecture-review.md) | Boundaries, Hindsight map, MVP cut, verdict |
| [docs/two-phase-implementation-plan.md](docs/two-phase-implementation-plan.md) | Phase 1 MVP (Sept 29) + Phase 2 (Oct 2) |
| [docs/llm-provider-architecture.md](docs/llm-provider-architecture.md) | Provider-independent LLM adapter design (no code yet) |
| [docs/provider-verification.md](docs/provider-verification.md) | Provider facts/unknowns + runtime-test gates |
| [docs/phase1-execution-plan.md](docs/phase1-execution-plan.md) | Implementation tree + M0–M8 sequence (Sept 29) |
| [docs/phase1-mukul-plan.md](docs/phase1-mukul-plan.md) | Mukul-side milestones MK0–MK9 + contract gaps |
| [docs/phase1-mukul-m0-plan.md](docs/phase1-mukul-m0-plan.md) | MK0 runtime verification (providers, schema, drift) |

***

## Key Constraints to Remember

- Hindsight memory must be **central**, not superficial
- Solve a **real professional problem** for a **specific persona**
- Demonstrate a **visible learning curve** (before/after memory)
- Keep **scope tight** — one workflow, one persona, one value prop
- Use **realistic data**, not toy examples
- Build something **portfolio-worthy**
- Do NOT claim Hindsight guarantees better results
- Separate: *previous experience* → *current evidence* → *agent reasoning* → *final outcome*

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

Simple conversation-history retrieval is insufficient for demonstrating meaningful persistent memory.

---

## Collaboration

- GitHub is the single source of truth; `main` is the shared stable branch.
- Mukul (`mukul-raii`) is a collaborator.
- Do substantial work in branches (e.g. `rama-planning`, `mukul-ideas`), not directly on `main`.
- Changes are reviewed through Pull Requests before merging.
- Planning decisions are recorded in [docs/decision-log.md](docs/decision-log.md).
- Workflow details: [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/collaboration-workflow.md](docs/collaboration-workflow.md).

---

## Change Tracking

- Significant changes are recorded in [docs/change-log.md](docs/change-log.md).
- Architectural decisions are recorded in [docs/decision-log.md](docs/decision-log.md).
- Git history (`git log`) is the authoritative technical change record.
- Commit planning changes logically with clear messages.
