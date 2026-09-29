# Submission Checklist — due 29 Sept

Source: `docs/Submission.md`. Drafts in this folder: `mukul-article.md`, `rama-article.md`, `posts.md`;
video script: `docs/demo-video-script.md`.

**Hard rule for every piece of content:** the event name and the word for it must not appear anywhere:
titles, body, comments, hashtags, video title and description. Check with
`grep -ci "hackathon\|hackwithhyderabad" <file>` before publishing; it must print 0.

## 0. Who does what

| Deliverable | Mukul | Rama | Team |
|---|---|---|---|
| Profile review form (each member, mandatory) | ☐ | ☐ | |
| Article, public URL | ☐ `mukul-article.md` | ☐ `rama-article.md` | |
| LinkedIn post + 2 comments | ☐ | ☐ | |
| Reddit link post | ☐ r/llmdevs | ☐ r/aiagents | |
| Video on YouTube (public, thumbnail) | | | ☐ |
| Final submission form (once, by the team leader) | | | ☐ |

## 1. Decide first (5 minutes, both)

1. **Repository name.** The posts must link the repo, and `github.com/RAMA-L7/hackwithhyderabad-3`
   contains the event name. Renaming it (GitHub → Settings → Repository name, e.g.
   `hindsight-debug-agent`) keeps the old URL redirecting and removes the risk. Only Rama, as owner,
   can rename. Afterwards each of us runs `git remote set-url origin <new url>`.
2. **README.** It still opens with "Pre-Planning Repository … implementation not started". Judges read
   it first. Replace the top with: one-line description, the four-line trust rule, *How Hindsight is used*
   (retain/recall, fact extraction, verbatim metadata, app-side abstention), and the run commands from
   `docs/phase1-implemented.md` §7. The submission form also asks for an explanation of how Hindsight
   memory is used, and the README is the place to point to.
3. **Who submits the final form** (the guide allows one submission per team).

## 2. Articles (30–45 min each)

For each author:

1. Read your draft; rewrite anything that doesn't sound like you. The guide's own advice: edit until it
   sounds like you. Keep the code snippets as they are (they are copied from `main`).
2. Add images (the guide requires screenshots):
   - Terminal: run the four acts from `docs/demo-video-script.md` on a fresh bank and screenshot
     (Cmd+Shift+4, PNG, cropped): Act 1 MEMORY *No usable memory*; Act 2 MEMORY with *Failed before*;
     Act 4 MEMORY + PROPOSAL (`memory-backed`, `cites:`). Rama: also Act 3 MEMORY (two `[contradictory]`).
   - Architecture: screenshot the diagram from the build map page or from `docs/phase1-implemented.md` §1.
3. Replace `[repo link]` at the end with the repository URL.
4. Publish publicly on Medium, Dev.to, Hashnode, Substack or LinkedIn Articles. Check that headings,
   code blocks and the three links render: Hindsight GitHub, Hindsight docs, Vectorize agent memory.
5. Pre-submit checklist from the guide:

| Check | Mukul | Rama |
|---|---|---|
| Title about the idea, not the event | ✓ | ✓ |
| Opens with something specific and surprising | ✓ recalled the right case, recommended the failed fix | ✓ 1.08 vs 0.003 |
| Problem in concrete terms | ✓ | ✓ |
| Where and how Hindsight is integrated | ✓ | ✓ |
| At least one real code snippet | ✓ 4 | ✓ 2 |
| A concrete before/after | ✓ billing issue twice | ✓ fallback floor before/after |
| An honest lesson or limitation | ✓ unit tests passed, real bank failed | ✓ provisional floor, VLSI gaps |
| Screenshots included | ☐ | ☐ |
| Public, linkable URL | ☐ | ☐ |

## 3. LinkedIn (after the article is live)

1. Copy your post from `posts.md`; replace `[REPO_URL]`. Keep it under 800 characters: shorten a bullet
   if the real URL pushes it over.
2. Type `@Code.in` and pick the company so the tag is real.
3. Publish. Then **first comment**: the article URL. **Second comment**: the Hindsight GitHub link.

## 4. Reddit (after the article is live)

Link post of your article on your subreddit from `posts.md`, titled with the article title. Check the
subreddit's self-promotion rules first.

## 5. Video (team, 45–60 min)

1. Record following `docs/demo-video-script.md`: two full takes on two fresh banks, keep the better one.
   The guide prefers a talking head plus screen; a short "Hi, I'm …" on camera at the start is enough.
   The guide's structure maps onto the script: intro (0:00–0:20), problem without memory (Act 1),
   live demo with recall (Acts 2–4), takeaway (close).
2. Thumbnail: Nano Banana with the prompt in `posts.md`, 16:9, with a photo of you.
3. Upload to YouTube as **public**, title and description from `posts.md`. Not Google Drive.

## 6. Forms

**Profile review form, every member:** https://forms.gle/AXWnanWsEEir6xSP9 (teams without it are not reviewed).

**Final submission form, once per team:** https://forms.gle/cD7fCnPnkdVm2sH78

| Field | Value |
|---|---|
| Email ID / Phone | the submitter's |
| Team name | as registered |
| Team members | Rama Krishna Ketha, Mukul Rai |
| GitHub repository | repository URL (after §1.1) |
| Social media post on LinkedIn | both post URLs |
| Article link | both article URLs |
| Video link | the YouTube URL |
| Reddit post link | both Reddit URLs |
| Feedback | a sentence or two |

The form has one box per field; paste both members' links into the same box, one per line or
comma-separated. Confirm with Rama that you both agree before submitting: only one submission is allowed.

## 7. Order for today

| Step | Who | Needs |
|---|---|---|
| 1. Decide repo name and README (§1) | both | — |
| 2. Profile review forms | each | — |
| 3. Record the video (§5) | Mukul (Rama reviews) | fresh bank |
| 4. Screenshots from the recording session | each | step 3 |
| 5. Edit and publish articles (§2) | each | step 4 |
| 6. LinkedIn + comments, Reddit (§3, §4) | each | step 5 URLs |
| 7. YouTube upload (§5) | Mukul | step 3 |
| 8. Final submission form (§6) | team leader | all URLs |

## Appendix — 20 title candidates (Prompt 1)

Chosen: **Mukul:** #1. **Rama:** #11.

1. Hindsight remembered the failed fix. My dedupe threw it away.
2. I built a debugging agent with Hindsight that admits ignorance
3. What Hindsight actually stores when your agent retains a case
4. How Hindsight made my debugging agent stop repeating failed fixes
5. My Hindsight agent treated memory as evidence. I banned it.
6. Why my Hindsight agent treats memory as a suggestion
7. Hindsight turned my incident notes into facts. I wasn't ready.
8. Debugging agents need "I don't know": lessons from Hindsight
9. How I stopped my Hindsight agent from trusting old fixes
10. The ten lines between Hindsight memory and my model
11. Why my Hindsight agent learned to say "no memory"
12. The same match scored 1.08 and 0.003 in Hindsight
13. I calibrated Hindsight recall floors with six real measurements
14. When two remembered incidents disagree, my Hindsight agent refuses
15. Hindsight memory, fresh banks and the tests that lied
16. How I built abstention on top of Hindsight recall scores
17. My Hindsight agent learned a fix, reused it minutes later
18. Unit tests passed; a fresh Hindsight bank found three bugs
19. Stop trusting similarity scores: a Hindsight memory postmortem
20. Why my debugging agent verifies Hindsight memory against today's facts
