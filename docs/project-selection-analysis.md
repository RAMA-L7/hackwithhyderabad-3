# Project Selection Analysis

## Purpose

This document supports team discussion between Rama and Mukul. It analyzes the five candidate
ideas against shared criteria using the verified Hindsight capabilities in
`docs/hindsight-capability-verification.md`. **It does not select the final project.**
No winner is declared and no ranking is produced. Shortlist candidates below are framed as
"deserves deeper team discussion," not as recommendations.

## Evaluation Criteria

| ID | Criterion | Why it matters |
|----|-----------|----------------|
| A | Hindsight memory centrality | Judging weight is 25% on Hindsight use; memory must change the workflow, not decorate it |
| B | Memory depth | Shallow single-fact recall is hard to distinguish from RAG/chat history |
| C | Learning / improvement loop | Organizers emphasize a visible learning curve (before/after memory) |
| D | Technical feasibility | Two people, hackathon timeframe, one reliable MVP |
| E | Hindsight dependency risk | Unverified capabilities in the critical path are the top implementation risk |
| F | Data availability | Seed data must be creatable in days, not weeks |
| G | Demo clarity | The before/after moment must land in ~60–120 seconds |
| H | Evaluation feasibility | Memory's contribution must be demonstrable without fabricated metrics |
| I | Scope control | One persona, one workflow, one value proposition |
| J | Engineering quality | Architecture, failure handling, testing, observability must be demonstrable |
| K | Real-world applicability | 10% real-world impact + portfolio credibility require a genuine problem |
| L | Portfolio value | The project must serve both members' public portfolios |

## Idea 1 Analysis — Engineering Debugging Memory Agent

- **Core workflow:** Engineer describes symptoms + context → agent recalls past debug cases → proposes
  investigation priority → engineer verifies against current environment → resolution retained.
- **Memory unit:** Debug case (problem signature, symptoms, environment, investigation trace, failed and
  successful approaches, evidence references, outcome, lessons).
- **Why memory matters:** Debugging is pattern recognition over high-dimensional context; institutional
  knowledge is currently siloed in tickets and tribal memory.
- **Hindsight dependency:** retain cases with metadata/tags (environment, outcome); recall by query +
  tags; application-side ranking. All verified. No dependency on unverified capabilities once the
  schema is treated as an application convention.
- **Technical complexity:** Medium. Hardest parts are seed-data realism and similarity quality for
  technical signatures — both application-level, both tunable.
- **Data requirements:** 10–15 synthetic-but-realistic timing cases. Rama's VLSI background is the
  decisive advantage here; proprietary-data risk is handled by synthesis.
- **Demo concept:** Generic checklist (no memory) vs targeted hypothesis with evidence link (with memory);
  verification gate visible on screen.
- **Evaluation approach (proposed):** Recall precision (% recalls marked relevant), recall utility
  (% relevant recalls that changed the investigation), failed approaches avoided. All observable in-demo;
  none pre-claimed.
- **Main risks:** Seed-data realism effort; semantic similarity on terse technical signatures may need
  tag-heavy fallback; "just use grep/wiki" skepticism from judges.
- **Unknowns:** Which signature fields actually discriminate cases (discoverable during seeding, not blocking).
- **Evidence needed:** A 2-hour spike: retain ~5 sample cases, recall with tags + query, confirm relevance
  ordering is steerable.

## Idea 2 Analysis — AI Evaluation Memory Agent

- **Core workflow:** Evaluator submits (prompt, response, rubric) → agent recalls similar past evaluations
  with reasoning and calibration notes → evaluator decides (agree/disagree) → full trace retained.
- **Memory unit:** Evaluation case (prompt, response, rubric version, score, structured reasoning, failure
  tags, calibration notes, evaluator decision on recalled cases).
- **Why memory matters:** Evaluation drift and inter-evaluator inconsistency are real, expensive problems;
  calibration memory is the missing institutional layer.
- **Hindsight dependency:** Semantic recall over responses (verified); tag filtering by rubric/pattern
  (verified); observations could consolidate failure patterns (verified); bank directives could encode
  calibration rules (verified, affects `reflect` only). No unverified dependency.
- **Technical complexity:** Medium. Hardest part is the UI (side-by-side response + recall panel) and
  making consistency visible — engineering effort, not capability risk.
- **Data requirements:** 20–30 seed evaluations across 3–4 failure patterns; Rama's eval background helps.
- **Demo concept:** Recall panel auto-surfaces pattern + score distribution + calibration note; evaluator
  aligns with consistent reasoning.
- **Evaluation approach (proposed):** Inter-annotator agreement trend, override rate, pattern coverage.
  Requires multiple evaluators or time-lapse to be convincing — the heaviest demo-setup cost of all ideas.
- **Main risks:** Least cinematic demo (consistency is a metric, not a moment); subjectivity/bias concerns;
  UI build time.
- **Unknowns:** Whether agreement deltas are demo-visible with only 20–30 seeds.
- **Evidence needed:** Mock the recall panel against 5 seed evals; judge whether the pattern surfacing
  reads clearly on screen in under a minute.

## Idea 3 Analysis — Engineering Incident Memory Agent

- **Core workflow:** Alert symptoms + context → agent recalls past incidents → ranked hypotheses +
  failed fixes + runbook links → engineer verifies (version/config) → resolution retained with timeline.
- **Memory unit:** Incident case (symptoms, severity, environment, triggering change, timeline, root cause,
  attempted fixes, resolution, runbook refs, postmortem link, lessons).
- **Why memory matters:** On-call rotation loses context; runbooks are static; incidents rhyme across services.
- **Hindsight dependency:** Tag scoping (service/severity — verified); temporal recency (verified);
  invalidate for stale runbooks (verified); entity graph for shared components (verified). No unverified
  dependency.
- **Technical complexity:** Medium. Log/alert normalization for one stack; chatops-style CLI/Slack UI is cheap.
- **Data requirements:** 8–12 seed incidents; public datasets and synthetic generators exist — best data
  availability of the five.
- **Demo concept:** Different engineer resolves a rhyming incident using recalled root cause + failed-fix
  warnings; verification (version check) visible.
- **Evaluation approach (proposed):** Resolution-path comparison with/without memory; failed fixes avoided;
  recall relevance marks.
- **Main risks:** High-stakes domain trust ("AI fixes production" misread — mitigated by suggest-not-act UX);
  incident-memory tooling exists (differentiation rests on the verification loop + Hindsight depth).
- **Unknowns:** None blocking; the design is the most conventionally understood of the five.
- **Evidence needed:** Same 2-hour spike as Idea 1 (shared recall mechanics); confirm tag scoping
  separates failure domains cleanly.

## Idea 4 Analysis — Deal Intelligence Agent

- **Core workflow:** Analyst enters deal context → agent recalls comparable deals (red flags that mattered,
  decisions + rationales, known outcomes) → prioritized diligence checklist → analyst verifies against
  live deal data → decision retained; outcome patched in later.
- **Memory unit:** Deal case (signature, thesis fit, diligence questions, red flags, references, decision +
  rationale, evidence refs, outcome when known).
- **Why memory matters:** Partner pattern-recognition is tribal and decays with turnover; diligence→outcome
  links are almost never closed.
- **Hindsight dependency:** retain + recall + tags (verified); **PATCH outcome updates** (verified via
  memories curation API); observation history for audit (verified); Memory Defense for confidentiality
  (verified, opt-in). The decision→outcome loop — the idea's signature mechanic — is natively supported.
- **Technical complexity:** Medium. Deal similarity is fuzzier than engineering signatures (tags + semantic
  hybrid); outcome updates arrive late by nature (support explicit unknown state).
- **Data requirements:** 10–15 synthetic deal cases across 3–4 red-flag patterns; Mukul's domain input is
  the decisive input here.
- **Demo concept:** Same red-flag pattern, second deal: recalled case surfaces the distinguishing question
  missed last time (e.g. domain co-founder); decision differs with explicit rationale.
- **Evaluation approach (proposed):** Diligence-question quality comparison (generic checklist vs
  recall-sharpened questions); recall relevance marks. No accuracy claims needed — the claim is sharper
  questions, visibly demonstrated.
- **Main risks:** Synthetic-data realism (requires VC pattern knowledge); confidentiality perception
  (synthetic-only demo + Memory Defense story mitigates); "just ask the partner" skepticism.
- **Unknowns:** Whether 10–15 cases produce visibly distinct recall across 3–4 patterns (testable in the spike).
- **Evidence needed:** Retain 4–5 sample deals with overlapping red flags; confirm the distinguishing-factor
  recall reads clearly; confirm PATCH outcome update round-trips.

## Idea 5 Analysis — Competitive Intelligence Agent

- **Core workflow:** Analyst asks about a sector → agent recalls prior landscape maps + theses + accuracy →
  flags staleness → analyst verifies against current evidence → updated landscape retained; prior thesis
  marked resolved with accuracy note.
- **Memory unit:** Landscape case (sector signature, competitors, moat thesis + confidence, sources,
  research date, resolution/accuracy).
- **Why memory matters:** Landscapes are rebuilt from zero each time; theses are falsifiable predictions
  that are never checked; blind spots recur.
- **Hindsight dependency:** Same verified set as Idea 4, plus `source_facts`/`prefer_observations` for
  thesis provenance (verified) and temporal ranking for staleness (verified; note recall's window ranks
  rather than filters — staleness warnings stay application-level).
- **Technical complexity:** Medium, slightly above Idea 4: sector-adjacency matching + staleness scoring
  ("sector clock-speed") are new application logic with no native equivalent.
- **Data requirements:** 10–15 landscapes, at least half with resolved outcomes — the most demanding seed
  set (each resolved case needs a plausible "what actually happened").
- **Demo concept:** Resolved prior thesis (rated inaccurate) surfaces the specific blind spot to correct
  in an adjacent segment.
- **Evaluation approach (proposed):** Thesis-accuracy comparison; blind-spot correction visible on screen.
- **Main risks:** Overlaps Idea 4 heavily (same persona, same domain, adjacent workflow) — picking both is
  out of scope; picking this over Idea 4 needs justification. Resolved-outcome seed burden is highest.
  "Just search Crunchbase" skepticism.
- **Unknowns:** Whether adjacency matching (trucking → cold-chain) recalls reliably with tags + semantics.
- **Evidence needed:** Same spike as Idea 4 plus one adjacency-recall test; compare demo sharpness of
  Idea 4 vs Idea 5 head-to-head before shortlisting one VC idea.

## Cross-Idea Comparison

| Criterion | Idea 1 (Debugging) | Idea 2 (Evaluation) | Idea 3 (Incident) | Idea 4 (Deal) | Idea 5 (Competitive) |
|-----------|-------------------|---------------------|-------------------|---------------|----------------------|
| Hindsight centrality (A) | High | High | High | High | High |
| Memory depth (B) | High (multi-case traces) | High (reasoning traces) | High (timelines) | High (decision→outcome) | High (thesis→accuracy) |
| Learning loop (C) | Strong (priority shifts) | Medium (calibration drift is slow) | Strong (hypothesis shifts) | Strong (question sharpens; loop closes late) | Strong (blind-spot correction) |
| Technical feasibility (D) | Medium | Medium (UI-heavy) | Medium | Medium | Medium (adjacency + staleness logic) |
| Hindsight dependency risk (E) | Low (all-verified path) | Low | Low | Low (PATCH/Memory Defense verified) | Low |
| Data availability (F) | Medium (Rama domain) | Medium (Rama domain) | High (public) | Medium (needs Mukul patterns) | Medium (heaviest resolved seeds) |
| Demo clarity (G) | High | Medium | High | High | High |
| Evaluation feasibility (H) | High (observable in-demo) | Medium (needs N evaluators) | High | High (question quality visible) | High |
| Scope control (I) | High | High | High | High | High |
| Engineering quality (J) | High | High | High | High | High |
| Real-world applicability (K) | High | High | High | High | High |
| Portfolio value (L) | High (systems depth) | High (ML engineering) | High (SRE) | High (decision systems) | High (research systems) |

## Shortlist Candidates (for deeper discussion — not winners)

- **Idea 3 (Incident):** Lowest data friction (public datasets), cheapest UI (CLI/chatops), clearest
  time-pressured demo, and every mechanism maps to a verified API. Discuss: differentiation vs existing
  incident tooling, and whether the team wants an ops-flavored portfolio piece.
- **Idea 1 (Debugging):** Strongest team-domain fit on Rama's side, most novel application surface
  (hardware timing + memory), EDA-tool-free MVP already designed. Discuss: seed-data authorship load
  and whether similarity quality can be shown convincingly in the spike.
- **Idea 4 (Deal):** Strongest Hindsight-native feedback loop (decision→outcome via verified PATCH +
  history), Mukul's domain, highest innovation-novelty claim available ("most tools stop at decision").
  Discuss: synthetic-data realism burden and whether the team is comfortable demoing a finance-domain
  workflow; run Idea 4 vs Idea 5 head-to-head and shortlist at most one VC idea.

Candidates held back pending evidence (reversible by the team): Idea 5 overlaps Idea 4 on persona,
domain, and mechanics while carrying heavier seed costs — revisit only if the head-to-head favors it.
Idea 2 has the weakest demo-clarity story and the costliest evaluation setup — revisit only if the team
values the eval-domain portfolio signal above demo risk or if new evidence (e.g. a crisp agreement
visualization) emerges during discussion.

## Decision Risks

1. **Hosting + LLM key/cost model unresolved** (local Docker vs embedded vs Cloud; whose API key funds
   retain/recall/reflection calls). Identical across ideas — resolve once, not per idea.
2. **Pre-seeded memory policy unknown** (organizer rule Q14). If live-learning-only is required, every
   demo concept needs redesign.
3. **Mukul's preference and background still open** (Q1, Q6, Q13) — the single highest-leverage unknown.
4. **VC-domain seed realism** (A11, A12, Q16) — if synthetic deals/landscapes can't be made convincing
   in a day, Ideas 4/5 lose their edge regardless of technical fit.
5. **Idea 2's agreement metric** needs multi-evaluator setup; Idea 5's resolved-outcome seeds are the
   heaviest authoring load. Both are effort risks, not capability risks.
6. **Server auth model and rate limits unverified** — matters only if the demo is shared/deployed rather
   than local; confirm before promising a live URL.

## Questions for Rama and Mukul

- [ ] Which 2 ideas feel most exciting to build for two weeks — gut check before evidence?
- [ ] Mukul: what is your background, and do you prefer the VC domain (4/5) or engineering (1/3)?
- [ ] Can we author convincing synthetic seeds for the preferred domain in ~1 day? (Trial: 3 cases each.)
- [ ] Local Docker vs embedded vs Cloud — who provisions what, whose LLM key, what budget?
- [ ] Is pre-seeded memory allowed, or must learning happen live in the demo? (Ask organizers.)
- [ ] Head-to-head if VC wins: Idea 4 or Idea 5 — which demo moment is sharper?
- [ ] Head-to-head if engineering wins: Idea 1 or Idea 3 — domain fit vs data friction?
- [ ] What is the decision deadline and the tiebreaker if we disagree?
- [ ] Who owns which half of the MVP (Hindsight layer, agent loop, UI, seed data, video)?
