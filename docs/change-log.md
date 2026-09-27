# Project Change Log

## Purpose

This file records significant planning, architecture, collaboration, and implementation changes
for the HackwithHyderabad 3.0 repository. It is a human-readable summary; the Git history
(`git log`) is the authoritative technical record of file-level changes, and
`docs/decision-log.md` is the authoritative record of project decisions.

## Timeline

### Stage 1: Initial Planning

- Initial project-planning package created (`README.md`, `docs/` planning documents, `.gitignore`).
- Three candidate ideas documented: Engineering Debugging Memory Agent, AI Evaluation Memory Agent,
  Engineering Incident Memory Agent.
- No final project selected.
- Hindsight by Vectorize identified as the required persistent-memory technology.
- EGER (Evidence-Grounded Engineering Reasoning) explicitly treated as background inspiration only —
  not a validated framework, not a project dependency.
- No application code created.

### Stage 2: Planning Quality Review

Corrections recorded in `docs/decision-log.md` (dated 2026-09-27 in the log):

- Removed unsupported performance/time claims (e.g. "3 hours → 15 minutes", "45 min → 8 min",
  κ-metric improvements); replaced with neutral workflow-comparison and "proposed evaluation" language.
- Qualified Hindsight capability assumptions: architecture proposals explicitly marked
  "proposed application-level behavior" or "to verify against current Hindsight documentation".
- Clarified that EGER is personal research and not a validated dependency, while preserving the
  architectural principle: "Memory informs. Evidence verifies. Agent proposes."
- Made the Engineering Debugging MVP independent of live EDA tool integration
  (pre-seeded structured cases; tool parsing moved to optional extension).
- Added the core design question "What changes because the agent remembers?" to `README.md`
  and `docs/system-design-notes.md`.
- Added a verification checklist to `docs/hackathon-requirements.md`.
- Final project decision remained open.

### Stage 3: Mukul Collaboration

- Mukul joined the team (GitHub: `mukul-raii`).
- Mukul contributed two additional project ideas, recorded in `docs/decision-log.md`:
  - Idea 4: Deal Intelligence Agent (VC deal diligence memory).
  - Idea 5: Competitive Intelligence Agent (competitive-landscape / thesis-accuracy memory).
- Candidate set expanded from three to five; documented in `docs/project-ideas.md`,
  `docs/idea-comparison.md`, and `docs/demo-concepts.md`.
- Project selection remained open; discussion questions preserved in `docs/decision-log.md`.

### Stage 4: Git Collaboration Setup

- Local Git repository initialized on branch `main` (previously no `.git` existed).
- Initial commit (see below) containing the full planning package plus collaboration files.
- `.gitignore` extended with `*.token`, `.envrc`, and `.venv/` (planning docs remain tracked).
- `CONTRIBUTING.md` created (branch → PR → review workflow, secrets rule).
- `docs/collaboration-workflow.md` created (exact commands for Rama + Mukul).
- Secret check performed before committing: no API keys, tokens, `.env`, or credential files found
  (only the word "secret" in documentation guidance).
- Private GitHub repository created via authenticated GitHub CLI (no README/.gitignore/license
  initialized remotely, since the local repo already contains them).
- Local connected as `origin`; existing `main` branch pushed (no force-push, no reset, no re-commit).
- Collaborator invitation sent to `mukul-raii` (pending acceptance at time of writing).
- `docs/change-log.md` (this file) created and `README.md` Change Tracking section added,
  committed as a follow-up documentation commit and pushed.

Git record (from `git log`, dates in local timezone):

- `24ef5b2` — 2026-09-27 — "Initialize HackwithHyderabad planning repository"
  - 10 files, +2340 lines: `.gitignore`, `CONTRIBUTING.md`, `README.md`,
    `docs/collaboration-workflow.md`, `docs/decision-log.md`, `docs/demo-concepts.md`,
    `docs/hackathon-requirements.md`, `docs/idea-comparison.md`, `docs/project-ideas.md`,
    `docs/system-design-notes.md`.

## Current State

- Repository visibility: PRIVATE (`RAMA-L7/hackwithhyderabad-3`).
- Default branch: `main` (local `main` tracks `origin/main`).
- Current commit: `24ef5b2` "Initialize HackwithHyderabad planning repository"
  (plus the documentation commit below once pushed).
- Working tree state: clean at time of setup.
- Remote: `origin` → `https://github.com/RAMA-L7/hackwithhyderabad-3.git`.
- Collaboration status: invitation sent to `mukul-raii`; pending acceptance.
  Recommended branches: `rama-planning`, `mukul-ideas`; changes via Pull Request into `main`.
- Application code: none exists (planning-only repository).
- Project selection: open — five candidates under evaluation, no winner declared.

## Next Planned Changes

- Mukul accepts the collaborator invitation.
- Team discussion to select the final project (see open questions in `docs/decision-log.md`).
- Verify Hindsight API capabilities against official documentation before implementation.
- Confirm hackathon submission rules with organizers.
