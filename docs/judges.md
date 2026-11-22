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

`ragas-faithfulness` runs Ragas at temperature 0 (Ragas's own default is 0.01, like DeepEval's
temperature of 0 it makes scoring near-deterministic) and allows 4096 tokens per LLM call (Ragas
defaults to 1024, which can cut off the verdicts for a long answer). Both are in the judge's
`cache_settings()`, so the response cache never mixes configurations.

Ragas 0.4.3 doesn't import with the `langchain-community` in `uv.lock` (it imports a module that
package removed). The adapter works around it; see `src/mtlj/judges/ragas/faithfulness.py`.
