# Hindsight remembered the failed fix. My dedupe threw it away.

My debugging agent recalled exactly the right past case, then recommended raising a timeout. That
past case recorded "raised the client timeout" as the thing that *didn't* work. The memory was fine.
The bug was in the ten lines of my code that sat between the memory and the model.

This is the story of how that happened, what [Hindsight's agent memory](https://hindsight.vectorize.io/)
actually stores when you retain something, and the one rule I ended up building everything around:
**memory informs, evidence verifies, the agent proposes, the engineer decides.**

## What the agent does

It's a command-line assistant for an engineer debugging a production issue. You describe the
problem (typed, or as a JSON/YAML file), and one session runs a fixed loop:

1. **Normalize** the description deterministically: service, runtime, proxy and region are pulled
   out of `key=value` text; nothing the engineer didn't say is invented.
2. **Recall** similar past cases from a Hindsight bank and classify each one: relevant, partial,
   contradictory, irrelevant or stale. If nothing clears the bar, the agent says *"No usable memory"*
   instead of guessing.
3. **Collect current evidence** from the engineer, and only from the engineer.
4. **Propose** two or three ranked hypotheses in one structured LLM call. Each may cite only a case
   that was actually recalled.
5. **Verify**: the engineer checks each hypothesis against current evidence and decides.
6. **Retain** the outcome, including what failed, but only if the engineer reports a resolution.

The output is split into four sections that never blur: MEMORY (what happened before), EVIDENCE
(what is true now), PROPOSAL (what the agent suggests, always marked unverified) and DECISION (what
the engineer decided).

Hindsight is the memory layer. My teammate Rama built the store around the
[Hindsight client](https://github.com/vectorize-io/hindsight); I built the pipeline, the LLM layer and
the CLI. We met at one small interface, `MemoryPort`, which hands the pipeline plain dictionaries
and keeps every Hindsight type on Rama's side of the line.

## The bug: memory is facts, not documents

When you `retain` a debugging case, I assumed Hindsight stores the text I gave it and hands it back.
It does something more useful: it extracts the text into separate facts. One retained case came back
from recall as several rows: one for the symptom, one for the root cause, one for the fix, one for
the failed approach. Each row had its own score and the same case key in its metadata.

Early on we'd seen recall return 35 rows for six stored cases, so the store collapsed rows to one per
case: keep the best-scoring row, drop the rest. That's reasonable if the rows are duplicates. They
weren't. The best-scoring row for an upload question was almost always the **symptom**, because it's
the fact that looks most like the question. The fix and the failed approach were thrown away before
the prompt was ever built.

So the model saw "media-uploader resets connections on uploads over 2 MB behind nginx", with no
resolution and no warning, and did what a reasonable model does: suggested looking at timeouts.

Unit tests never caught this. Our fake Hindsight client returned one row per case, so every test was
green. It only showed up in an end-to-end run against a real, freshly seeded bank.

The first fix was to stop throwing facts away. Scores and ordering still come from the best row, but
the text is every distinct fact of the case:

```python
def dedupe_by_case(items: list[RecallResult]) -> list[RecallResult]:
    rows: dict[str, list[RecallResult]] = {}
    for item in items:
        rows.setdefault(item.case_id, []).append(item)
    collapsed = []
    for case_rows in rows.values():
        ordered = sorted(case_rows, key=lambda r: (r.score_final, r.score_semantic or 0.0), reverse=True)
        facts = list(dict.fromkeys(t for t in (r.text.strip() for r in ordered) if t))[:MAX_FACTS_PER_CASE]
        collapsed.append(replace(ordered[0], text=" | ".join(facts)))
    return collapsed
```

The next run recalled the root cause and the fix. It still didn't recall the failed approach.
Fact extraction is LLM-driven, so each bank extracts slightly differently, and in that bank the
failed approach simply wasn't one of the facts. No recall setting brings back a fact that was never
stored.

The second fix was to stop depending on extraction for anything an engineer can't do
without. Hindsight metadata is stored verbatim, so the failed approaches go there at retain time:

```python
if case.failed_approaches:
    metadata["failed_approaches"] = "; ".join(
        f"{f.approach} (why it failed: {f.why_failed})" for f in case.failed_approaches
    )[:MAX_FAILED_APPROACHES_CHARS]
```

At recall time it's appended once per case, after dedupe, as `Failed before: …`. The same change
moved `proxy` and `region` into metadata, because extraction had been dropping those too.

## Memory is not evidence

The failed-approach bug made me more careful about the opposite failure: a strong memory match
talking the agent into something. Two rules in the code carry most of the weight.

First, a hypothesis can only cite cases the memory layer actually returned. The check runs inside the
LLM call, so a made-up case id counts as invalid output: it's retried, then sent to the fallback
model, and never shown to the engineer:

```python
def check(data: dict) -> list[str]:
    return [f"hypotheses[{i}] cites {cid!r}, which is not a recalled citable case"
            for i, h in enumerate(data["hypotheses"]) for cid in h["supporting_case_ids"] if cid not in allowed]
```

Second, memory never fills a gap in current evidence. If the cited past case depended on a runtime
and the engineer hasn't said what runtime is running now, the result is `insufficient_evidence`, even
if the match is perfect and the engineer claims it's supported:

```python
# memory never fills a gap: missing evidence means insufficient_evidence, however strong the match
status = "insufficient_evidence" if missing else decision.claim
```

The `Evidence` model has no input that accepts recalled content at all. The model never labels its own
trust level either: whether a hypothesis is "memory-backed" or "generic" is derived in code from the
classes of the cases it cites.

## What it looks like now

The clearest before/after is the same issue twice.

**First time.** *billing-service writes duplicate invoices when the job is retried.* The bank holds six
past cases, none about billing. MEMORY says *"No usable memory"*. Every hypothesis is labelled
`generic · no memory used`. I verify, decide, and record what fixed it: an idempotency key with a
unique constraint. I also record what failed first: wrapping the insert in a transaction, because
the retry opened a new transaction and inserted again. That goes into Hindsight.

**A few minutes later**, the same class of issue comes back: *billing-service creates duplicate invoice
rows after the nightly job retries.* This time MEMORY shows the case from the first session, with its
root cause and fix, ending in:

```
Failed before: wrapped the insert in a transaction (why it failed: the retry opened a new
transaction and inserted again)
```

The first hypothesis is marked `memory-backed`, cites that case, and starts from the idempotency
key. The second investigation is informed by the first. That, not a benchmark number, is the claim.

## Lessons

1. **Find out what your memory layer actually stores.** Hindsight turned my cases into facts, which is
   more useful than storing text blobs, but only if the code downstream expects facts. Everything I
   assumed was a duplicate was information.
2. **Put must-survive fields in structured metadata.** Anything the agent can't work without goes in
   verbatim; extracted text is for recall, not for guarantees.
3. **Test against a real, fresh bank.** Every bug in this story passed unit tests with a fake client
   and failed an end-to-end run on a fresh Hindsight bank.
4. **Make "I don't know" a first-class output.** "No usable memory" and `insufficient_evidence` are
   the two answers I trust most, because the agent can't bluff its way past either.
5. **Budget for reasoning tokens.** On a separate note: both models I tried spent their whole
   3,000-token budget reasoning and returned no JSON until I turned reasoning down per provider. Local
   schema validation was the only reason I noticed instead of shipping empty answers.

If you're building agents that are supposed to learn from past work, the [agent memory
primer from Vectorize](https://vectorize.io/what-is-agent-memory) is a good place to start, and the
[Hindsight repository](https://github.com/vectorize-io/hindsight) has the client I used.

*Read [the code on GitHub](https://github.com/RAMA-L7/hackwithhyderabad-3). Built with Hindsight. Tagging Code.in.*
