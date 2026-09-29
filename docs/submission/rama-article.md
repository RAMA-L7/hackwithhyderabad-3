# Why my Hindsight agent learned to say "no memory"

The same correct match scored **1.08** in one bank and **0.003** in another. Nothing about the match
had changed; the second bank just held more cases. That was the day I stopped treating a memory
score as a measure of relevance, and started building the part of the agent that decides when *not*
to use memory at all.

I built the memory layer of a debugging agent on [Hindsight](https://hindsight.vectorize.io/). An
engineer describes a production issue; the agent recalls similar past cases, proposes hypotheses, and
the engineer verifies them against what's true now. When memory is wrong, the agent doesn't just give
a worse answer. It gives a *confident* worse answer, backed by a citation. So most of my work went
into three questions: when to use a past case, when to refuse, and what to do when past cases
disagree.

## How the memory layer fits

Each resolved investigation is retained as one case with ten required fields: problem signature,
symptoms, environment, evidence references, the investigation trace, failed approaches, root cause,
resolution, outcome and verification notes. Validation fails closed; an invalid case is rejected, not
half-stored. Retention is idempotent through a local ledger keyed on the problem signature and
session.

On the way out, recall goes through three steps before my teammate's pipeline ever sees it:

1. `HindsightMemoryStore.recall()` queries the bank and collapses Hindsight's per-fact rows into one
   entry per case.
2. `classify_candidates()` labels each case **relevant**, **partial**, **contradictory**,
   **irrelevant** or **stale**, and decides whether to abstain, always with a human-readable reason.
3. An adapter turns that into plain dictionaries behind a `MemoryPort` interface, so no Hindsight type
   crosses into the reasoning code.

Hindsight does the hard parts I didn't want to build: fact extraction from free text, semantic and
keyword retrieval, and a hosted bank per project through the [Hindsight
client](https://github.com/vectorize-io/hindsight). What it can't know is my domain's definition of
"relevant enough to act on". That decision is application-side, and it's the rest of this article.

## Scores are query-relative, so abstention is mine

Hindsight documents its `final` score as query-relative, and I measured what that means in
practice. On a five-case bank a good match scored about 1.0. On a bank with 26+ near-identical cases,
a genuinely matching case fell to `final` 0.003, while its `semantic` score (a plain vector
similarity) stayed at 0.78.

One fixed threshold on `final` would have thrown the real match away as soon as the bank grew. So
the policy uses two signals with different permissions: `final` decides ordering and is the primary
acceptance signal; `semantic` may only *admit* a case when `final` has collapsed, never reorder:

```python
def effective_score(item: RecallResult, policy: AbstentionPolicy) -> tuple[float, str]:
    final = float(item.score_final)
    if final >= policy.min_final_score:
        return final, "final"
    semantic = item.score_semantic
    if isinstance(semantic, (int, float)) and not isinstance(semantic, bool):
        return float(semantic), "semantic_fallback"
    return final, "final"
```

If nothing clears the floor, the agent abstains and says so. On a fresh bank, a billing-service
duplicate-invoice question against six unrelated past cases produces *"no recalled case reached the
usable floor; proceeding without historical memory"*, and every hypothesis that follows is labelled
generic. That sentence is the most important output the memory layer has.

## The fallback that let strangers in

The semantic fallback was necessary, and its floor was wrong. On an end-to-end run against a fresh bank, a
nightly batch-job slowdown was admitted as *relevant* to the billing issue with a semantic score of
**0.7084**. An upload case was admitted to an unrelated orders-api issue at **0.7016**. The floor was
0.70.

I didn't want to pick a new number by feel, so we collected every semantic score we had measured with
`final` below its floor:

| Kind of case | Semantic scores measured |
|---|---|
| Unrelated | 0.641, 0.678, 0.7016, 0.7084 |
| Vague question | 0.766 |
| Low-information synthetic self-match | 0.736, 0.739, 0.746 |
| Genuine match, `final` collapsed | 0.78, 0.788 |

A floor of 0.75 rejects every unrelated case I measured, including the two that got through at
0.70. It does **not** keep every genuine match: the synthetic self-match in the third row was
rejected too, and that is the honest cost of the number. Those rows are a handful of measurements,
not a labelled evaluation set — so 0.75 is the *currently configured* policy, configurable through
`DEBUGAGENT_SEMANTIC_FLOOR`, and still a provisional one. With it, two end-to-end runs on new banks
passed all four demo scenarios. Turning it into a defensible threshold means a labelled calibration
set per domain, which is the next thing I'd build.

## When history disagrees

Two past `orders-api` cases in our bank share the same symptom (large payloads fail) with different
root causes: a proxy request-size limit, and an upstream dependency returning 502. Picking the
higher-scoring one would be the agent resolving an argument it has no evidence for.

So a conflict is defined as the same service with materially different root causes. The cases in
that conflict are marked `contradictory`, each names the other, and if no side clearly leads, the
layer abstains and explains the margin. How many of them reach the engineer as *candidates* depends
on the scores: on a fresh bank one side's `final` can collapse far enough that its `semantic` falls
below the floor, and it is then shown as excluded rather than as a candidate. The layer still never
picks a side — that is the part worth relying on, not the count.

"Materially different" needed its own rule. The root-cause key is derived text, and a live run
stored a paraphrase of an existing cause, which the layer then reported as disagreeing with itself.
Two causes that share enough of their content words are now treated as one:

```python
def same_cause(left: str, right: str, threshold: float) -> bool:
    left_tokens, right_tokens = tokenize(left), tokenize(right)
    if not left_tokens or not right_tokens:
        return False
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens) >= threshold
```

Contradictions are also limited to services the engineer actually named. A disagreement between two
orders-api cases isn't actionable when you're debugging media-uploader, and reporting it there was
just noise.

On a live bank, the orders-api question surfaces that conflicting history, and each surfaced case
points at the other. Any hypothesis that leans on one side is labelled *past cases disagree*, and
without current facts neither side can be verified.

## What I'd tell someone starting

1. **Measure your memory layer's scores on a big, messy bank before you pick a threshold.** Every
   behavioural bug I found appeared only on a dirty or large bank; isolated tests passed.
2. **Give each score one job.** Ordering and admission are different decisions. Mixing them is how a
   collapsed `final` score silently drops a correct match.
3. **Abstention needs a reason string.** "No usable memory" plus *why* is what makes an engineer trust
   the times it does recall something.
4. **Treat disagreement as information.** Surfacing a conflict with its conditions is more useful
   than any tie-break rule.
5. **Fresh banks are not identical.** Hindsight extracts facts with an LLM at retain time, so two fresh
   banks seeded with the same six cases score slightly differently. Test on a new bank every time,
   and give the idempotency ledger its own directory per bank. Ours isn't keyed on bank id yet, and
   reusing it once left a "fresh" bank empty.

## Where it's thin

A second-domain probe (VLSI design issues) kept the trust boundary intact: no memory leaked into
evidence, and every citation stayed tied to a recalled case. The retrieval didn't generalise. Only
four environment keys survive into metadata, so a VLSI node, PVT corner or tool stage is lost before
comparison, and the semantic fallback admitted unrelated designs. Domain-neutral metadata and a
labelled calibration set per domain are next.

If agent memory is new to you, Vectorize's [overview of agent
memory](https://vectorize.io/what-is-agent-memory) is a good starting point, and the [Hindsight
documentation](https://hindsight.vectorize.io/) covers the retain and recall APIs this is built on.

*Read [the code on GitHub](https://github.com/RAMA-L7/hackwithhyderabad-3). Built with Hindsight. Tagging Code.in.*
