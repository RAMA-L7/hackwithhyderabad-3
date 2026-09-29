# Why a debugging agent should remember experience, not answers

> **A note on the examples.** Every service and incident below — `billing-service`,
> `media-uploader`, `orders-api` — is synthetic: realistic in shape, invented for this project.
> Nothing here describes a real customer or production outage, and we quote no accuracy or
> performance benchmark because we have not measured one.

## The problem

Debugging knowledge accumulates in engineer's heads, old tickets, and unsearchable Slack threads. Two
years later the same failure resurfaces and the person who last fixed it is on another team.

An agent that can search that history is useful. The risk is subtler: **history is not proof.** An old
incident describes a past system state, and treating it as evidence about the system in front of you
is how a confident, well-cited, wrong answer reaches a runbook. The question is not "can the agent
recall past cases?" It is: *what is memory allowed to influence, and what must it never touch?*

## What we built

A command-line debugging agent for a single engineer, built on
[Hindsight](https://hindsight.vectorize.io/) as the memory layer. An engineer describes an incident;
the agent recalls similar past cases, proposes ranked hypotheses in one LLM call, and the engineer
verifies each against facts they supply *now*. The resolved outcome — including whatever failed on
the way — is retained for the next similar incident. Four lines hold the design:

```
MEMORY informs · EVIDENCE verifies · AGENT proposes · ENGINEER decides
```

## How a session works

```
incident --> normalize (deterministic key=value parse)
              |
              +--> recall + classify --> MEMORY    what happened before
              |     (or abstain, with a reason)
              |
              +--> engineer facts -----> EVIDENCE  what is true right now
                        |
                        +--> one structured LLM call --> PROPOSAL  unverified
                                     |                  cites only recalled ids
                                     +--> engineer accept|modify|reject --> DECISION
                                               |
                                         resolved? --yes--> retain to Hindsight
                                                    --no-->  nothing written
```

Two enforcement points carry the design. The evidence object has no input that accepts recalled
content, so recall results cannot flow into it — we removed the channel rather than asking the model to
respect the boundary. And a hypothesis may cite only cases the memory layer actually returned; an
invented id is invalid structured output, retried, then failed over, never shown.

## The before/after

### Act 1 — a new issue with no usable history

```
billing-service writes duplicate invoices when the job is retried
```

The bank holds six past cases; none concern billing or duplicate writes. MEMORY says *"No usable
memory: no recalled case reached the usable floor; proceeding without historical memory."* Every
hypothesis is labelled `generic · no memory used` with **empty** citations — nothing dressed up as
experience. The engineer finds an idempotency problem, verifies it, and records the fix plus the
approach that failed first. That outcome goes to Hindsight as a real case.

### Act 2 — a different issue, where history does help

```
media-uploader resets connections on uploads over 2 MB behind nginx
```

Now MEMORY surfaces the seeded `media-uploader` case as `relevant`: the environment it happened in, a
diff of what differs now, and the approach that had **failed** there. A weaker foreign-service case
appears as a reference with its mismatch stated, not merged into the current environment. H1 cites
that case and leads with the fix that worked there, without proposing the timeout change the past case
recorded as ineffective — which only works because `failed_approaches` is stored in Hindsight metadata
verbatim and re-attached at recall. The boundary reasserts itself: EVIDENCE holds only what the
engineer stated this session.

### Act 4 — the learning curve, measured

The Act 1 billing issue recurs minutes later. MEMORY recalls the case learned in the first run —
root cause, fix, and `Failed before: wrapped the insert in a transaction` — and H1 cites it, starting
from the idempotency key. That is the before/after on one task.

We re-rehearsed this on **four fresh banks** at `semantic_floor` 0.75, driving the real pipeline end
to end. Every run: Act 1 abstained and retained; Act 4 recalled that live case as its single
`relevant` candidate and cited it from H1, `abstained=False`, no backend 5xx. `final` landed at
1.0956–1.0966, `semantic` at 0.833–0.840, and recall returned on the first attempt each time
(0.5–1.4 s). Four identical runs is a stability check, not a performance claim.

## One real bug worth telling you about

Early on, our store collapsed Hindsight's per-fact recall rows to one row per case, keeping the
best-scoring row. Unit tests were green, because our fake client returned one row per case. Against a
real freshly-seeded bank, that best row was almost always the **symptom** — the fact most like the
query. The model got the symptom with no resolution and no failed approach, and did the reasonable
thing: suggested looking at timeouts, which that case had recorded as ineffective. The fix was to stop
discarding facts, and to write environment values and `failed_approaches` into Hindsight metadata
verbatim. **Every behavioural bug we hit passed unit tests with a fake client and failed only on a
fresh real bank.**

## When memory disagrees

The seed bank holds two `orders-api` cases with the *same* symptom — payloads above 2 MB fail — and
*different* root causes: an upstream proxy request-size limit, and an upstream dependency returning
502. Picking the higher-scoring one would be the agent settling an argument it has no evidence for. So a
conflict is defined structurally (same service, materially different root causes), the cases are marked
`contradictory`, each names the other, and if no side leads by the configured margin the layer
abstains and states that margin.

The honest part: **how many of those sides reach the engineer as candidates depends on the scores.**
One side's `final` can collapse far enough that its `semantic` falls below the floor, leaving it shown
as excluded rather than as a candidate — a fresh-bank measurement did exactly that, one case accepted
as `contradictory` and the other excluded as `irrelevant`. Rely not on the count but on the conflict
being surfaced rather than silently resolved, and on a hypothesis leaning on a disputed side being
labelled `memory-backed · past cases disagree` and marked `insufficient_evidence` when current
evidence is missing. Paraphrases are merged first: one run stored a reworded copy of an existing cause
as a separate root cause.

## Threshold calibration, honestly

Hindsight's `final` score is **query-relative**: the same correct match scored about 1.0 on a small
bank and collapsed to 0.003 on a bank holding 26+ near-identical cases, while its `semantic` score
stayed informative. One absolute `final` threshold therefore discards correct matches as the bank
grows. So each signal gets one job: `final` decides ordering and is the primary acceptance signal,
while `semantic` may only *admit* a case when `final` has collapsed, never reorder.

```python
def effective_score(item, policy):
    final = float(item.score_final)
    if final >= policy.min_final_score:
        return final, "final"                            # ordering + acceptance
    semantic = item.score_semantic
    if isinstance(semantic, (int, float)) and not isinstance(semantic, bool):
        return float(semantic), "semantic_fallback"     # admission only
    return final, "final"
```

The fallback floor started at 0.70, and on a fresh-bank rehearsal that admitted a `batch-runner` case
as `relevant` to a billing issue (`semantic` 0.7084) and a `media-uploader` case to an `orders-api`
issue (0.7016) — both then cited in proposals. We raised it to 0.75, configurable via
`DEBUGAGENT_SEMANTIC_FLOOR`.

We are **not** claiming 0.75 is validated. It rejects every unrelated case we measured, including the
two that got through at 0.70 — and it also rejects a low-information synthetic self-match measured
around 0.736–0.746. That is the cost of the number, resting on a handful of measurements from a
six-case bank rather than a labelled evaluation set. All five thresholds are configurable via
`DEBUGAGENT_*` variables; code and docs both call them calibration parameters, not constants.

## Model fallback

The LLM call goes to a primary provider with a fallback behind it, and every proposal section prints
which one answered:

```
served by openrouter / stealth/space-bunny-alpha · fallback_used=False
```

If the primary times out or returns unusable output the fallback answers, and the header says so —
three of our four Act 4 runs were primary, one fallback.

## Takeaways

1. **Make the trust boundary structural, not instructional.** Removing the channel by which memory
   reaches evidence did more than any prompt wording. An instruction is a request; an absent field is
   a guarantee.
2. **Test against a real, fresh bank.** Every behavioural bug we found passed unit tests with a fake
   client.
3. **Give each score one job.** Ordering and admission are different decisions; mixing them is how a
   collapsed score drops a correct match and admits a wrong one.
4. **Store what cannot be regenerated in structured metadata.** Extracted text is for recall;
   load-bearing fields belong somewhere verbatim.
5. **"I don't know" is a feature.** A visible abstention with a reason is what makes an engineer trust
   the times the system *does* recall something.

## Closing

A useful debugging agent should not merely remember answers. It should remember engineering
experience — including the approaches that failed — while keeping that experience strictly separate
from the evidence about today's incident.

Everything above is a command-line agent on synthetic incidents with 254 automated tests. We quote no
accuracy or performance benchmark, because we have not measured one. The claim is behavioural and
narrow: *the second investigation was informed by the first, and nothing the agent remembered was ever
treated as proof.*

---

# Submission package

## A. Title options

1. **Why a debugging agent should remember experience, not answers**
2. The agent that said "no memory" — and was right
3. Query-relative scores nearly broke our debugging agent's memory
4. Building a debugging agent where the model can never cite a case that wasn't recalled
5. Our fake Hindsight client made 254 tests pass and hid every bug that mattered

*Selected: **1** — it states the architectural claim, makes no accuracy promise, and does not depend
on the reader caring about retrieval scores.*

## B. LinkedIn post

We built a command-line debugging agent on Hindsight and spent most of the engineering on one
question: what is memory allowed to influence, and what is it forbidden to touch?

The evidence object has no input that accepts recalled content — the boundary is structural, not a
prompt instruction. A hypothesis citing a case that wasn't actually recalled is treated as invalid
structured output, retried, then failed over.

Full write-up: [ARTICLE_URL]

## C. X / Twitter post

Built a debugging agent on Hindsight where memory can inform but never prove.

The evidence object has no field that accepts recalled content. A hypothesis citing a case that wasn't recalled is invalid output → retried → failed over.

Every behavioural bug passed unit tests with a fake client and failed on a fresh real bank.

[ARTICLE_URL]

## D. GitHub project description

A command-line debugging agent that recalls past incidents from Hindsight, proposes hypotheses, and
lets the engineer verify them against current evidence. Memory informs, evidence verifies, the agent
proposes, the engineer decides.

## E. Tags and keywords

`Hindsight` `agent-memory` `retrieval` `debugging` `LLM agents` `abstention` `trust-boundary`
`AI safety` `RAG` `developer-tools` `Python`

## F. Thumbnail concept

Dark terminal screenshot, slightly angled, showing the two lines that carry the argument:
`No usable memory: no recalled case reached the usable floor.` above
`MEMORY informs · EVIDENCE verifies · AGENT proposes · ENGINEER decides`.
High contrast, one short bold line of text — *"it remembered — then said no"* — in the lower third.
No faces, no stock imagery, no robot iconography. The terminal is the subject.
