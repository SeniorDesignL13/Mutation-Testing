# v1 Evaluation Corpus — Selection and Freeze
MTLJ-44 · Ethan Goedelman · 2026-10-19

## What we froze

**RAGTruth QA**, filtered to GPT-4 responses that human annotators marked
hallucination-free and rated good quality. **919 records**, frozen at
`corpus/v1/corpus_v1.jsonl`, SHA-256
`aceb7c3063361ba3141ba15c68aba531bca0e3855c9d7f099c4a782b9608323c`.

Audits draw a seeded sample from this pool rather than running the whole thing.
The seed is recorded in run provenance, so any audit is reproducible from its
manifest without pinning a fixed subset in advance.

## Why RAGTruth

We evaluated four candidates in MTLJ-40: RAGTruth, HaluEval, HotpotQA, and
FEVER, on size, licensing, answer structure, and whether the provided context
genuinely determines faithfulness.

**Answer length decided it.** RAGTruth's QA answers run about seven sentences
with roughly 1,200 characters of context. HaluEval's QA subset is built on
HotpotQA, so its answers are short spans with about 320 characters of context.
That difference is not cosmetic: you cannot pad or reformat a two-word answer.
Two of our three v1 operators would have had nothing to work with.

Three other factors confirmed the choice:

**Licensing.** MIT, which permits our use without restriction.

**Human-verified clean originals.** Our mutations assume a faithful starting
point — an answer that was already partly unfaithful produces a meaningless
delta. RAGTruth annotates hallucinated spans by hand, so filtering to records
with no annotations gives us originals a human already certified, rather than
originals we assumed were fine.

**The annotation taxonomy mirrors our operators.** RAGTruth's "Evident Conflict"
category is our entity substitution operator occurring naturally in real model
output. One of their annotated examples records a response stating "highest 75%"
where the source said "lowest 75% or highest 25%." We can therefore argue our
synthetic mutations reproduce failure modes human annotators independently
identified in production output — a direct answer to the equivalent-mutant
objection that mutation testing conventionally faces.

## Filter chain

Reproduced exactly by `scripts/freeze_corpus.py`.

| Step | Records remaining |
|---|---|
| All records in `wandb/RAGTruth-processed` (train + test) | 17,790 |
| `task_type == QA` | 5,934 |
| No hallucination annotations | 4,210 |
| `model == gpt-4-0613` | 947 |
| `quality == good` | 919 |
| Non-empty question, answer, and context | **919** |

**Why GPT-4 only.** RAGTruth's six generating models hallucinate at very
different rates — GPT-4 at 4.4%, Llama-2-7B at 51.2%. Restricting to GPT-4 gives
the cleanest pool of verified-faithful answers and removes generating model as a
confound. A mixed pool would leave us unable to tell whether a judge's behavior
tracked our mutation or the original answer's provenance.

**Why `quality == good`.** Drops 27 incorrect refusals and 1 truncated response.
A refusal has no factual claims to mutate, so it would silently produce a
zero-delta mutant that looks like a judge miss.

## Composition

| | |
|---|---|
| Records | **919** |
| Median answer length | 99 words |
| Answer length range | 7–237 words |
| Median context | 1,213 characters |
| Passages per record | 3 |
| Gold set | 20 records |

**Context handling.** Each record's context is three passages, split on blank
lines. The split is lossless — no text is modified at any point in the freeze.

**The gold set.** The 20 records hand-mutated and human-reviewed during the
MTLJ-31 spike are tagged `gold_set: true` in the corpus. Their 60 hand-written
mutations, with measured per-record score deltas, serve as the validation target
for the automated entity-substitution operator (MTLJ-60): does the automated
operator produce mutants the judge catches at a comparable rate?

## Freeze mechanics

Records are sorted by `id` before writing, so re-running the script on the same
dataset version produces a byte-identical file and the same checksum. The
manifest at `corpus/v1/manifest.json` records the filter chain, resulting counts,
composition statistics, and the digest.

Verify at any time from the repo root. The checksum file names a bare
`corpus_v1.jsonl`, so the check has to run inside `corpus/v1/`:

```
(cd corpus/v1 && sha256sum -c corpus_v1.sha256)
```

**A new corpus gets a new version directory.** `corpus/v1/` is never edited in
place. If we need different records, that is `corpus/v2/` with its own manifest
and a note on what changed and why. Results reference the corpus version they
were produced against, so a result from October and one from March are never
silently comparing different data.

## Known limitations

**Single domain.** MS MARCO questions are everyday web-search queries — cooking,
home repair, consumer health. Findings may not transfer to technical, legal, or
clinical content, where claim density and the judge's domain knowledge both
differ.

**Single generating model.** Restricting to GPT-4 buys clean originals at the
cost of answer-style diversity. A judge might behave differently on the more
verbose or hedged output of a weaker model.

**Clean is not uncontested.** The MTLJ-31 spike found that DeepEval's
faithfulness judge ruled at least one claim unfaithful in 32% of scorings of
these human-certified originals. Our "clean" pool is clean by human annotation,
not by judge agreement — which is itself one of the findings the tool exists to
surface, but it means per-record baselines vary more than a naive reading would
suggest.

**Size.** 919 records is enough for the SD1 prototype and the planned audit, but
it bounds the precision of any per-operator rate we report. Confidence intervals
are reported alongside every rate for that reason.
