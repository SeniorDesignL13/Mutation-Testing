# Judges

`mtlj audit run --judge <name>` audits one of these. `--model` sets the OpenAI model, default
`gpt-4o-mini`. All three need `OPENAI_API_KEY` (see [How to develop](DEVELOPER.md)).

| Name | Metric | Notes |
|---|---|---|
| `deepeval-faithfulness` | DeepEval `FaithfulnessMetric` | "borderline" verdicts count as faithful |
| `deepeval-faithfulness-penalized` | the same, `penalize_ambiguous_claims=True` | a different judge: borderline counts as unfaithful |
| `ragas-faithfulness` | Ragas `Faithfulness` (`ragas.metrics.collections`) | no "borderline" verdict |

A detection rate belongs to a judge *and* its config, so each name is audited and reported
separately. Each result records the judge's name, model and library version.

## Ragas faithfulness versus DeepEval's

Both score "how much of the answer does the context support" from 0 to 1, as a share of the
answer's claims. They are different implementations, so the same record can score differently.
From the installed sources (Ragas 0.4.3, DeepEval 4.2.8):

| | DeepEval | Ragas |
|---|---|---|
| LLM calls per scoring | 3 (truths from the context, claims from the answer, verdicts) | 2 (statements from the answer, verdicts) |
| Verdicts are checked against | the "truths" the LLM first extracted from the passages | the passages themselves, joined with newlines |
| Verdict values | yes, no, borderline | 1 or 0 only |
| Borderline | counts as faithful, unless `penalize_ambiguous_claims` | doesn't exist; the adapter reports `borderline = 0` |
| A claim with no verdict | skipped; the counts may sum to less than the claims | skipped the same way, and the score is over the verdicts returned |
| An answer with no claims | 1.0 | NaN, which the adapter raises as an error rather than score |

Consequences for reading results:

- DeepEval's extra truths step can drop or reword context, which Ragas never does, so a fact the
  extraction misses is unfaithful in DeepEval and may be faithful in Ragas.
- Ragas must call every claim yes or no, so its scores are more binary at the claim level.
- The two judges' verdict counts aren't comparable on `borderline`.

## Adapter settings

`ragas-faithfulness` runs Ragas as it ships: temperature 0.01, the default of Ragas's
`InstructorModelArgs` (a test checks it still is), and Ragas's own top_p of 0.1. DeepEval's
adapter uses temperature 0, so the two aren't identical in sampling either. One setting differs
from Ragas: `max_tokens` is 4096, not Ragas's 1024. On the gold set, the longest answer (45
statements) used about 2000 output tokens across the two LLM calls (estimated with tiktoken), so
a 1024 limit would risk cutting off the verdicts. Temperature and `max_tokens` are in the judge's
`cache_settings()`, so the response cache never mixes configurations.

Ragas 0.4.3 doesn't import with the `langchain-community` in `uv.lock` (it imports a module that
package removed). The adapter works around it; see `src/mtlj/judges/ragas/faithfulness.py`.

## Ragas vs DeepEval on the gold set

Both judges (gpt-4o-mini, 5 samples each) scored the same 153 texts: the 20 gold records and
their 133 default-battery mutants (`tests/fixtures/gold-set/`). The scores are replayed from
`tests/fixtures/judge/deepeval-faithfulness.jsonl` (DeepEval 4.2.8, penalized off) and
`ragas-faithfulness.jsonl` (Ragas 0.4.3, temperature 0.01). Numbers are per-text means over the 5
samples.

| | DeepEval | Ragas |
|---|---|---|
| Mean score, all 153 texts | 0.967 | 0.847 |
| Mean score, 20 originals | 0.970 | 0.973 |
| Mean score, 133 mutants | 0.967 | 0.828 |
| Scorings that are exactly 1.0 | 75.6% | 52.5% |
| Texts whose mean is 1.0 | 64.7% | 43.8% |
| Claims per answer (mean) | 7.4 | 13.9 |
| Scorings with a borderline verdict | 505 of 765 (66%) | none possible |

- **Correlation across the 153 texts:** Pearson -0.07, Spearman -0.04. That is almost entirely
  `verbosity_padding`. Without its 80 mutants (73 texts) it is 0.27 (Spearman 0.25), and on
  the 20 originals alone 0.35. So the two agree weakly even on clean answers, which mostly score
  near 1 for both, leaving little to correlate.
- **Mean difference (Ragas minus DeepEval):** -0.120, mean absolute difference 0.152. On the
  originals it is +0.003; on the mutants -0.139.
- **By operator** (mean DeepEval / Ragas): `entity_swap` (13) 0.936 / 0.829,
  `verbosity_padding` (80) 0.971 / 0.750, `reformat_to_bullets` (20) 0.967 / 0.980,
  `reformat_to_markdown` (20) 0.967 / 0.986.

The finding: `verbosity_padding` is meant to leave a judge's score unchanged, and DeepEval nearly
does (0.970 to 0.971), but Ragas drops it from 0.973 to 0.750. The operator appends filler such
as "To put it another way, this is simply a restatement of the same answer." Ragas splits every
sentence into a statement and marks the filler 0, since the context can't support it, while
DeepEval's claim step mostly leaves it out (6 claims on the first text below, where Ragas has
40). Ragas is the more sensitive to padding; whether that is a defect of the judge or of the
operator's text is for the audit's spurious-sensitivity numbers to say.

The five clearest disagreements, all `verbosity_padding` mutants (DeepEval / Ragas):

| Text | DeepEval | Ragas | Why |
|---|---|---|---|
| `mut_d2ffdfead42c3821` (uTorrent) | 1.00 | 0.24 | Ragas made 40 statements and called 33 of them unsupported filler; DeepEval made 6 |
| `mut_8fe552524d403591` (Linksys) | 1.00 | 0.26 | 46 Ragas statements, 37 unsupported; DeepEval 22 claims, none "no" |
| `mut_6f4d720f778b2197` (care costs) | 1.00 | 0.31 | 26 Ragas statements, 18 unsupported; DeepEval's 6 are all "yes" |
| `mut_77f0b7772312c001` (factory system) | 1.00 | 0.32 | 14 of Ragas's 21 statements are filler it can't support |
| `mut_3c4cc66516427881` (photo to CD) | 1.00 | 0.33 | the same pattern: 17 of 25 unsupported |

This is one set of 20 records, one model and one seed, so read it as a first comparison, not a
verdict on either library.
