# 10. Statistical Methodology

Every statistic in MTLJ has a written definition, a worked example on real data, the decisions behind it, and tests, kept in `docs/metrics.md`. Each definition was accepted by the whole team before it was implemented. This section summarizes them.

## 10.1 Paired Comparison

Each edited answer is compared with its own original, not with an overall average. Both are scored by the same judge with the same settings and the same number of samples N. This removes differences between records, such as one context being harder to check than another, and gives much more statistical power on a modest corpus. For record r, the paired delta is the original's mean score minus the mutant's mean score, so a positive number means the judge scored the edit lower (MTLJ-34).

## 10.2 Noise Floor

The noise floor σ is how much the judge's score moves on identical input. It is estimated in every run from the repeated scores of the originals, by pooling their variances:

σ = sqrt( sum over r of (N_r − 1) · s_r²  ÷  sum over r of (N_r − 1) )

where s_r² is the sample variance of original r's N_r scores. With equal N this is the square root of the mean variance. It is re-estimated for every run and never hard-coded, so it describes this judge on this sample of the corpus.

The feasibility study first estimated noise by averaging each original's standard deviation. When the metric was formalized, the team switched to pooled variance, because the noise turned out to be concentrated in a few records. One record alone contributed 53% of the total variance, and averaging standard deviations understates that: 0.028 against 0.054 on the same data.

## 10.3 Detection Rate

A degrading mutant counts as **detected** when its paired delta exceeds a threshold τ derived from the noise floor:

τ = z · σ · sqrt( 1/N_o + 1/N_m ),  with z = 1.645 (one-sided, α = 0.05)

If the edit made no difference, the mutant's scores come from the same distribution as the original's, and the difference of the two means has standard deviation σ · sqrt(2/N). τ is that distribution's one-sided 95th percentile, so noise alone gets past it for about 5% of mutants. It is one-sided because a degrading operator predicts a drop; a higher mutant score is never a detection. {tab:tau} shows how τ shrinks as samples increase.

Table: Detection threshold as a multiple of the noise floor {#tau}
| Samples per text (N) | 2 | 3 | 5 | 10 |
|---|---|---|---|---|
| τ ÷ σ (detection, one-sided) | 1.64 | 1.34 | 1.04 | 0.74 |
| τ ÷ σ (spurious sensitivity, two-sided) | 1.96 | 1.60 | 1.24 | 0.88 |

The **detection rate** is detected mutants divided by testable mutants. Three kinds of mutants are excluded from the denominator and counted separately: those the operator did not change, those without a successful score after retries, and those with no headroom (an original whose mean score is already at or below τ, so no drop could exceed it). Each rate also carries a one-sided exact binomial test of the detections against α. With 20 testable mutants, a judge needs 4 detections to be above chance. Being above chance says the judge catches the operator at all, not that it catches it well.

## 10.4 Spurious Sensitivity

Spurious sensitivity is the headline metric for preserving operators: the share of preserving mutants whose score moves, in either direction, by more than noise can explain. This definition changed after the preliminary design document. That document proposed reusing detection rate's one-sided rule, which counts only drops. When the metric was formalized, the team found that a penalties-only rule misses half the problem, since a judge that rewards padding has a length bias just as much as one that punishes it. The threshold is therefore two-sided, with z = 1.960, so noise alone still flags about 5% of mutants in total. Penalized and rewarded mutants are counted and reported separately, because they are different findings about the judge.

Because noise alone flags about 5% of mutants, 5% is the score of a perfect judge, not 0%. Every spurious sensitivity is read against α with an exact binomial test, and a result counts only if the judge is flagged more often than chance.

## 10.5 Confidence Intervals

Every rate gets two 95% intervals. The **Wilson score interval** {ref:wilson} treats each mutant's verdict as fixed and behaves well at 0%, at 100%, and with few mutants. The **record-level bootstrap interval** {ref:efron} accounts for something Wilson ignores: τ is itself estimated from the same records, so a resample that draws a noisy record twice has a larger τ and flips marginal verdicts. The bootstrap resamples records with replacement 10,000 times from seed 0, recomputes σ, τ, and every verdict in each resample, and takes the middle 95% of the resampled rates. The report names which interval to show first: the bootstrap from 100 testable mutants upward, where both cover well and the bootstrap captures more uncertainty, and Wilson below that, where the bootstrap's coverage drops (to about 64% at 20 mutants and a true rate of 5%).

## 10.6 Sample Size

Two different sample sizes matter, and the preliminary design document blurred them. This section separates them.

**Samples per text (N)** is how many times each original and each mutant is scored. It controls how precisely each paired delta is measured, and so how small τ is relative to σ ({tab:tau}). N multiplies every judge call, so it drives cost more than anything else. The feasibility study showed entity swaps moving scores by about three times the noise floor, which five samples resolve comfortably (τ ≈ 1.04 σ). The team adopted N = 5 as the default on October 20, 2026, and checked it against the pilot audits in Sprint 4. Re-cutting the pilot's five recorded samples into every subset of two, three, and four showed that smaller N finds slightly fewer of everything and changes about one per-mutant verdict in ten at N = 3, while no headline rate moves by more than its interval. N = 5 stays the default, and N = 3, which would cut a full run by 40%, is the fallback if the budget requires it.

**Number of pairs (testable mutants per operator and intensity)** controls how precisely each rate and each mean effect is estimated. As a guide for the mean paired effect, a paired test at 80% power and α = 0.05 needs about 34 pairs to detect a medium standardized effect (0.5), about 90 for 0.3, and about 200 for a small effect (0.2). For the rates themselves, the interval width matters more. A full corpus audit has several hundred testable mutants per operator and intensity, which gives Wilson intervals roughly ±3 to ±5 percentage points wide. The 20-record gold set gives intervals of about ±20 points and is meant only for development.

## 10.7 Multiple-Comparison Correction

One audit runs many tests at once, one per operator and intensity, so some will look significant by chance. A judge that ignores padding and formatting entirely, tested on four padding lengths and three reformatting operators, would get seven chances to appear spuriously sensitive. Each result is therefore reported with its raw p-value and its Benjamini-Hochberg adjusted p-value {ref:bh} across every test in the run (MTLJ-36), and a result counts as significant after correction when the adjusted value is below q = 0.05. Benjamini-Hochberg was chosen over Bonferroni and Holm because an audit is a profile across many operators. What a reader needs is that most of the weaknesses shown are real, and Benjamini-Hochberg loses less power as tests are added.

## 10.8 Dose-Response Curve

For an operator run at several intensities, a curve uses all the intensities' mutants at once to answer the question the intensities exist for: how much of an edit the judge can take before it reacts (MTLJ-37). The hit rate at intensity d is modeled as P(d) = α + (1 − α) · ER(d), where ER is a logistic curve in log intensity. The curve's floor is chance, α, because a judge that ignores the edit is still hit about α of the time. The **onset** is the intensity at which the hit rate has risen 10% of the way from α to 1, which is 14.5% at α = 0.05. The onset and slope are fitted by maximum likelihood, the onset gets a profile-likelihood interval, and a Pearson chi-square test checks the fit. {fig:dose_curve} illustrates two judges with different onsets.

![Illustration of the dose-response model: two hypothetical judges with onsets at intensity 0.25 and 0.60. Illustrative curves, not measured data.](diagrams/chart_dose_response.png){width=5.2 #dose_curve}

If the data cannot rule out a judge that never responds, the interval's upper end is infinite and the result says the judge does not respond. If every mutant at every intensity is a hit, the onset is reported as 0, below anything tested.

## 10.9 Worked Example

{tab:worked} applies the formal definitions to the feasibility study's data: the 20 hand-written entity swaps, scored five times each by DeepEval faithfulness on gpt-4o-mini with the default configuration.

Table: Detection under the formal definitions (feasibility study data) {#worked}
| Quantity | Original spike estimate | Formal definition |
|---|---|---|
| Noise floor on clean originals | 0.028 (mean of standard deviations) | 0.054 (pooled variance) |
| Detection threshold at N = 5 | 0.029 | 0.056 |
| Entity swaps detected | 13 of 20 | 12 of 20 |
| Share of total variance from one record | 53% | 53% |
| Scorings where a clean answer had a claim flagged | 32% | 32% |

Reading the table: an entity swap only counts as caught if the judge's average score on it is at least 0.056 lower than its average on the original. Twelve of twenty cleared that bar. The other eight mostly did not move at all, which points to the judge treating the swapped claim as unclear rather than to the threshold being too strict. {tab:rules} shows why the pooled rule was chosen: on preserving edits, which should be flagged about 5% of the time by chance, it comes closest to that rate while still detecting most swaps.

Table: Candidate detection rules on the feasibility data {#rules}
| Rule | Threshold | Swaps detected | Padding and reformat edits flagged |
|---|---|---|---|
| Any drop | 0 | 13 of 20 | 8 of 40 (20%) |
| Fixed drop of 0.1 | 0.100 | 8 of 20 | 1 of 40 (3%) |
| Per-original standard deviation | 0 for 13 of 20 records | 11 of 20 | 6 of 40 (15%) |
| Mean of standard deviations | 0.029 | 13 of 20 | 6 of 40 (15%) |
| **Pooled standard deviation (adopted)** | **0.056** | **12 of 20** | **3 of 40 (8%)** |

# 11. Feasibility Study

## 11.1 Purpose and Method

Before building operator infrastructure, the team ran an eight-hour feasibility study (MTLJ-31) to test the assumption everything else depends on: a judge's score moves more when an answer is broken on purpose than it moves on its own. If a swapped name moved the score by 0.03 while repeated runs varied by 0.08, the signal would be lost in noise and the project would need to change direction.

The study took 20 clean RAGTruth records with at least three sentences and at least one named entity that also appears in the context. For each record, three edits were written by hand: one entity swap, one pad of roughly 100 words, and one reformat. The swaps followed the operator rule of replacing something the context supports with something that contradicts it. All 80 texts were scored five times with DeepEval 4.2.8's faithfulness metric on gpt-4o-mini under both the default and the `penalize_ambiguous_claims` settings, for 800 scorings in total.

## 11.2 Findings

The idea works. Entity swaps moved the score by about three times the judge's noise floor, which is clearly detectable, while padding and reformatting caused no significant change. Under the formal metric (pooled noise floor 0.054, threshold 0.056), 12 of the 20 swaps were detected. The study's rougher original estimate gave 13 of 20, and the conclusion is the same either way.

The study also found three things about the judge itself, a preview of what the full system is meant to produce:

- **Contradictions called "unclear."** The judge missed 8 of the 20 swaps, or 40%, and most misses were not close. The verdicts showed that the judge often marked the changed claim as unclear instead of wrong, and DeepEval counts unclear claims as faithful by default. This is why verdict counts are now saved with every score.
- **False alarms on clean answers.** In 32% of scorings of human-verified clean answers, the judge flagged at least one claim as unfaithful. This is why detection is measured against the judge's own noise rather than a fixed cutoff.
- **Missing verdicts.** In 42 of 800 scorings, DeepEval returned fewer verdicts than claims and scored using only the ones it had. One record produced 15 claims, 14 verdicts, and a score of 13/14. This led to the adapter rule that verdict counts may add up to less than the claim count.

## 11.3 What Changed Because of It

The study supports five samples per text as the default. It confirmed that entity swaps must contradict the source rather than merely add something new, and it led to two data model changes: saving verdict and claim counts with every score, and switching to the pooled noise estimate. It also flagged score saturation as a risk. If clean answers almost always score 1.0, harmless edits can only push scores down, so the system can catch a judge that punishes padding but not one that rewards it. That is documented as a limitation. The study used only 20 records, one judge, one model, and edits written by one person. That is adequate for a yes-or-no feasibility check, and it is exactly what the full system is designed to go beyond.

## 11.4 Pilot Audits

In Sprint 4 the team moved from the hand-built feasibility study to the real system. The pilot audits (MTLJ-89) sized the full study and looked for problems before a long run. They had two parts. The first replayed the recorded gold-set run (20 records, 133 default-battery mutants, five samples each, 765 DeepEval scorings on gpt-4o-mini) through the real engine with no network, and generated every corpus v1 mutant locally to count the planned work exactly. The second was a live run on 20 corpus records per judge configuration. The write-up, data, and analysis script are in `docs/pilot-audits.md`, `docs/pilot/`, and `scripts/pilot_analysis.py`.

> NOTE: The live pilots (the second part) and the Ragas pilot need an OpenAI key and had not run when this section was written. Add their measured latency, errors, rate-limit responses, and tokens per scoring here, and compare them with the replay figures below.

{tab:pilot_size} shows the size of a full audit. {tab:pilot_time} extrapolates its run time and cost. Latency comes from the 765 recorded calls: a mean of 8.46 seconds per scoring (median 8.30, 90th percentile 10.89, maximum 16.86), nearly the same for originals and every operator. It grows with the number of claims the judge has to check (correlation 0.64). Cost is a ceiling from the README's three-record run (at most $0.0013 per scoring), since token counts are not recorded in the fixtures.

Table: Planned work for one full audit of one judge configuration (corpus v1, N = 5) {#pilot_size}
| Part | Texts | Scorings |
|---|---|---|
| Originals | 919 | 4,595 |
| Verbosity padding (four lengths) | 3,676 | 18,380 |
| Reformatting (bullets, markdown, prose) | 1,844 | 9,220 |
| Entity substitution (364 / 151 / 23 at the three intensities) | 538 | 2,690 |
| **Default battery total** | **6,977** | **34,885** |
| With numeric perturbation added (one intensity, see Section 8.4) | 7,376 | 36,880 |

Table: Extrapolated run time and cost of the full audit {#pilot_time}
| Setup | Scorings | Hours at 4 concurrent | Hours at 8 concurrent | Cost (ceiling) |
|---|---|---|---|---|
| One configuration, default battery | 34,885 | 20.5 | 10.2 | $45.50 |
| Both DeepEval configurations, default battery | 69,770 | 41.0 | 20.5 | $91 |
| Both DeepEval configurations, with numeric perturbation | 73,760 | 43.4 | 21.7 | $96 |

The resources are modest: about 1 MB of run directory per 1,000 scorings (about 35 MB for a full run) and a peak of 283 MB of memory for the replay process. The extrapolation assumes calls overlap perfectly and that the provider does not throttle at eight concurrent calls, which only a live run can confirm. The feasibility study ran about twice as fast per scoring, so the times may halve.

The replay also gave the first engine-produced rates. The noise floor was 0.030, about half the feasibility study's 0.054 on the same records, because the judge was nearly deterministic this time: 15 of 20 originals and 100 of 133 mutants scored identically on all five samples, 13 originals at exactly 1.0. The noise floor is re-estimated in every run, and with 919 originals it rests on far more records. Spurious sensitivity was above chance for every preserving operator: 25% for bullets, 35% for markdown, and 19% for padding, against the 5% that noise alone produces. The rate rises rather than falls with N, so these are consistent reactions to formatting rather than noise, and they are likely to be a headline finding of the full study.

The pilots found four problems, ordered by how much they would waste:

1. **Entity swap makes invalid mutants** (Section 8.9). The gold set's 46% detection rate comes entirely from invalid or doubtful swaps, so it does not yet measure how well the judge catches wrong facts.
2. **Numeric perturbation loses two thirds of its intensities** to the repeated-dose rule (Section 8.4).
3. **The CLI did not use the response cache**, and a run that stopped partway had to start again (Section 9.3). Resume (MTLJ-91) and rate limiting (MTLJ-96) were not yet built when the pilot ran.
4. **Verdict counts sometimes fall short of the claim count.** In 43 of 765 scorings (5.6%) the counts sum to fewer than `n_claims`, concentrated in long, padded answers. No score is wrong, because DeepEval divides by the verdicts it returned, but analyses must not use `n_claims` as the denominator.

The pilots' recommendation was not to start the full run until entity swap is fixed or filtered and checked by hand, the response cache is wired into the CLI, and a live pilot has confirmed latency, errors, and rate-limit behavior.

## 11.5 First Full Audit

The team selected the judge population for the study in Sprint 4 (MTLJ-88). It had planned to run the full audit battery (MTLJ-86) and analyze it (MTLJ-87) in the same sprint, but the pilots changed that plan. About 70,000 scorings with an entity swap operator that mostly makes invalid mutants, a CLI that cannot reuse scores it has already paid for, and a numeric operator that loses two thirds of its intensities would have spent the budget on numbers the team would have to discard. The team therefore moved the full audit to the start of Senior Design II, after three fixes in Sprint 5: filtering entity swaps and checking 50 of them by hand, wiring the response cache into `mtlj audit run`, and the repeated-dose fix for numeric perturbation. {tab:judges_selected} lists the judges selected, and {tab:audit_plan} gives the plan for the run.

> NOTE: Fill in the remaining cells of the judges table (the Ragas version and model, and any fourth judge) from MTLJ-88. Do not leave any "fill in" cell in the submitted document.

Table: Judges selected for the full audit (MTLJ-88) {#judges_selected}
| Judge | Framework and version | Model | Settings | Why it was chosen |
|---|---|---|---|---|
| DeepEval faithfulness | DeepEval 4.2.8 | gpt-4o-mini | default | Widely used; claim-by-claim design |
| DeepEval faithfulness | DeepEval 4.2.8 | gpt-4o-mini | penalize_ambiguous_claims | Tests the setting that changes how unclear claims count |
| Ragas faithfulness | (fill in) | (fill in) | default | Same metric, different implementation |
| (fill in) | (fill in) | (fill in) | (fill in) | (fill in) |

Table: Plan for the first full audit {#audit_plan}
| Item | Plan |
|---|---|
| When | Sprint 6 (January 12 to 26, 2027); findings write-up in Sprint 7 |
| Prerequisites | Entity swap filtered and 50 mutants checked by hand; response cache wired into `mtlj audit run`; numeric perturbation repeated-dose fix; a live pilot with no rate-limit errors |
| Scope | Corpus v1 (919 records), default battery plus numeric perturbation once fixed, N = 5, seed 0, every judge in the table above |
| Size | About 35,000 scorings per judge configuration |
| Time | About 20 hours per configuration at four concurrent calls, or 10 at eight |
| Cost | At most about $46 per configuration at the pilot's cost ceiling |
| Output | Run directories and results.json per judge, the findings write-up (MTLJ-87), and the mutants the validation study samples |

The first full audit's results will be preliminary, because the human validation study runs after it and may fix or drop operators. Because every operator carries a version and every mutant ID includes it, any operator change produces new mutants that can never be confused with the first audit's. The final audit in March re-runs the battery with the validated operators and supersedes the first one. The first audit's value is that it exercises the full pipeline at scale and gives the validation study real mutants to sample.

# 12. Validating the Ground Truth

## 12.1 Why Validation Is Required

The whole system is only as good as its labels, so the labels are checked directly. An edit can look right and still not change the meaning the way the operator assumes: a swap into a sentence the source never supported, a mislabeled entity such as the gold-set examples in Section 8.9, a reformat that splits a claim differently, or filler that slips in a new claim. Any of these gives an edit the wrong expected direction. The team therefore treats human validation as required and has agreed it will not be cut even if other work is.

## 12.2 Study Design

The study is planned for the first half of Senior Design II and has its own epic (MTLJ-7). It samples mutants from the first full audit, so reviewers see exactly the edits the judges were scored on:

1. **Write the instructions (MTLJ-47).** Define what reviewers check for each operator. For degrading edits: does the answer now actually contradict or misstate the source? For preserving edits: is every claim and its support unchanged?
2. **Draw the sample (MTLJ-45).** At least 100 edits, stratified across every operator and intensity, chosen with a fixed seed.
3. **Review (MTLJ-46).** All four team members take part, and each edit is labeled independently by at least two people who do not see each other's labels.
4. **Measure agreement (MTLJ-50).** Cohen's kappa {ref:cohen} for pairs of reviewers, or Krippendorff's alpha {ref:krippendorff} when more than two reviewers label an item, computed per operator family to show the review itself is reliable.
5. **Score each operator (MTLJ-48, MTLJ-49).** Report the share of each operator's edits whose expected direction reviewers confirmed. Operators below the agreed bar are fixed and rechecked, or dropped, before the full audit.

{tab:validation} lists the planned acceptance bars. The team will confirm them before the first review round, so they cannot be tuned after the results are known.

Table: Planned acceptance bars for the validation study {#validation}
| Measure | Planned bar | Action if missed |
|---|---|---|
| Inter-reviewer agreement per operator family | Kappa or alpha of at least 0.6 | Clarify the instructions and relabel |
| Expected direction confirmed, degrading operators | At least 90% of reviewed edits | Fix the operator or the validity filter, then re-sample |
| Expected direction confirmed, preserving operators | At least 90% of reviewed edits | Fix the operator, report as a curve only, or drop it |
| Sample size per operator | At least 10 edits, at least 100 in total | Draw more before deciding |

> NOTE: The acceptance bars in this table are proposals. Confirm them as a team before Sprint 6 and record the decision in docs/metrics.md.

## 12.3 Preserving Edits

Preserving edits get extra attention, because that is where the labels are easiest to argue with. Heavy padding could reasonably be said to make an answer worse, even if it adds no claim. The design limits this risk by building filler only from the record's own context and fixed neutral phrases, and by reporting these operators as curves across strengths rather than a single pass or fail. A judge that reacts only at 400 words of filler is behaving very differently from one that reacts at 50.
