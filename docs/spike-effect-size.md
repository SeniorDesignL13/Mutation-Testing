# Spike: Effect-Size Feasibility Study
MTLJ-31 · Ethan Goedelman · 2026-10-11

## Question
Does a judge's score move more when we corrupt an answer than it moves on its own across
repeated runs of identical input?

**Short answer: yes.** A single wrong fact lowers DeepEval faithfulness by about 3× the judge's
own run-to-run noise, and two changes that should not matter (padding, reformatting) produce no
significant shift. The judge also misses about a third of the wrong facts, which is the kind of
weakness mtlj exists to surface.

## Method
**Records.** 20 records from RAGTruth QA (`wandb/RAGTruth-processed`, train + test), GPT-4
responses with no hallucination annotations.

| Filter | Records left |
|---|---|
| All records | 17,790 |
| `task_type == QA` | 5,934 |
| No hallucination annotations | 4,210 |
| Model `gpt-4-0613` | 947 |
| `quality == good` (not in the original brief: drops 27 incorrect refusals, 1 truncated) | 919 |

From these, shuffled with seed 31, the first 20 whose answer has at least 3 sentences and at
least one spaCy (`en_core_web_sm`) entity that appears verbatim in the context. Two records were
skipped by hand because their only in-context entity made a weak swap target (14088, 16278).
Context is the record's three passages, split on blank lines (lossless). No text was modified.

**Mutations.** 3 per record, 60 in total:
- **entity_swap** (should lower the score): one value replaced with a plausible, same-type value
  that the context contradicts, e.g. "1 cup of water" → "3 cups". Full list in the appendix.
- **reformat** (should not): prose → `- ` bullets, one sentence per bullet. With the bullets
  removed, the word sequence is identical to the original (checked by script).
- **verbosity_pad** (should not): one hand-written, topic-neutral filler of 89 words (45 before
  the answer, 44 after), added identically to all 20 answers by a deterministic script. It talks
  about the response, not the topic, and has no names or numbers. Answers stay one paragraph.

All mutation text, including the filler, was written by hand. No language model generated any
mutation text. Claude (an AI coding assistant) wrote the tooling (record selection, the editing
workbench, the mutation checker, scoring and analysis scripts) and reviewed each swap against
its context, flagging invalid ones (synonyms, weaker claims, values another passage supports) and
in some cases pointing to a better entity to target.

**Scoring.** DeepEval 4.2.8 `FaithfulnessMetric` with `gpt-4o-mini` (DeepEval's default
temperature 0, `eval_mode="llm"`, `include_reason=False`, which doesn't affect the score). Input =
the question, retrieval context = the three passages. Every original and every mutant scored 5×,
under the default config and under `penalize_ambiguous_claims=True`: 80 texts × 5 × 2 = 800
scorings, 0 failures. 400 scorings took 6.7 minutes at 4 concurrent calls.

**Statistics.** Noise floor = mean across originals of the within-text SD of their 5 scores.
Delta = mean(original) − mean(mutant) per record, averaged across records (positive = mutant
scored lower). Effect size = delta ÷ noise floor. Implied N = ceil(8 × (noise ÷ delta)²).
95% CI = paired t-interval over the 20 per-record deltas. The swap is judged on effect size;
pad and reformat, which should not move the score, are judged on whether their CI includes 0.

## Results
**Default config.** Noise floor 0.028 · 7 of 20 originals varied at all across 5 runs ·
10 distinct score values across originals (quantization level) · mean original score 0.954

| Mutation type | Delta | 95% CI | Effect size | Implied N | Verdict |
|---|---|---|---|---|---|
| entity_swap | **+0.088** | [+0.045, +0.130] | 3.11 | 1 | Strong |
| verbosity_pad | −0.005 | [−0.027, +0.017] | −0.18 | — | No significant shift |
| reformat | −0.018 | [−0.048, +0.013] | −0.64 | — | No significant shift |

**`penalize_ambiguous_claims=True`.** Noise floor 0.038 · 9 of 20 originals varied ·
22 distinct values · mean original score 0.781

| Mutation type | Delta | 95% CI | Effect size | Implied N | Verdict |
|---|---|---|---|---|---|
| entity_swap | **+0.093** | [+0.023, +0.164] | 2.48 | 2 | Strong |
| verbosity_pad | −0.015 | [−0.052, +0.022] | −0.41 | — | No significant shift |
| reformat | −0.008 | [−0.040, +0.023] | −0.22 | — | No significant shift |

**What the judge did with each text** (default config, all samples):

| Text | Claims extracted per answer | yes | borderline | no |
|---|---|---|---|---|
| original | 7.9 | 79% | 16% | 5% |
| entity_swap | 7.6 | 68% | 18% | 13% |
| reformat | 6.6 | 79% | 18% | 3% |
| verbosity_pad | 7.8 | 79% | 16% | 5% |

Other observations:
- **About a third of swaps were missed.** 13 of 20 swaps lowered the default score by more than
  the noise floor; 7 did not move it (records 05, 07, 08, 10, 13, 15, 18). In several misses the
  judge did react, rating the swapped claim "borderline" instead of "no", which the default
  config counts as faithful. Record 05 (2010 → 2020) went from 0 to 15 borderline verdicts while
  its score stayed at 1.0; the penalize config caught it (delta 0.50).
- **The judge ignored the padding.** Padded answers yielded the same number of claims and the
  same verdict mix as the originals: the filler was never treated as a claim.
- **Bullets changed claim segmentation, not judgement.** Reformatted answers yielded 6.6 claims
  vs 7.9, with an unchanged verdict mix. Individual records moved by up to 0.19 in either
  direction, but not consistently.
- **Clean answers are not judged clean.** The judge ruled at least one claim "no" in 32% of
  scorings of the (human-annotated, hallucination-free) originals.
- **Penalize config is harsh on originals.** Mean 0.78 vs 0.95, and one original (record 18)
  averages 0.07. Its per-record swap deltas scatter more (SD 0.15 vs 0.09).
- **Record 18 doesn't change the result.** Its swap changes an action ("copy to" → "delete from")
  rather than a named entity. Excluding it: effect size 3.33 (default) / 2.85 (penalize); swaps
  caught unchanged.

## Finding
The premise holds, in both directions. With DeepEval faithfulness on gpt-4o-mini, one
hand-written factual contradiction lowers the score by 0.088 on average, about 3× the judge's
run-to-run noise (0.028) and clearly separated from zero (95% CI 0.045–0.130). Two changes that
add no information, padding and reformatting, produce no significant shift, and under the default
config their intervals don't overlap the swap's. The judge's noise is real but small and cheap to
average out. The catch is that the average hides a split: the judge clearly caught 13 of 20 swaps
and missed 7, mostly by filing the wrong claim as "borderline", which the default config forgives.
More samples cannot recover a systematic miss, so a mutation score from this judge will undercount
by roughly that miss rate. That is a concrete, explainable weakness in a widely used judge, and
exactly what mtlj is meant to find.

## Implications for the build
- **Sample count N = 3–5 for degrading operators.** The formula gives 1–2, but it only accounts
  for run-to-run noise; per-record effects vary about as much as the mean effect, so each mutant
  should be tested on its own rather than only on average.
- **Record verdicts, not just scores.** A third of swaps show up only as yes → borderline shifts.
  Tracking verdict counts recovers signal the default score throws away.
- **Prefer the default config as the baseline;** run penalize as a second view. Default gives
  higher, steadier originals and a cleaner gap between swaps and controls.
- **Entity-swap operator rules,** learned while reviewing the hand-written swaps. An automated
  operator should enforce them:
  - exactly one change; same type and format; a plausible value
  - the new value must not be supported by any passage (watch for values that appear elsewhere)
  - change the result, not the condition ("if X, then Y": change Y)
  - never a weaker or entailed value ("at least 3" → "at least 1" is still true), and avoid
    hedged sentences ("may be above 50")
  - no synonyms ("scan" → "look", "copy to" → "add to")
  - prefer entities that appear once in the answer
- **Use the 20 hand-written swaps as a gold set** for validating the automated operator: does it
  produce mutants the judge catches at a similar rate?
- **Verbosity is invisible to faithfulness by construction** (it only scores factual claims).
  Test verbosity operators on judges that score the whole answer (answer relevancy, G-Eval-style
  rubrics), where length bias is plausible.
- **Normalize format before comparing claim-based scores.** Bullets changed how many claims were
  extracted, which shifts score granularity.
- **Compute is cheap.** 400 judge calls take about 7 minutes at 4 concurrent calls on gpt-4o-mini.

## Limitations
- 20 records, one judge (DeepEval faithfulness), one model (gpt-4o-mini) at temperature 0
- Mutations hand-written by one person; not second-annotator validated (swaps were reviewed for
  validity with an AI assistant, see Method)
- Faithfulness is quantized by claim count (~8 claims per answer, so one wrong claim moves the
  score by at most ~0.125), limiting score resolution
- One filler text for all pads (89 words, slightly under the ~100 target), placed before and
  after the answer rather than interleaved; another filler might behave differently
- One reformat style (one sentence per bullet)
- Two swaps are imperfect: record 18 swaps an action rather than a named entity, and record 11's
  new value (90 seconds) is partly compatible with passage 3 ("20 seconds or more")
- The plan's effect-size scale uses within-text noise only; CIs are reported alongside it
- Nothing was descoped: all three operators, both configs, 20 records

## Appendix: entity swaps
Δ = original − mutant mean score; positive means the swap lowered the score.

| # | Record | Question | Swap | Δ default | Δ penalize |
|---|---|---|---|---|---|
| 01 | 13122 | how to bake salmon med rare in the oven | oven time 5 → 2 min | +0.167 | +0.000 |
| 02 | 15450 | how often should i change guitar strings | 2 to 3 → 4 to 5 weeks | +0.167 | +0.167 |
| 03 | 16980 | how to name your router linksys | 192.168.1.1 → 192.168.0.0 | +0.143 | +0.036 |
| 04 | 17622 | how to cook cuban boston butt pork roast in oven | 1 cup → 3 cups of water | +0.100 | +0.133 |
| 05 | 13302 | average cost of long term care | 2010 → 2020 | +0.000 | +0.500 |
| 06 | 17388 | the difference between a cold and sinusitis | two to three → four to five days | +0.070 | +0.043 |
| 07 | 13560 | how to grill pork chops, allrecipes.com | stand 10 → 2 min | +0.000 | +0.200 |
| 08 | 15204 | how the colonists respond to the stamp act | British → German goods | +0.000 | +0.000 |
| 09 | 14922 | how to uninstall utorrent windows 10 | "Scan Leftovers" → "Add Leftovers" | +0.057 | +0.143 |
| 10 | 12660 | how do you know if a horse is sick | 99.8 → 97.8 F | +0.000 | −0.033 |
| 11 | 17148 | how to relieve tight calf muscles | hold 30 → 90 seconds | +0.125 | +0.106 |
| 12 | 12852 | how to cook a tenderloin steak in the oven | oven 5 → 15 min | +0.091 | +0.131 |
| 13 | 17490 | how to tie water balloon | 3-6 → 6-8 inches | −0.006 | −0.076 |
| 14 | 17028 | what did the factory system effect | six → four days a week | +0.136 | −0.114 |
| 15 | 11868 | safely clean your computer screen | 1 part → 2 parts alcohol | +0.000 | −0.008 |
| 16 | 17592 | what is different about the hobbit and lord of the rings | Bilbo → Frodo | +0.279 | +0.222 |
| 17 | 14352 | how to boil potatoes for easy peeling | boil 15 → 5 min | +0.033 | +0.167 |
| 18 | 17472 | how can i copy my photo from laptop to cd | copy to → delete from | −0.029 | −0.167 |
| 19 | 12018 | wat is dna | four → 5 types of bases | +0.167 | +0.167 |
| 20 | 16080 | what are the chances of getting hiv after a single encounter | 1% → 3% | +0.250 | +0.250 |

## Reproducing
Everything is in `spike/` (run from there with its venv active). `get_records.py` selects the
records; `make_workbench.py` and `make_pads.py` lay out the editable mutation files in
`workbench/`; `build_mutants.py` checks them and writes `mutants.json`; `run_spike.py` scores
into `scores.csv` (resumable); `analyze.py` prints the tables above. The pad filler is in
`pad_filler.txt`.
