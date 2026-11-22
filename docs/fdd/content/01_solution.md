# 3. Background and Related Work

## 3.1 Mutation Testing

Mutation testing is an established technique for judging the quality of a test suite. A tool makes many small changes to a program, such as flipping a comparison or deleting a line, and runs the tests against each changed version. If a test fails, the change is said to be "killed." If every test still passes, the suite has a blind spot. The technique dates to the 1970s {ref:jia} and is now used in industry at Google and Meta {ref:petrovic, beller}.

The mapping to LLM judges is direct. The judge plays the role of the test suite, a known-good answer plays the correct program, and a scripted edit plays the mutant. A breaking edit that the judge does not penalize is a surviving mutant. MTLJ adds a second kind of mutant that classic mutation testing does not have: edits that should not change the outcome at all. A judge that penalizes those is producing a false positive.

Mutation testing also has a well-known weak spot, the equivalent mutant: a change that looks different but does not actually change behavior, so nothing could ever catch it. In natural language this risk is larger. If a swapped name lands in a sentence that the source never mentioned, the answer is not actually unfaithful, and a judge that ignores the swap is correct. The operator rules in Section 8.3 and the human review in Section 12 exist to control this risk.

## 3.2 LLM-as-a-Judge

Using a strong model to grade another model's output became common after studies showed that model judgments agree reasonably well with human preferences {ref:zheng}. The same studies found consistent biases toward the first answer shown, toward longer answers, and toward answers from the same model family {ref:wang, panickssery}. Approaches such as G-Eval {ref:geval} try to reduce variance with structured rubrics. Most of this research covers pairwise comparisons. MTLJ focuses on single-answer, claim-based faithfulness scoring, which is what most teams use as an automatic gate.

## 3.3 Related Tool: muteval

During research the team found muteval {ref:muteval}, an open-source package that also applies mutation testing to LLM evaluation. The team installed it, ran its demonstration, and read its documentation. {tab:muteval} summarizes how the two tools differ.

Table: Comparison of muteval and MTLJ {#muteval}
| Aspect | muteval | MTLJ |
|---|---|---|
| What gets changed | Prompts, retrieved context, tool outputs, and the model (22 operators) | Only the answer text |
| Needs to rerun the application | Yes | No, the edited text is scored directly |
| Changes that do nothing | Common: 6 of 13 in the demonstration had no effect | Rare: every degrading edit changes the text, and no-op edits are excluded and counted |
| Harmless edits | Generated but not scored | Scored as spurious sensitivity |
| Noise handling | Not measured | Noise floor measured per run; thresholds derived from it |

The most useful part of muteval's documentation is a limitation its authors call fundamental: checks without labels can measure coverage and consistency, but they cannot say whether an evaluation is correct. MTLJ works around this by creating labels instead of collecting them. Those labels only say which direction a score should move, not by how much, so MTLJ measures directional correctness rather than calibration. That makes the two tools complementary.

# 4. Proposed Solution

This section describes the solution at a high level, before any discussion of tools. Sections 7 through 17 then cover the design in detail.

## 4.1 Concept

MTLJ treats a judge like a piece of software under test. It takes answers that are known to be good, makes controlled edits whose correct effect is known in advance, asks the judge to score both versions several times, and measures whether the judge reacted the way it should. The result is a profile of the judge: which kinds of mistakes it catches, which it misses, which harmless changes it wrongly reacts to, and at what strength of edit it starts to slip.

The design rests on three ideas:

- **Script-made ground truth.** Every edit is made by deterministic code with a fixed random seed. The operator declares in advance whether the score should go down or stay the same. No language model writes edits, filler text, or expected labels, because a model-made label would bring back the trust problem the project is meant to remove.
- **Paired, repeated measurement.** Each edited answer is compared only with its own original, scored by the same judge with the same settings the same number of times. This removes differences between records and exposes the judge's own noise.
- **Thresholds from the judge's own noise.** Instead of a fixed cutoff, a change only counts if it is larger than what the judge's measured run-to-run variation would produce by chance. Two judges with different noise levels can then be compared fairly.

## 4.2 System Pipeline

{fig:pipeline_a} and {fig:pipeline_b} show the full pipeline as an activity diagram, from the source corpus to the report a user reads. It has four stages.

![System pipeline, part 1: data preparation and mutation.](diagrams/act_pipeline_a.png){width=4.6 #pipeline_a}

**Data preparation** happens once per corpus version. The RAGTruth question-answering records are loaded and filtered to answers that human annotators marked as free of hallucinations. They are converted to the standard record format and frozen with a checksum, so results from different months remain comparable.

**Mutation** reads the operator battery (which operators to run and at which intensities) and a base seed. Each operator is applied to each record at each intensity with a seed derived from the base seed, the record, the operator, and the intensity, so any single mutant can be regenerated on its own. An edit that changes nothing is logged as a skip with its reason. An edit that changes no more than the same record's edit at a lower intensity is dropped as a repeated dose, so a higher intensity always means a bigger change.

![System pipeline, part 2: execution and analysis.](diagrams/act_pipeline_b.png){width=4.6 #pipeline_b}

**Execution** wraps the judge in its adapter and scores every original and every mutant N times (five by default). Calls run concurrently. Each score is written to disk the moment it returns. Transient failures such as rate limits are retried with exponential backoff. Scores that were already obtained are answered from a content-addressed cache instead of calling the judge again.

**Analysis** pools the variance of the originals' repeated scores into a noise floor, pairs each mutant with its original, and decides for each mutant whether the judge reacted beyond noise. It then computes the detection rate and spurious sensitivity per operator and intensity, with Wilson and bootstrap intervals. It applies a Benjamini-Hochberg correction across all tests in the run, fits a dose-response curve for operators run at several strengths, and writes a versioned `results.json` file. The same file feeds the printed summary, the planned dashboard, and any CI gate.

## 4.3 Objectives

The solution is built to meet five objectives, each of which can be checked:

1. **Trustworthy ground truth.** Seeded, reproducible operators whose expected effect is known in advance, confirmed by a human review of at least 100 edits with two reviewers each.
2. **Any judge.** One adapter interface that any LLM-based scoring function can implement, with DeepEval faithfulness and plain Python functions supported first.
3. **Measurement instead of guesswork.** Detection rate and spurious sensitivity reported relative to each judge's own noise, with an interval and a corrected significance test on every number.
4. **Cheap audits.** Free local models through Ollama, results saved as they arrive, cached reruns, and recovery from crashes.
5. **A real result.** An audit of at least two published open-source judges, written up with where they fail.

## 4.4 How Teams Would Use It

The design is built around four real decisions that teams make about their judges:

- **Choosing a judge.** Run the same battery of edits against each candidate and compare how they do on the mistakes that matter in the team's domain.
- **Switching the judge's model.** Run the battery against an expensive hosted model and a cheaper local one to see exactly what the switch costs in accuracy.
- **Editing a custom judge.** Rerun the battery before and after every prompt change, which works like a regression test for the prompt.
- **Investigating a miss.** When a judge passes a bad output in production, run the matching kind of edit to see whether the miss was a one-off or a pattern.

## 4.5 A Typical Session

A developer installs the package and either picks a built-in adapter or writes a short one that exposes the team's judge as a Python function. The developer picks a dataset (the bundled 20-record gold set, the full corpus, or the team's own records in RAGTruth format), chooses the operators, intensities, and sample count in a small configuration file, and runs `mtlj audit run --config audit.toml`. The tool checks every setting before making any judge call and asks for confirmation before any run larger than 1,000 scorings. It then generates the edits, scores everything, saves every score, and prints a summary of each rate with its interval. In Senior Design II the developer will also be able to open the dashboard to see the full profile, compare two judges, and read the specific answers the judge got wrong.

## 4.6 Out of Scope

MTLJ does not check whether the original model's answer is correct, only whether the judge reacts correctly to changes. It only edits the answer, never the prompt, the context, or the model. It does not measure how well calibrated a judge's numbers are, since it only knows which direction a score should move. It does not support judges that compare two answers against each other. It is an offline development and CI tool, not a production monitor. Anything that scores text deterministically, such as a regular expression, a schema check, or a latency limit, can already be tested normally and is outside the project's scope.
