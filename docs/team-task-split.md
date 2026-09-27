# Team Task Split (Proposed)

**Status:** Proposed only — to be confirmed by Rama Krishna Ketha and Mukul Rai before
implementation starts. Nothing below is assigned until both agree.
**Reference:** `docs/implementation-plan.md` for phase definitions.

## Proposed Split

| Area | Proposed owner | Notes |
|------|---------------|-------|
| Hindsight layer (setup, retain/recall/PATCH/invalidate, spike) | Rama (proposed) | Builds on planning-phase verification work |
| Case schema + seed data (taxonomy, 10–15 cases) | Rama (proposed) | Leverages VLSI/debugging background |
| Agent workflow (recall → verify → propose → retain loop) | Mukul (proposed) | Core loop; pair on verification-gate design |
| CLI / interaction design | Mukul (proposed) | Demo-facing surface; pair on recall rendering |
| Test / evaluation runs | Shared (proposed) | Both observe; raw results logged jointly |
| Demo video + live rehearsal | Shared (proposed) | One narrator, one operator suggested |
| Technical articles (1 each) | Each member (required) | Topics chosen after MVP behavior exists |
| Repo hygiene (reviews, merges, decision log) | Shared (proposed) | All merges via Pull Request with review |

## Confirmation Checklist

- [ ] Both members confirm language + hosting + key ownership (Phase 0).
- [ ] Both members confirm this split or amend it (recorded in decision log).
- [ ] Branch plan confirmed (`rama-*` / `mukul-*` working branches, PRs into `main`).
- [ ] Decision deadline and tiebreaker recorded (open questions Q6, Q11).
