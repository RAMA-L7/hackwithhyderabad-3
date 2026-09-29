# Demo Video — Recording Script

**Target:** 3:30–4:00 (limit 2–5 min) · **Code:** `main` @ `4bafcbe` · **Dress rehearsal:** 29 Sept 2026,
fresh bank `demo-dress-1790661809`, every act as below (timings in §4 are measured, not estimated).

**The one claim the video makes:** *the second investigation is informed by the first, and memory never
counts as evidence.* No speed, accuracy or improvement numbers anywhere, on screen or in narration.

## 1. Story in one line per act

| Act | File | What the judge must see | Criterion |
|---|---|---|---|
| 1 · Before | `act1-billing.yaml` | New issue, **no usable memory**, generic proposal, engineer resolves, **case retained** | Hindsight: memory starts empty for this issue |
| 2 · Recall | `act2-media-uploader.json` | Past case recalled **with its original environment and "Failed before"**; H1 leads with the recorded fix | Innovation, Hindsight |
| 3 · Disagreement | `act3-orders.yaml` | The bank holds **conflicting history** for this service; it is surfaced and the agent **never resolves it**. Nothing is verified without current facts | Trust, Innovation |
| 4 · Learning curve | `act4-billing-recurs.yaml` | Act 1's issue returns: **the case learned minutes ago** is recalled and cited, with its fix and failed approach | **Before/after on the same task** (organizers' key moment) |

Act 1 → Act 4 is the before/after on the same issue. Close by saying so.

## 2. Pre-flight (15 minutes before, off camera)

1. `git switch main && git pull` → `4bafcbe` or later. `env PYTHONPATH=src python3 -m unittest discover -s tests` → OK.
2. `python3 -c "import hindsight_client, yaml"` (installs: `pip install "hindsight-client==0.10.1" pyyaml`).
3. `.env.live` has both LLM keys, `HINDSIGHT_URL`, `HINDSIGHT_API_KEY`.
4. Terminal: dark theme, font 18–20 pt, window ~100 columns × 40 rows (sections are 78 wide), a short prompt
   (e.g. `function fish_prompt; echo '$ '; end` for this shell only), notifications off.
5. Screen recorder at 1080p, system audio off, mic on.
6. **New shell for the take**, then:

```fish
set -x HINDSIGHT_BANK_ID demo-rec-(date +%s)
set -x DEBUGAGENT_DATA_DIR /tmp/demo-ledger-$HINDSIGHT_BANK_ID
set -x LLM_PRIMARY_TIMEOUT_S 20      # Baseten was slow on 29 Sept (>10 s); avoids ~25 s of timeout + failover
env PYTHONPATH=src python3 -c "from debugagent.config import load_memory_config; from debugagent.memory.hindsight_store import HindsightMemoryStore; from debugagent.seeds.loader import load_seed_cases; s = HindsightMemoryStore(load_memory_config()); print(load_seed_cases(s).to_dict()); s.close()"
```

The seed output **must** show all six under `'inserted'`, and `'skipped': [], 'rejected': []`. Anything else:
open a new shell and start step 6 again with a new bank (never record on a bank that was used before).

7. Optional provider check (spends ~10 LLM calls): `python3 mk0/probe.py --only D1,D2,S2`.
8. `clear`. Start recording.

## 3. Exact answers for every prompt

**The CLI does not ask the same questions in every act.** Two of the per-hypothesis prompts are
conditional, so type the answer for the prompt that is actually on screen — if a prompt is not
shown, send nothing and wait for the next one.

| Prompt | Appears | Answer |
|---|---|---|
| `Add current facts as name=value, or a plain sentence for an observation. … (blank line to continue)` | always, once per session | **Enter** |
| `Verify H1: <hypothesis>` (header) | always, per hypothesis | read only |
| `Does current evidence support or contradict it?` [supported/contradicted] | **only when current evidence is complete.** Skipped when the CLI prints `current evidence lacks: …` | `supported` |
| `Is the cited past case relevant to this issue?` [y/n] | **only when that hypothesis cites a case.** Skipped for a hypothesis that cites nothing | `y` |
| `Your decision` [accept/modify/reject] | always, per hypothesis | `accept` |
| `Note (optional)` | always, per hypothesis | **Enter** |

What to expect per act:

| Act | Support/contradict prompt | Cited-case relevance prompt |
|---|---|---|
| 1 · billing | asked | **skipped** — memory abstained, so no hypothesis cites a case |
| 2 · media-uploader | asked | asked (H1 cites the past case) |
| 3 · orders-api | **skipped** — `act3-orders.yaml` states only `service`, so the CLI prints `current evidence lacks: …` and sets `insufficient_evidence` itself | asked, for hypotheses that cite a conflicting case |
| 4 · billing recurs | asked | asked (H1 cites the case from Act 1) |

The number of `Verify` rounds is whatever the LLM returned — two or three hypotheses in the
dress rehearsal. Answer each round the same way; if a prompt you expected is missing, that is the
CLI behaving correctly, not a fault (§5).

Resolution answers (after `Did you resolve the issue? [y/n]`):

| Prompt | Act 1 | Act 2 | Act 3 | Act 4 |
|---|---|---|---|---|
| Did you resolve the issue? | `y` | `y` | `n` | `n` |
| What did you do? | `added an idempotency key on (job_id, invoice_no) with a unique constraint` | `raised nginx client_max_body_size to 10m` | — | — |
| What did you observe after it? | `retries no longer create duplicate invoices` | `no resets above 2 MB in a 20-upload sweep` | — | — |
| Confirmed root cause | `the retry path re-ran the insert without an idempotency key` | `reverse proxy request body limit` | — | — |
| Outcome | `resolved` | `resolved` | — | — |
| Failed approaches (`approach \| why`) | `wrapped the insert in a transaction \| the retry opened a new transaction and inserted again`, then **Enter** | **Enter** | — | — |
| Evidence references | `log://billing-service/2026-09-29/job-retries.log`, then **Enter** | `log://media-uploader/2026-09-29/nginx-error.log`, then **Enter** | — | — |

The Act 1 failed approach matters: Act 4 shows it back as "Failed before".

## 4. Storyboard

Timings are the dress rehearsal's wall time per act (LLM call included). Talk over the waits; cut
them to 1–2 s in editing.

| # | Time | On screen | Say | Point at |
|---|---|---|---|---|
| 0 | 0:00–0:20 | Title card, then terminal | "Debugging knowledge lives in people's heads and old tickets. Every investigation starts from zero. This agent remembers past debugging cases in Hindsight, and treats them as experience, never as proof." | — |
| 1a | 0:20 | `env PYTHONPATH=src python3 -m debugagent.cli debug --input demo/inputs/act1-billing.yaml` | "A new issue: billing-service writes duplicate invoices on retries. I'm feeding it as a YAML file." | `Issue loaded from …` |
| 1b | +5 s | MEMORY | "The bank has six past cases, none about this. It says so instead of guessing." | *No usable memory* |
| 1c | +~15 s (LLM) | PROPOSAL | "So every hypothesis is generic. Nothing is dressed up as experience." | `[generic · no memory used]`, `cites: no past case` |
| 1d | answers §3 | DECISION, then resolve | "I verify, decide, and record what fixed it, including the approach that failed first." | the failed-approach line |
| 1e | end (Act 1 ≈ 32 s) | RETENTION | "That outcome is now a real Hindsight memory." | `memory id:` |
| 2a | 1:20 | `… --input demo/inputs/act2-media-uploader.json` | "A different team's issue: uploads over 2 MB reset behind nginx." | — |
| 2b | +5 s | MEMORY | "Now memory has something. A past media-uploader case, with the environment it happened in, what differs now, and what failed before." | `original environment: … proxy=nginx-1.25`, `Failed before: raised the uploader client timeout` |
| 2c | +~6 s | PROPOSAL | "The first hypothesis cites that case and leads with the fix that worked. It doesn't repeat the approach that failed." | `H1 [memory-backed]`, `cites: 2bf03cc80118b00a`, `client_max_body_size` |
| 2d | answers §3 | EVIDENCE / DECISION | "The past case is a suggestion. Only today's facts verify it, and I decide." | EVIDENCE section; `past case relevance confirmed by engineer: yes` |
| 2e | end (Act 2 ≈ 12 s) | RETENTION | "Retained." | `memory id:` |
| 3a | 2:10 | `… --input demo/inputs/act3-orders.yaml` | "What if history disagrees with itself?" | — |
| 3b | +5 s | MEMORY | "The bank holds two past orders-api cases: same symptom, different root causes. The conflict is surfaced, and the agent doesn't pick a side. How many appear as candidates depends on the scores." | the conflicting case(s) and their `conflicts with` — **not a fixed count of lines** |
| 3c | +~10 s | PROPOSAL / DECISION | "Hypotheses that lean on a conflicting case are marked as disputed, and without today's facts neither side can be verified." | `memory-backed · past cases disagree`, `insufficient_evidence` |
| 3d | end (Act 3 ≈ 13 s) | answer `n` | "Not resolved, so nothing is written to memory." | `Nothing retained` |
| 4a | 2:45 | `… --input demo/inputs/act4-billing-recurs.yaml` | "Now the billing duplicates come back, a few minutes later." | — |
| 4b | +5 s | MEMORY | "This time memory has the case it learned in the first run: the root cause, the fix, and the approach that failed." | `[relevant] case …`, `Failed before: wrapped the insert in a transaction` |
| 4c | +~10 s | PROPOSAL | "Same issue, different investigation: it starts from what worked last time, citing that case." | `H1 [memory-backed]`, `cites:` the Act 1 id |
| 4d | answer `n` (Act 4 ≈ 26 s) | — | "That's the learning curve: the first run informed the second." | — |
| 5 | 3:30–3:50 | Diagram or README | "Memory informs, evidence verifies, the agent proposes, the engineer decides. Hindsight is the memory; if the primary model fails, a fallback serves it and says so." | `served by … · fallback_used=` |

If the recording shows `fallback_used=True` (it did twice in the dress rehearsal), keep it and say:
*"the primary model timed out, the fallback answered, and the header says which one."*

## 5. Recovery rules

| Situation | Do |
|---|---|
| A typo at a prompt | The CLI re-asks; keep going, trim in editing |
| An act looks wrong (e.g. Act 1 recalls something) | Stop. New shell, new bank (§2 step 6), restart **from Act 1**. Never re-run one act on the same bank |
| `error: memory unavailable …` (Hindsight 504) | Wait a minute, new bank, restart from Act 1 |
| An LLM wait over 30 s | Keep recording; cut in editing |

Record **two complete takes on two different banks**; keep the better one, the other is the backup.

## 6. Editing

- Cut LLM waits to 1–2 s; never cut a section's content or the provider header.
- Title card (0:00): project name and one line on what it does, e.g. *"A debugging agent that remembers
  past incidents — built on Hindsight"*. Keep the event name off the title; it can appear in the video
  description and in the repo link.
- Lower-third per act: *Act 1 · no memory* · *Act 2 · recall* · *Act 3 · disagreement* · *Act 4 · learning curve*.
- Zoom (1.5×) on the "Point at" lines in §4.
- End card: repo URL, the four-line trust rule, team names.
- Export 1080p MP4, 3:30–4:00.

## 7. What not to say or show

- No numbers about speed, accuracy or improvement; no "the threshold is 0.75" (thresholds are provisional).
- Don't promise a count of Act 3 candidate lines, and don't call 0.75 a validated threshold. Both are
  documented as provisional in `docs/phase1-demo-scenario.md` and `docs/m1-contract.md`; if a judge
  asks, say the floors are calibration parameters measured on a six-case bank, and that Act 4's
  genuine match is accepted on `final` rather than on the fallback.
- Don't call the data real: the services are fictional, the incidents are synthetic but realistic.
- Don't say the agent "fixes" anything: it proposes; the engineer decides and acts.
- Don't show `.env.live`, API keys, or the Hindsight URL.
