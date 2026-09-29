# Social posts, Reddit, YouTube

Rules from `docs/Submission.md`: never write the event name or the word for it anywhere (post, comments,
hashtags); no links or hashtags in the first two lines; the repo link goes in the main post; the article
URL is the **first comment**; the Hindsight GitHub link is another comment; tag **Code.in**.

Replace `https://github.com/RAMA-L7/hackwithhyderabad-3` with the repository URL (see checklist §1 about its name) and `[ARTICLE_URL]` with
your published article.

## Mukul — LinkedIn post

```text
My debugging agent recalled the right past incident.
Then it recommended the exact fix that incident said had failed.

The memory was fine. My ten lines between the memory and the model weren't.

Building it on Hindsight agent memory taught me:

→ Hindsight stores a case as facts: symptom, root cause, fix, what failed. I kept only the best-scoring fact per case and threw the warning away.
→ Anything the agent can't lose goes in metadata, verbatim.
→ A recalled case is a suggestion. Only today's facts verify it; a missing fact means "insufficient evidence".
→ Before: new issue, "no usable memory". After: same issue minutes later, it recalls what fixed it and what failed.

Code: https://github.com/RAMA-L7/hackwithhyderabad-3
Thanks @Code.in
#AIAgents #AgentMemory #Hindsight #LLM
```

First comment: `Full write-up: [ARTICLE_URL]`
Second comment: `Here's the memory layer I used, if you want to try it: https://github.com/vectorize-io/hindsight`

## Rama — LinkedIn post

```text
The same correct match scored 1.08 in one memory bank and 0.003 in another.
If your agent trusts retrieval scores as relevance, it's trusting noise.

Building the memory layer of a debugging agent on Hindsight agent memory taught me:

→ Give each score one job: final for ordering, semantic only to admit a match when final collapses.
→ Abstain out loud: "no usable memory", plus why.
→ Calibrate on data: unrelated cases scored 0.64–0.71, real ones 0.78+, so the floor moved from 0.70 to 0.75.
→ When past cases disagree, show both. Never let the agent pick a side.
→ Before: unrelated incidents slipped in as "relevant". After: end-to-end runs on fresh banks pass.

Code: https://github.com/RAMA-L7/hackwithhyderabad-3
Thanks @Code.in
#AIAgents #AgentMemory #Hindsight #AIMemory
```

First comment: `Full write-up: [ARTICLE_URL]`
Second comment: `Try Hindsight here: https://github.com/vectorize-io/hindsight`

(When pasting into LinkedIn, type `@Code.in` and pick the company from the dropdown so it becomes a real tag.)

## Reddit (one link post each)

| Who | Subreddit | Title (= article title) | Post type |
|---|---|---|---|
| Mukul | https://reddit.com/r/llmdevs | Hindsight remembered the failed fix. My dedupe threw it away. | Link post → `[ARTICLE_URL]` |
| Rama | https://reddit.com/r/aiagents | Why my Hindsight agent learned to say "no memory" | Link post → `[ARTICLE_URL]` |

If a subreddit asks for body text too, use the article's first paragraph. Check each subreddit's
self-promotion rules before posting; `r/sideproject` and `r/aimemory` are the alternates.

## YouTube (team video)

Title options (Prompt 5):

1. My AI debugging agent remembered what failed last time
2. Watch an agent learn a fix in one session, reuse it the next
3. Agent memory that refuses to guess: a debugging walkthrough
4. I gave my debugging agent memory. Here's the before/after
5. Memory informs, evidence verifies: building a debugging agent with Hindsight

Recommended: **#1**. It names the behaviour the video proves.

Description:

```text
A command-line debugging agent that remembers past incidents with Hindsight agent memory.

In this walkthrough: a new issue with no usable memory, a recalled past case with its original environment and the approach that failed before, two past cases that disagree (shown side by side, never resolved by the agent), and the first issue coming back, now recalled and cited from what the agent learned minutes earlier.

Memory informs. Evidence verifies. The agent proposes. The engineer decides.

Code: https://github.com/RAMA-L7/hackwithhyderabad-3
Hindsight: https://github.com/vectorize-io/hindsight
Docs: https://hindsight.vectorize.io/
```

Thumbnail (Prompt 6, in Google Nano Banana; attach a photo of one or both of you):

```text
Generate a viral thumbnail for this YouTube video. Make the thumbnail attention grabbing and something that people scrolling would want to click on if they see it. The aspect ratio needs to be 16:9.

Video: "My AI debugging agent remembered what failed last time". A terminal-based debugging agent. First run: it says "No usable memory". Minutes later, the same bug returns and the terminal shows "Failed before: wrapped the insert in a transaction" and a hypothesis marked "memory-backed". Put the attached person on the left looking at a dark terminal on the right; large bold text "IT REMEMBERED WHAT FAILED"; a small red "✗ failed before" tag and a green "memory-backed" tag. High contrast, no clutter.
```
